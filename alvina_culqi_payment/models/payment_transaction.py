from odoo import _, fields, models
from odoo.exceptions import ValidationError
from ..services.contract import ContractError, LinkRequest, minor_units


class PaymentTransaction(models.Model):
    _inherit = "payment.transaction"

    culqi_link_url = fields.Char(readonly=True, copy=False, groups="base.group_user")

    def _culqi_expected(self):
        self.ensure_one()
        return LinkRequest(self.reference, self.company_id.id,
                           minor_units(self.amount, self.currency_id.decimal_places),
                           self.currency_id.name)

    def _culqi_refresh_verified(self):
        """Private API: re-fetch using this provider's credential, under a row lock.

        Not called by the public controller until authenticity is verified.
        Nothing in a webhook body is passed to _process or _set_done.
        """
        self.ensure_one()
        if self.provider_code != "culqilink":
            raise ValidationError(_("Not a CulqiLink transaction."))
        self.env.cr.execute("SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE", [self.id])
        self.invalidate_recordset()
        if self.company_id != self.provider_id.company_id:
            raise ValidationError(_("Company mismatch."))
        payment = self.provider_id._culqi_adapter().retrieve_payment(self.provider_reference)
        try:
            payment.validate(self._culqi_expected(), self.provider_reference)
        except ContractError as exc:
            raise ValidationError(_("Culqi payment validation failed.")) from exc
        # Confirmed payments are immutable. A verified success can recover a previous
        # failed attempt, matching the native Odoo error-to-done transition.
        if self.state in ("done", "cancel"):
            return
        if payment.state == "paid":
            self._set_done()
            # account_payment creates the payment and reconciles through standard Odoo logic.
            self._post_process()
        elif payment.state == "failed":
            self._set_error(_("Payment rejected by provider."))
        elif payment.state == "cancelled":
            self._set_canceled()
        else:
            self._set_pending()

    def _post_process(self):
        """Serialize Culqi accounting from callbacks, manual actions and native cron.

        Apply only to this provider; preserve the native behavior for all others.
        A fresh cache under the row lock prevents a stale payment_id from creating
        another account.payment after another worker has already posted it.
        """
        culqi = self.filtered(lambda tx: tx.provider_code == "culqilink")
        other = self - culqi
        if other:
            super(PaymentTransaction, other)._post_process()
        for tx in culqi.sorted("id"):
            self.env.cr.execute(
                "SELECT id FROM payment_transaction WHERE id = %s FOR UPDATE", [tx.id])
            tx.invalidate_recordset()
            if not tx.is_post_processed:
                super(PaymentTransaction, tx)._post_process()
