"""Pure Python tests; do not claim these validate Odoo accounting or Culqi HTTP."""
import importlib.util
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

path = Path(__file__).resolve().parents[1] / "alvina_culqi_payment/services/contract.py"
spec = importlib.util.spec_from_file_location("culqi_contract", path)
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.request = c.LinkRequest("ALV-123", 1, 12050, "PEN")
        self.payment = c.VerifiedPayment("remote-1", "ALV-123", 1, 12050, "PEN", "paid")

    def test_mock_link(self):
        adapter = Mock()
        adapter.create_link.return_value = c.HostedLink("remote-1", "https://payments.example.test/pago/1")
        link = adapter.create_link(self.request).validate({"payments.example.test"})
        self.assertEqual(link.remote_id, "remote-1")
        adapter.create_link.assert_called_once_with(self.request)

    def test_zero_negative_nonfinite_precision(self):
        for amount in (0, -1, "NaN", "Infinity", "12.001", None):
            with self.subTest(amount=amount), self.assertRaises(c.ContractError):
                c.minor_units(amount)

    def test_exact_minor_units(self):
        self.assertEqual(c.minor_units("120.50"), 12050)

    def test_success(self):
        self.assertEqual(self.payment.validate(self.request, "remote-1").state, "paid")

    def test_rejected(self):
        self.assertEqual(replace(self.payment, state="failed").validate(self.request, "remote-1").state, "failed")

    def test_amount_mismatch(self):
        with self.assertRaises(c.ContractError):
            replace(self.payment, amount_minor=12049).validate(self.request, "remote-1")

    def test_company_isolation(self):
        with self.assertRaises(c.ContractError):
            replace(self.payment, company_id=2).validate(self.request, "remote-1")

    def test_currency_reference_and_remote_id(self):
        for changes in ({"currency": "USD"}, {"reference": "other"}, {"remote_id": "other"}):
            with self.subTest(changes=changes), self.assertRaises(c.ContractError):
                replace(self.payment, **changes).validate(self.request, "remote-1")

    def test_unknown_status(self):
        with self.assertRaises(c.ContractError):
            replace(self.payment, state="unknown").validate(self.request, "remote-1")

    def test_untrusted_urls(self):
        for url in ("http://payments.example.test/1", "https://evil.test/1",
                    "https://user:pass@payments.example.test/1", "https://payments.example.test:8443/1"):
            with self.subTest(url=url), self.assertRaises(c.ContractError):
                c.HostedLink("1", url).validate({"payments.example.test"})



if __name__ == "__main__":
    unittest.main()
