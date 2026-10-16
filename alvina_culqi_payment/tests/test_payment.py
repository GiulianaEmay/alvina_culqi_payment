from dataclasses import replace
from unittest.mock import Mock, patch
from psycopg2 import IntegrityError
from odoo.tools import mute_logger
from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.addons.account_payment.tests.common import AccountPaymentCommon
from ..services.contract import VerifiedPayment


@tagged("post_install", "-at_install")
class TestCulqiPayment(AccountPaymentCommon):
    """Requires a disposable Odoo 19 DB. No live requests or production credentials."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.culqi = cls.env["payment.provider"].create({
            "name": "Culqi test fixture", "code": "culqilink", "state": "disabled",
            "company_id": cls.env.company.id,
            "payment_method_ids": [Command.set(cls.payment_method.ids)],
        })
        cls.culqi.journal_id = cls.company_data["default_journal_bank"]
        cls.culqi.journal_id.inbound_payment_method_line_ids.filtered(
            lambda line: line.payment_provider_id == cls.culqi
        ).payment_account_id = cls.inbound_payment_method_line.payment_account_id

    def _tx(self):
        return self.env["payment.transaction"].create({
            "provider_id": self.culqi.id, "payment_method_id": self.payment_method.id,
            "reference": "CULQI-TEST", "provider_reference": "remote-1",
            "amount": 12.50, "currency_id": self.currency_usd.id,
            "partner_id": self.partner.id, "operation": "online_redirect",
        })

    def _snapshot(self, tx, state="paid"):
        return VerifiedPayment("remote-1", tx.reference, tx.company_id.id, 1250, "USD", state)

    def test_release_gate(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.culqi.state = "test"

    def test_duplicate_confirmation_calls_postprocess_once(self):
        tx = self._tx()
        adapter = Mock()
        adapter.retrieve_payment.return_value = self._snapshot(tx)
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter), \
                patch.object(type(tx), "_post_process") as postprocess:
            tx._culqi_refresh_verified()
            tx._culqi_refresh_verified()
        self.assertEqual(tx.state, "done")
        postprocess.assert_called_once()

    def test_amount_and_company_mismatch(self):
        tx = self._tx()
        for changes in ({"amount_minor": 100}, {"company_id": tx.company_id.id + 100}, {"currency": "PEN"}):
            adapter = Mock()
            adapter.retrieve_payment.return_value = replace(self._snapshot(tx), **changes)
            with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter), \
                    self.assertRaises(ValidationError):
                tx._culqi_refresh_verified()
            self.assertEqual(tx.state, "draft")
            self.assertFalse(tx.payment_id)

    def test_rejected_payment(self):
        tx = self._tx()
        adapter = Mock()
        adapter.retrieve_payment.return_value = self._snapshot(tx, "failed")
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter):
            tx._culqi_refresh_verified()
        self.assertEqual(tx.state, "error")
        self.assertFalse(tx.payment_id)

    def test_timeout_and_credentials(self):
        tx = self._tx()
        for failure in (TimeoutError(), PermissionError()):
            adapter = Mock()
            adapter.retrieve_payment.side_effect = failure
            with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter), \
                    self.assertRaises(type(failure)):
                tx._culqi_refresh_verified()
            self.assertEqual(tx.state, "draft")
            self.assertFalse(tx.payment_id)

    def test_native_payment_is_posted_once(self):
        tx = self._tx()
        adapter = Mock()
        adapter.retrieve_payment.return_value = self._snapshot(tx)
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter):
            tx._culqi_refresh_verified()
            payment_id = tx.payment_id.id
            tx._culqi_refresh_verified()
            tx._post_process()  # Same entry point used by the native cron/manual action.
        self.assertTrue(payment_id)
        self.assertEqual(tx.payment_id.id, payment_id)
        payments = self.env["account.payment"].search([("payment_transaction_id", "=", tx.id)])
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments.move_id.state, "posted")
        self.assertEqual(payments.amount, 12.50)
        self.assertEqual(payments.company_id, tx.company_id)

    def test_invoice_reconciled_by_native_accounting(self):
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.partner.id,
            "journal_id": self.company_data["default_journal_sale"].id,
            "currency_id": self.currency_usd.id,
            "invoice_line_ids": [Command.create({
                "name": "Culqi mock payment", "quantity": 1, "price_unit": 12.50,
                "account_id": self.company_data["default_account_revenue"].id,
                "tax_ids": [Command.clear()],
            })],
        })
        invoice.action_post()
        tx = self._tx()
        tx.invoice_ids = [Command.set(invoice.ids)]
        adapter = Mock()
        adapter.retrieve_payment.return_value = self._snapshot(tx)
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter):
            tx._culqi_refresh_verified()
            tx._post_process()
        self.assertEqual(invoice.amount_residual, 0)
        self.assertTrue(invoice.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable").reconciled)
        self.assertEqual(tx.payment_id.move_id.state, "posted")

    def test_binding_cannot_be_shared_between_companies(self):
        self.culqi.culqi_credential_env = "CULQI_COMPANY_ONE_TEST"
        other_company = self.env["res.company"].create({"name": "Separate Culqi company"})
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.env["payment.provider"].create({
                "name": "Other provider", "code": "culqilink", "state": "disabled",
                "company_id": other_company.id, "culqi_credential_env": "CULQI_COMPANY_ONE_TEST",
            })

    def test_release_gate_also_blocks_background_work(self):
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.culqi._culqi_assert_release_ready()

    def test_verified_success_can_recover_error(self):
        tx = self._tx()
        tx._set_error("Mock earlier rejection")
        adapter = Mock()
        adapter.retrieve_payment.return_value = self._snapshot(tx)
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter):
            tx._culqi_refresh_verified()
        self.assertEqual(tx.state, "done")
        self.assertTrue(tx.payment_id)
        self.assertEqual(tx.payment_id.move_id.state, "posted")
