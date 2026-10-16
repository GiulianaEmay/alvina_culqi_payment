from odoo import http
from odoo.http import request


class CulqiWebhook(http.Controller):
    @http.route("/payment/culqilink/webhook", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def webhook(self, **kwargs):
        # Intentionally do not read/store body, trust references, sudo or alter records.
        # Replace only after confirming Culqi's documented authenticity protocol.
        return request.make_response(
            "Webhook verification is not configured", status=503,
            headers=[("Content-Type", "text/plain"), ("Cache-Control", "no-store")])
