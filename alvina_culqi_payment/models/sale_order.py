from uuid import uuid4

from markupsafe import Markup

from odoo import _, Command, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.contract import ContractError, HostedLink, minor_units
from ..services.culqi import CulqiLinkClient


class SaleOrder(models.Model):
    _inherit = "sale.order"

    culqi_transaction_id = fields.Many2one(
        "payment.transaction", readonly=True, copy=False, check_company=True,
        groups="base.group_system")
    culqi_request_ids = fields.One2many(
        "alvina.culqi.link.request", "sale_order_id", readonly=True,
        groups="sales_team.group_sale_salesman")
    culqi_request_id = fields.Many2one(
        "alvina.culqi.link.request", compute="_compute_culqi_request",
        groups="sales_team.group_sale_salesman")
    culqi_request_state = fields.Selection([
        ("queued", "Queued"), ("requesting", "Request started"),
        ("created", "Link created"), ("uncertain", "Reconciliation required"),
        ("blocked", "Blocked before request"),
    ], compute="_compute_culqi_request", groups="sales_team.group_sale_salesman")
    culqi_link_url = fields.Char(
        compute="_compute_culqi_request", readonly=True,
        groups="sales_team.group_sale_salesman")

    @api.depends("culqi_request_ids.state", "culqi_request_ids.link_url")
    def _compute_culqi_request(self):
        requests = self.env["alvina.culqi.link.request"].search([
            ("sale_order_id", "in", self.ids),
        ])
        by_order = {request.sale_order_id.id: request for request in requests}
        for order in self:
            request = by_order.get(order.id)
            order.culqi_request_id = request or False
            order.culqi_request_state = request.state if request else False
            order.culqi_link_url = request.link_url if request else False

    def write(self, values):
        # readonly in a view does not protect RPC writes to a stored field.
        if "culqi_transaction_id" in values and not self.env.su:
            raise AccessError(_("The Culqi transaction is managed by the connector."))
        return super().write(values)

    def _culqi_check_sales_access(self, operation):
        self.ensure_one()
        if not self.env.user.has_group("sales_team.group_sale_salesman"):
            raise AccessError(_("Sales access is required."))
        self.check_access(operation)
        if self.company_id not in self.env.companies:
            raise AccessError(_("Select an order in an allowed company."))

    def _culqi_payable_amount(self, own_transaction=None):
        """Conservative server-side balance for the supported uninvoiced flow."""
        self.ensure_one()
        if self.state != "sale":
            raise UserError(_("Select a confirmed order."))
        if self.partner_id.company_id and self.partner_id.company_id != self.company_id:
            raise UserError(_("The customer must belong to the order company."))
        if not self.partner_id.email:
            raise UserError(_("The customer must have an email address."))
        if self.invoice_ids:
            raise UserError(_("Invoiced orders require the verified invoice balance workflow."))
        # Only inspect transactions derived from this previously authorized order.
        transactions = self.sudo().transaction_ids
        if own_transaction:
            transactions -= own_transaction
        if any(tx.state in ("draft", "pending", "authorized", "done") for tx in transactions):
            raise UserError(_("An existing payment must be resolved before generating a link."))
        if self.currency_id.name not in ("PEN", "USD") or self.currency_id.decimal_places != 2:
            raise UserError(_("CulqiLink requires PEN or USD with two decimal places."))
        try:
            amount_minor = minor_units(self.currency_id.round(self.amount_total), 2)
        except ContractError:
            raise UserError(_("The pending amount must be positive.")) from None
        if not 600 <= amount_minor <= 999900:
            raise UserError(_("The CulqiLink amount must be between 6.00 and 9,999.00."))
        return amount_minor

    def _culqi_prepare_link_request(self):
        """Queue locally. The request transaction never performs a remote POST."""
        self.ensure_one()
        self._culqi_check_sales_access("write")
        self.env.cr.execute("SELECT id FROM sale_order WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        self._culqi_check_sales_access("write")
        request_model = self.env["alvina.culqi.link.request"].sudo()
        if request_model.search_count([("sale_order_id", "=", self.id)]):
            raise UserError(_("A Culqi request already exists. Reconcile it before any new charge."))
        amount_minor = self._culqi_payable_amount()
        provider = self.env["payment.provider"].sudo().search([
            ("code", "=", "culqilink"), ("company_id", "=", self.company_id.id),
            ("state", "in", ["test", "enabled"]),
        ])
        if len(provider) != 1:
            raise UserError(_("Exactly one CulqiLink provider is required for this company."))
        provider._culqi_assert_release_ready()
        if not provider.journal_id or provider.journal_id.company_id != self.company_id:
            raise UserError(_("A payment journal in the order company is required."))
        method = provider.payment_method_ids[:1]
        if not method:
            raise UserError(_("A payment method is required."))
        reference = "ALV-" + uuid4().hex
        transaction = self.env["payment.transaction"].sudo().with_company(self.company_id).create({
            "provider_id": provider.id, "payment_method_id": method.id,
            "reference": reference, "amount": amount_minor / 100,
            "currency_id": self.currency_id.id, "partner_id": self.partner_id.id,
            "operation": "online_redirect", "sale_order_ids": [Command.set(self.ids)],
        })
        request = request_model.with_company(self.company_id).create({
            "sale_order_id": self.id, "company_id": self.company_id.id,
            "provider_id": provider.id, "transaction_id": transaction.id,
            "reference": reference, "amount": amount_minor / 100,
            "currency_id": self.currency_id.id, "requested_by_id": self.env.user.id,
        })
        self.sudo().write({"culqi_transaction_id": transaction.id})
        self.invalidate_recordset(["culqi_request_id", "culqi_request_state", "culqi_link_url"])
        return request

    def action_generate_culqi_link(self):
        self._culqi_prepare_link_request()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("CulqiLink"), "type": "info",
            "message": _("Link request queued. Refresh the order to check its result."),
        }}

    def _culqi_shareable_link(self):
        self.ensure_one()
        self._culqi_check_sales_access("read")
        request = self.env["alvina.culqi.link.request"].search([
            ("sale_order_id", "=", self.id), ("state", "=", "created"),
        ], limit=1)
        if not request:
            raise UserError(_("No verified hosted link is available for this order."))
        try:
            return HostedLink(request.remote_id, request.link_url).validate(
                CulqiLinkClient.allowed_link_hosts).url
        except (ContractError, ValueError):
            raise UserError(_("The hosted link could not be validated.")) from None

    def action_open_culqi_link(self):
        return {"type": "ir.actions.act_url", "url": self._culqi_shareable_link(), "target": "new"}

    def action_send_culqi_link(self):
        url = self._culqi_shareable_link()
        if not self.partner_id.email:
            raise UserError(_("The customer must have an email address."))
        body = Markup('<p>%s</p><p><a href="%s">%s</a></p>') % (
            _("Payment link for order %s", self.name), url, _("Open secure payment link"))
        return {
            "type": "ir.actions.act_window", "res_model": "mail.compose.message",
            "view_mode": "form", "target": "new", "context": {
                "default_model": "sale.order", "default_res_ids": self.ids,
                "default_composition_mode": "comment", "default_partner_ids": self.partner_id.ids,
                "default_subject": _("Payment link for order %s", self.name),
                "default_body": str(body),
            },
        }
