from . import models
from . import controllers

import odoo.addons.payment as payment


def post_init_hook(env):
    """Prepare the native payment method and disabled providers per company."""
    payment.setup_provider(env, "culqilink")


def uninstall_hook(env):
    """Use Odoo's safeguards when removing a provider with accounting history."""
    payment.reset_payment_provider(env, "culqilink")
