"""Durable outbox: persist the attempt before the non-idempotent remote POST.

Only the dedicated cron worker commits, through the Odoo 19 progress API.
An interrupted or ambiguous attempt is never automatically submitted again.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.contract import ContractError, minor_units


class CulqiLinkRequest(models.Model):
    _name = "alvina.culqi.link.request"
    _description = "Culqi hosted-link request"
    _rec_name = "reference"
    _order = "id"
    _check_company_auto = True

    sale_order_id = fields.Many2one("sale.order", required=True, index=True, readonly=True,
                                    check_company=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True,
                                ondelete="restrict")
    provider_id = fields.Many2one("payment.provider", required=True, readonly=True,
                                 check_company=True, ondelete="restrict", groups="base.group_system")
    transaction_id = fields.Many2one("payment.transaction", required=True, readonly=True,
                                    check_company=True, ondelete="restrict", groups="base.group_system")
    reference = fields.Char(required=True, readonly=True, copy=False)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True, ondelete="restrict")
    amount = fields.Monetary(required=True, readonly=True)
    requested_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    requested_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    attempted_at = fields.Datetime(readonly=True, copy=False)
    state = fields.Selection([
        ("queued", "Queued"), ("requesting", "Request started"),
        ("created", "Link created"), ("uncertain", "Reconciliation required"),
        ("blocked", "Blocked before request"),
    ], default="queued", required=True, readonly=True, index=True, copy=False)
    remote_id = fields.Char(readonly=True, copy=False)
    link_url = fields.Char(readonly=True, copy=False)
    failure_code = fields.Selection([
        ("preflight", "Local validation failed; no remote request"),
        ("interrupted", "Previous attempt interrupted; reconciliation required"),
        ("remote_uncertain", "Remote result uncertain; reconciliation required"),
    ], readonly=True, copy=False)

    _sale_order_unique = models.Constraint(
        "unique(sale_order_id)", "Only one Culqi request per order is supported; reconcile it first.")
    _reference_unique = models.Constraint("unique(reference)", "The Culqi reference must be unique.")
    _transaction_unique = models.Constraint(
        "unique(transaction_id)", "A transaction can have only one Culqi request.")

    def write(self, values):
        immutable = {
            "sale_order_id", "company_id", "provider_id", "transaction_id", "reference",
            "currency_id", "amount", "requested_by_id", "requested_at",
        }
        if immutable.intersection(values):
            raise ValidationError(_("The original Culqi request snapshot cannot be changed."))
        if "attempted_at" in values and any(request.attempted_at for request in self):
            raise ValidationError(_("A Culqi attempt cannot be cleared or replaced."))
        if values.get("state") == "queued" and any(
                request.state != "queued" or request.attempted_at for request in self):
            raise ValidationError(_("A Culqi request cannot be automatically submitted again."))
        return super().write(values)

    def unlink(self):
        if any(request.attempted_at or request.state in ("requesting", "created", "uncertain")
               for request in self):
            raise UserError(_("Attempted Culqi requests must be retained for reconciliation."))
        return super().unlink()

    def _culqi_preflight(self):
        self.ensure_one()
        if self.state != "queued" or self.attempted_at:
            raise ValidationError(_("Only an unattempted request may be sent."))
        user = self.requested_by_id
        if not user.active or self.company_id not in user.company_ids:
            raise ValidationError(_("The requesting user no longer has company access."))
        order = self.sale_order_id.with_user(user).with_context(
            allowed_company_ids=[self.company_id.id])
        order._culqi_check_sales_access("write")
        transaction = self.transaction_id
        provider = self.provider_id
        if (order.company_id != self.company_id or transaction.company_id != self.company_id
                or provider.company_id != self.company_id or transaction.provider_id != provider
                or provider.code != "culqilink" or provider.state not in ("test", "enabled")
                or transaction.state != "draft"
                or transaction.sale_order_ids != order.sudo()
                or transaction.partner_id != order.partner_id
                or transaction.reference != self.reference
                or transaction.currency_id != self.currency_id
                or order.currency_id != self.currency_id):
            raise ValidationError(_("The order, company or transaction changed after queueing."))
        expected = transaction._culqi_expected()
        try:
            amount_minor = minor_units(self.amount, self.currency_id.decimal_places)
        except ContractError:
            raise ValidationError(_("The request amount is invalid.")) from None
        if (expected.amount_minor != amount_minor
                or order._culqi_payable_amount(own_transaction=transaction) != amount_minor):
            raise ValidationError(_("The pending amount changed after queueing."))
        provider._culqi_assert_release_ready()
        if not provider.journal_id or provider.journal_id.company_id != self.company_id:
            raise ValidationError(_("The payment journal no longer matches the company."))
        return provider._culqi_adapter(), expected

    @api.model
    def _cron_create_links(self, limit=10):
        """Run only via ir.cron, never from an HTTP request or the queue action."""
        context = self.env.context
        assert context.get("cron_id") and context.get("ir_cron_progress_id"), "Cron context required"
        progress = self.env["ir.cron.progress"].sudo().browse(context["ir_cron_progress_id"])
        assert progress.exists() and progress.cron_id.id == context["cron_id"], "Invalid cron progress"
        configured_cron = self.env.ref("alvina_culqi_payment.ir_cron_culqi_link_requests")
        assert configured_cron.id == context["cron_id"], "Dedicated Culqi cron required"
        cron = self.env["ir.cron"]
        requests = self.sudo()
        # The scheduler serializes this cron. Anything still requesting came from
        # a previous worker interrupted after its durable attempt marker.
        interrupted = requests.search([("state", "=", "requesting")]).try_lock_for_update()
        if interrupted:
            interrupted.write({"state": "uncertain", "failure_code": "interrupted"})
        remaining = requests.search_count([("state", "=", "queued")])
        if not cron._commit_progress(remaining=remaining):
            return
        for request in requests.search([("state", "=", "queued")], limit=max(1, min(limit, 50))):
            request = request.try_lock_for_update().filtered_domain([("state", "=", "queued")])
            if not request:
                continue
            request.invalidate_recordset()
            if request.attempted_at:
                request.write({"state": "uncertain", "failure_code": "interrupted"})
                if not cron._commit_progress(1):
                    return
                continue
            try:
                with self.env.cr.savepoint():
                    adapter, expected = request._culqi_preflight()
            except Exception:
                # No remote call has taken place. Never retain exception bodies:
                # adapters may contain a credential or upstream customer data.
                request.write({"state": "blocked", "failure_code": "preflight"})
                if not cron._commit_progress(1):
                    return
                continue
            request.write({"state": "requesting", "attempted_at": fields.Datetime.now()})
            # Persist BEFORE POST. Odoo retries, crashes or schema errors must
            # leave a durable marker which can never become queued again.
            if not cron._commit_progress():
                return
            try:
                with self.env.cr.savepoint():
                    link = adapter.create_link(expected)
                    link.validate(adapter.allowed_link_hosts)
                    request.transaction_id.write({
                        "provider_reference": link.remote_id, "culqi_link_url": link.url,
                    })
                    request.transaction_id._set_pending()
                    request.write({
                        "state": "created", "remote_id": link.remote_id,
                        "link_url": link.url, "failure_code": False,
                    })
            except Exception:
                request.write({"state": "uncertain", "failure_code": "remote_uncertain"})
            if not cron._commit_progress(1):
                return
