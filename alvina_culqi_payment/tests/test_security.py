"""ORM permissions and real PostgreSQL concurrency; no Culqi network calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from psycopg2.errors import SerializationFailure

from odoo import Command, SUPERUSER_ID, api
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.orm.registry import DummyRLock, Registry


@tagged("post_install", "-at_install")
class TestCulqiSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Culqi restricted company"})
        cls.sales_user = new_test_user(
            cls.env, login="culqi_restricted_sales",
            groups="sales_team.group_sale_salesman",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.provider = cls.env["payment.provider"].create({
            "name": "Culqi permission fixture", "code": "culqilink", "state": "disabled",
            "company_id": cls.company.id, "culqi_credential_env": "CULQI_PERMISSIONS_TEST",
        })

    def test_sales_user_cannot_read_binding_or_check_credentials(self):
        provider = self.provider.with_user(self.sales_user)
        self.assertNotIn("culqi_credential_env", provider.fields_get(["culqi_credential_env"]))
        with self.assertRaises(AccessError):
            provider.read(["culqi_credential_env"])
        with self.assertRaises(UserError):
            provider.action_culqi_check_connection()

    def test_cross_company_order_cannot_queue_a_request(self):
        partner = self.env["res.partner"].create({
            "name": "Culqi isolated customer", "email": "culqi-isolated@example.test",
        })
        order = self.env["sale.order"].with_company(self.other_company).create({
            "partner_id": partner.id, "company_id": self.other_company.id,
            "user_id": self.sales_user.id, "state": "sale",
        })
        requests = self.env["alvina.culqi.link.request"]
        request_count = requests.search_count([])
        transaction_count = self.env["payment.transaction"].search_count([])
        forbidden_order = order.with_user(self.sales_user).with_context(
            allowed_company_ids=self.company.ids)
        # Remove only the development gate: ordinary ACL/record rules must reject
        # the request before any privileged transaction or queue creation.
        with patch.object(type(self.provider), "_culqi_assert_release_ready", return_value=None):
            with self.assertRaises(AccessError):
                forbidden_order.action_generate_culqi_link()
        self.assertEqual(requests.search_count([]), request_count)
        self.assertEqual(self.env["payment.transaction"].search_count([]), transaction_count)


@tagged("post_install", "-at_install")
class TestCulqiConcurrentAccounting(TransactionCase):
    """Two real cursors share committed, disposable records, never class fixtures.

    The cursors start from the same unpaid snapshot. PostgreSQL may serialize
    the losing transaction; retrying that transaction must see the one payment
    already created by the winner. No accounting method is patched.
    """

    def _create_committed_fixture(self):
        suffix = uuid4().hex[:10].upper()
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {"tracking_disable": True})
            company = env.ref("base.main_company")
            env = env(context=dict(env.context, allowed_company_ids=company.ids))
            accounts = env["account.account"].create([
                {"name": "Culqi concurrency bank", "code": "CCB" + suffix,
                 "account_type": "asset_cash", "company_ids": [Command.set(company.ids)]},
                {"name": "Culqi concurrency receivable", "code": "CCR" + suffix,
                 "account_type": "asset_receivable", "reconcile": True,
                 "company_ids": [Command.set(company.ids)]},
                {"name": "Culqi concurrency outstanding", "code": "CCO" + suffix,
                 "account_type": "asset_current", "reconcile": True,
                 "company_ids": [Command.set(company.ids)]},
            ])
            journal = env["account.journal"].create({
                "name": "Culqi concurrency " + suffix, "code": "C" + suffix[:4],
                "type": "bank", "company_id": company.id,
                "default_account_id": accounts[0].id, "suspense_account_id": accounts[2].id,
            })
            partner = env["res.partner"].create({
                "name": "Culqi concurrency " + suffix,
                "property_account_receivable_id": accounts[1].id,
            })
            method = env.ref("payment.payment_method_card")
            provider = env["payment.provider"].create({
                "name": "Culqi concurrency " + suffix,
                "code": "culqilink", "state": "disabled", "company_id": company.id,
                "payment_method_ids": [Command.set(method.ids)],
            })
            provider.journal_id = journal
            payment_line = journal.inbound_payment_method_line_ids.filtered(
                lambda line: line.payment_provider_id == provider)
            self.assertEqual(len(payment_line), 1, "Native provider setup must supply its accounting method")
            payment_line.payment_account_id = accounts[2]
            tx = env["payment.transaction"].create({
                "provider_id": provider.id, "payment_method_id": method.id,
                "reference": "CULQI-CONCURRENT-" + suffix,
                "provider_reference": "mock-concurrent-" + suffix,
                "amount": 12.50, "currency_id": company.currency_id.id,
                "partner_id": partner.id, "operation": "online_redirect",
            })
            # This is the test's verified-payment boundary. The test covers only
            # native accounting after confirmation, without enabling the provider.
            tx._set_done()
            env.flush_all()
            fixture = {
                "company_id": company.id, "tx_id": tx.id, "provider_id": provider.id,
                "journal_id": journal.id, "partner_id": partner.id, "account_ids": accounts.ids,
            }
            cr.commit()
        self.addCleanup(self._cleanup_committed_fixture, fixture)
        return fixture

    def _cleanup_committed_fixture(self, fixture):
        # Delete only IDs created above; shared company, native payment methods,
        # existing journal entries, and the test class transaction are untouched.
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {
                "allowed_company_ids": [fixture["company_id"]], "tracking_disable": True,
            })
            tx = env["payment.transaction"].browse(fixture["tx_id"]).exists()
            payments = env["account.payment"].search([("payment_transaction_id", "=", fixture["tx_id"])])
            payments.with_context(force_delete=True).unlink()
            tx.unlink()
            env["payment.provider"].browse(fixture["provider_id"]).unlink()
            env["account.journal"].browse(fixture["journal_id"]).unlink()
            env["res.partner"].browse(fixture["partner_id"]).unlink()
            env["account.account"].browse(fixture["account_ids"]).unlink()
            cr.commit()

    def test_two_cursors_create_one_posted_payment(self):
        fixture = self._create_committed_fixture()
        both_loaded = Barrier(2)

        def post_process_worker():
            for attempt in range(2):
                with self.registry.cursor() as cr:
                    cr.execute("SET LOCAL lock_timeout = '10s'")
                    cr.execute("SET LOCAL statement_timeout = '20s'")
                    env = api.Environment(cr, SUPERUSER_ID, {
                        "allowed_company_ids": [fixture["company_id"]],
                        "tracking_disable": True,
                    })
                    tx = env["payment.transaction"].browse(fixture["tx_id"])
                    if attempt == 0:
                        self.assertFalse(tx.payment_id)
                        self.assertFalse(tx.is_post_processed)
                        both_loaded.wait(timeout=10)
                    try:
                        tx._post_process()
                        env.flush_all()
                        payment_id = tx.payment_id.id
                        cr.commit()
                        return payment_id
                    except SerializationFailure:
                        cr.rollback()
                        if attempt:
                            raise
            self.fail("The accounting worker did not complete")

        # Registry initialization holds a process-wide lock during post-install
        # tests. Release only that test harness lock, as Odoo's official threaded
        # tests do; each worker retains a real independent PostgreSQL transaction.
        with patch.object(Registry, "_lock", DummyRLock()), ThreadPoolExecutor(max_workers=2) as executor:
            workers = [executor.submit(post_process_worker) for _ in range(2)]
            payment_ids = [worker.result(timeout=40) for worker in workers]
        self.assertTrue(all(payment_ids))
        self.assertEqual(payment_ids[0], payment_ids[1])
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {"allowed_company_ids": [fixture["company_id"]]})
            payments = env["account.payment"].search([("payment_transaction_id", "=", fixture["tx_id"])])
            self.assertEqual(len(payments), 1)
            self.assertEqual(payments.move_id.state, "posted")
            self.assertEqual(payments.amount, 12.50)
            self.assertTrue(env["payment.transaction"].browse(fixture["tx_id"]).is_post_processed)
