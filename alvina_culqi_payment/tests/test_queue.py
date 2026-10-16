"""Durable generation uses mocked Culqi; accounting is covered separately."""
from unittest.mock import Mock, patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.addons.account_payment.tests.common import AccountPaymentCommon

from ..services.contract import HostedLink
from ..services.culqi import CulqiAPIError


@tagged("post_install", "-at_install")
class TestCulqiQueue(AccountPaymentCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sales_user = new_test_user(
            cls.env, login="culqi_queue_sales", groups="sales_team.group_sale_salesman",
            company_id=cls.env.company.id, company_ids=[Command.set(cls.env.company.ids)],
        )
        cls.partner.email = "culqi-test@example.test"
        cls.culqi = cls.env["payment.provider"].create({
            "name": "Culqi queue fixture", "code": "culqilink", "state": "disabled",
            "company_id": cls.env.company.id,
            "payment_method_ids": [Command.set(cls.payment_method.ids)],
        })
        cls.culqi.journal_id = cls.company_data["default_journal_bank"]
        # Test-only fixture bypass: no provider can be enabled through the product.
        # Every remote call below is mocked; do not reproduce this in an instance.
        cls.env.cr.execute("UPDATE payment_provider SET state = 'test' WHERE id = %s", [cls.culqi.id])
        cls.culqi.invalidate_recordset()

    def _order(self, amount=12.50):
        return self.env["sale.order"].sudo().create({
            "partner_id": self.partner.id, "user_id": self.sales_user.id,
            "company_id": self.env.company.id, "currency_id": self.currency_usd.id,
            "state": "sale",
            "order_line": [Command.create({
                "product_id": self.product_a.id, "name": "Culqi request fixture",
                "product_uom_qty": 1, "price_unit": amount, "tax_ids": [Command.clear()],
            })],
        })

    def _queue(self, order):
        with patch.object(type(self.culqi), "_culqi_assert_release_ready", return_value=None):
            return order.with_user(self.sales_user)._culqi_prepare_link_request()

    def _run_worker(self, adapter):
        cron = self.env.ref("alvina_culqi_payment.ir_cron_culqi_link_requests")
        progress = self.env["ir.cron.progress"].sudo().create({"cron_id": cron.id})
        worker = self.env["alvina.culqi.link.request"].sudo().with_context(
            cron_id=cron.id, ir_cron_progress_id=progress.id)
        # Keep the test's rollback boundary; production uses the real committing API.
        with patch.object(type(self.culqi), "_culqi_assert_release_ready", return_value=None), \
                patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter), \
                patch.object(type(cron), "_commit_progress", return_value=60):
            worker._cron_create_links()

    def test_sales_user_queues_without_payment_model_write_access(self):
        order = self._order()
        adapter = Mock()
        with patch.object(type(self.culqi), "_culqi_adapter", return_value=adapter):
            request = self._queue(order)
        self.assertEqual(request.state, "queued")
        self.assertEqual(request.amount, order.amount_total)
        self.assertEqual(request.requested_by_id, self.sales_user)
        adapter.create_link.assert_not_called()
        with self.assertRaises(AccessError):
            request.with_user(self.sales_user).write({"state": "created"})

    def test_valid_link_saved_and_shareable(self):
        order = self._order()
        request = self._queue(order)
        adapter = Mock(allowed_link_hosts={"express.culqi.com"})
        adapter.create_link.return_value = HostedLink("link_test_abcdefghijklmnop", "https://express.culqi.com/pago/TEST")
        self._run_worker(adapter)
        self.assertEqual(request.state, "created")
        self.assertEqual(request.transaction_id.state, "pending")
        self.assertTrue(request.attempted_at)
        expected = adapter.create_link.call_args.args[0]
        self.assertEqual(expected.amount_minor, 1250)
        self.assertEqual(expected.company_id, order.company_id.id)
        action = order.with_user(self.sales_user).action_open_culqi_link()
        self.assertEqual(action["url"], request.link_url)
        composer = order.with_user(self.sales_user).action_send_culqi_link()
        self.assertEqual(composer["res_model"], "mail.compose.message")
        self.assertIn(request.link_url, composer["context"]["default_body"])
        self._run_worker(adapter)
        adapter.create_link.assert_called_once()

    def test_zero_and_negative_amounts_rejected(self):
        for amount in (0, -12.50):
            with self.subTest(amount=amount), self.assertRaises(UserError):
                self._queue(self._order(amount))

    def test_second_request_for_same_order_rejected(self):
        order = self._order()
        self._queue(order)
        with self.assertRaises(UserError):
            self._queue(order)
        self.assertEqual(self.env["alvina.culqi.link.request"].sudo().search_count([
            ("sale_order_id", "=", order.id)]), 1)

    def test_timeout_is_uncertain_without_retry(self):
        request = self._queue(self._order())
        adapter = Mock()
        adapter.create_link.side_effect = CulqiAPIError("mock timeout")
        self._run_worker(adapter)
        self.assertEqual(request.state, "uncertain")
        self.assertEqual(request.failure_code, "remote_uncertain")
        self.assertTrue(request.attempted_at)
        self._run_worker(adapter)
        adapter.create_link.assert_called_once()
        with self.assertRaises(ValidationError):
            request.write({"state": "queued"})
        with self.assertRaises(UserError):
            request.unlink()

    def test_interrupted_attempt_is_never_resubmitted(self):
        request = self._queue(self._order())
        request.write({"state": "requesting", "attempted_at": fields.Datetime.now()})
        adapter = Mock()
        self._run_worker(adapter)
        self.assertEqual(request.state, "uncertain")
        self.assertEqual(request.failure_code, "interrupted")
        adapter.create_link.assert_not_called()

    def test_changed_balance_blocks_before_post(self):
        order = self._order()
        request = self._queue(order)
        order.order_line.price_unit = 13.50
        adapter = Mock()
        self._run_worker(adapter)
        self.assertEqual(request.state, "blocked")
        self.assertFalse(request.attempted_at)
        adapter.create_link.assert_not_called()

    def test_worker_cannot_run_in_http_context(self):
        with self.assertRaises(AssertionError):
            self.env["alvina.culqi.link.request"]._cron_create_links()
