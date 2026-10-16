import os
import re
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from ..services.contract import AdapterUnavailable
from ..services.culqi import CulqiAPIError, CulqiLinkClient


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(selection_add=[("culqilink", "CulqiLink")],
                            ondelete={"culqilink": "set default"})
    culqi_credential_env = fields.Char(
        string="Credential environment variable name", copy=False,
        groups="base.group_system",
        help="Name only; never enter a secret here. Separate binding per company.")

    _culqi_credential_binding_uniq = models.UniqueIndex(
        "(culqi_credential_env) WHERE code = 'culqilink' "
        "AND culqi_credential_env IS NOT NULL AND culqi_credential_env != ''",
        "A Culqi credential binding must belong to one provider and company only.",
    )

    @api.constrains("code", "culqi_credential_env")
    def _check_culqi_credential_binding(self):
        for provider in self.filtered(lambda p: p.code == "culqilink"):
            name = provider.culqi_credential_env
            if name and not re.fullmatch(r"CULQI_[A-Z0-9_]+", name):
                raise ValidationError(_(
                    "Enter only a dedicated CULQI_ environment variable name, never a secret."))

    @api.constrains("code", "state", "is_published")
    def _check_culqi_release_gate(self):
        for provider in self.filtered(lambda p: p.code == "culqilink"):
            if provider.state != "disabled" or provider.is_published:
                raise ValidationError(_(
                    "CulqiLink cannot be enabled until the official API and webhook "
                    "contract are verified and integration tests pass."))

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code == "culqilink":
            return {"card"}
        return super()._get_default_payment_method_codes()

    def _culqi_assert_release_ready(self):
        """Central gate for background jobs as well as interactive generation."""
        self.ensure_one()
        raise UserError(_(
            "CulqiLink is awaiting verified payment and webhook integration. "
            "No payment links can be created yet."))

    def _culqi_adapter(self):
        self.ensure_one()
        name = self.sudo().culqi_credential_env or ""
        if not re.fullmatch(r"CULQI_[A-Z0-9_]+", name):
            raise UserError(_("Use a dedicated CULQI_ credential binding name per company."))
        mode = "live" if self.state == "enabled" else "test"
        return CulqiLinkClient(os.environ.get(name), mode=mode)

    def action_culqi_check_connection(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Administrator access is required."))
        self.check_access("read")
        try:
            self._culqi_adapter().check_connection()
        except (AdapterUnavailable, CulqiAPIError) as exc:
            raise UserError(_("Culqi connection could not be verified. Check the secure binding and network access.")) from None
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Culqi"), "message": _("Connection verified."), "type": "success"}}
