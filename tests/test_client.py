import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock
import requests

root = Path(__file__).resolve().parents[1] / "alvina_culqi_payment/services"
pkg = types.ModuleType("culqi_services_test")
pkg.__path__ = [str(root)]
sys.modules[pkg.__name__] = pkg
for name in ("contract", "culqi"):
    spec = importlib.util.spec_from_file_location(pkg.__name__ + "." + name, root / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
from culqi_services_test.contract import ContractError, LinkRequest
from culqi_services_test.culqi import CulqiAPIError, CulqiLinkClient


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = Mock(status_code=201)
        self.data = {"object": "link", "id": "link_test_abcdefghijklmnop",
                     "url": "https://express.culqi.com/pago/EXAMPLE", "amount": 1250,
                     "currency_code": "PEN", "concept": "ALV-1", "is_open_amount": False,
                     "limit_uses": 1}
        self.response.json.return_value = self.data
        self.session.request.return_value = self.response
        self.client = CulqiLinkClient("mock-credential-not-a-real-key", session=self.session)
        self.request = LinkRequest("ALV-1", 1, 1250, "PEN")

    def test_official_create_contract(self):
        link = self.client.create_link(self.request)
        self.assertEqual(link.remote_id, self.data["id"])
        args, kwargs = self.session.request.call_args
        self.assertEqual(args, ("POST", "https://api.culqi.com/v2/links"))
        self.assertEqual(kwargs["headers"]["x-culqi-product"], "link")
        self.assertEqual(kwargs["json"], {"amount": 1250, "currency_code": "PEN",
            "concept": "ALV-1", "limit_uses": 1, "is_open_amount": False,
            "payment_methods": ["tarjeta"]})
        self.assertEqual(kwargs["timeout"], (5, 20))
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertNotIn("verify", kwargs)  # requests default TLS verification retained

    def test_documented_amount_bounds(self):
        for amount in (1, 599, 999901):
            with self.subTest(amount=amount), self.assertRaises(ContractError):
                self.client.create_link(LinkRequest("ALV-1", 1, amount, "PEN"))
        self.session.request.assert_not_called()

    def test_bad_credentials(self):
        self.response.status_code = 401
        self.response.text = "do not expose provider response"
        with self.assertRaises(CulqiAPIError) as caught:
            self.client.create_link(self.request)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn(self.response.text, str(caught.exception))
        self.assertNotIn("mock-credential", str(caught.exception))

    def test_timeout_has_no_automatic_post_retry(self):
        self.session.request.side_effect = requests.Timeout("sensitive request data")
        with self.assertRaises(CulqiAPIError) as caught:
            self.client.create_link(self.request)
        self.session.request.assert_called_once()
        self.assertNotIn("sensitive", str(caught.exception))

    def test_connection_failure(self):
        self.session.request.side_effect = requests.ConnectionError()
        with self.assertRaises(CulqiAPIError):
            self.client.create_link(self.request)

    def test_redirect_not_followed(self):
        self.response.status_code = 302
        with self.assertRaises(CulqiAPIError):
            self.client.create_link(self.request)

    def test_mismatched_response(self):
        for key, value in (("amount", 1200), ("currency_code", "USD"), ("concept", "OTHER"),
                           ("is_open_amount", True), ("limit_uses", 2), ("url", "https://evil.test/"),
                           ("id", "../../events")):
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.response.json.return_value = dict(self.data, **{key: value})
                self.client.create_link(self.request)

    def test_invalid_json(self):
        self.response.json.side_effect = ValueError()
        with self.assertRaises(CulqiAPIError):
            self.client.create_link(self.request)

    def test_read_only_connection_check(self):
        self.response.status_code = 200
        self.response.json.return_value = {"object": "list", "data": []}
        self.assertTrue(self.client.check_connection())
        self.assertEqual(self.session.request.call_args.args[0], "GET")

    def test_missing_binding(self):
        with self.assertRaises(CulqiAPIError):
            CulqiLinkClient(None, session=self.session)
        self.session.request.assert_not_called()

    def test_response_environment_mismatch(self):
        self.data["id"] = "link_live_abcdefghijklmnop"
        with self.assertRaises(ContractError):
            self.client.create_link(self.request)

    def test_connection_requires_a_list_response(self):
        self.response.status_code = 200
        self.response.json.return_value = {"unexpected": True}
        with self.assertRaises(CulqiAPIError):
            self.client.check_connection()
