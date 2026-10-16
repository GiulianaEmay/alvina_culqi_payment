"""CulqiLink operations verified against the official apiculqi.yaml.

The link object does not establish settlement; it must never confirm a payment.
No automatic retry of POST: a timeout may mean the link was created remotely.
"""
import re
import requests
from .contract import AdapterUnavailable, ContractError, HostedLink


class CulqiAPIError(RuntimeError):
    pass


class CulqiLinkClient:
    BASE = "https://api.culqi.com/v2"
    allowed_link_hosts = frozenset({"express.culqi.com"})

    def __init__(self, secret, *, mode="test", session=None):
        if not secret:
            raise CulqiAPIError("Missing Culqi credential binding")
        if mode not in {"test", "live"}:
            raise CulqiAPIError("Invalid Culqi environment mode")
        self._mode = mode
        self._secret = secret
        self._session = session or requests.Session()

    def _request(self, method, path, payload=None):
        try:
            response = self._session.request(
                method, self.BASE + path,
                headers={"Authorization": "Bearer " + self._secret,
                         "x-culqi-product": "link", "Content-Type": "application/json"},
                json=payload, timeout=(5, 20), allow_redirects=False,
            )
        except requests.RequestException:
            raise CulqiAPIError("Culqi network failure; reconcile before retrying creation") from None
        expected = 201 if method == "POST" else 200
        if response.status_code != expected:
            # Do not leak credentials, provider body, customer data or request URLs.
            raise CulqiAPIError("Culqi request failed (HTTP %s)" % response.status_code)
        try:
            data = response.json()
        except ValueError:
            raise CulqiAPIError("Culqi returned invalid JSON") from None
        if not isinstance(data, dict):
            raise CulqiAPIError("Culqi returned an unexpected response")
        return data

    def create_link(self, request):
        if not 600 <= request.amount_minor <= 999900:
            raise ContractError("CulqiLink amount must be between 600 and 999900 minor units")
        data = self._request("POST", "/links", {
            "amount": request.amount_minor, "currency_code": request.currency,
            "concept": request.reference, "limit_uses": 1,
            "is_open_amount": False, "payment_methods": ["tarjeta"],
        })
        if (data.get("object") != "link" or data.get("amount") != request.amount_minor or
                type(data.get("amount")) is not int or
                data.get("currency_code") != request.currency or
                data.get("concept") != request.reference or
                data.get("is_open_amount") is not False or data.get("limit_uses") != 1):
            raise ContractError("Unexpected CulqiLink creation response; reconcile before retrying")
        remote_id, url = data.get("id"), data.get("url")
        if not isinstance(remote_id, str) or not re.fullmatch(r"link_(?:test|live)_[A-Za-z0-9]+", remote_id):
            raise ContractError("Invalid CulqiLink identifier")
        if not remote_id.startswith("link_" + self._mode + "_"):
            raise ContractError("CulqiLink environment mismatch; reconcile before retrying")
        if not isinstance(url, str):
            raise ContractError("Missing CulqiLink URL")
        return HostedLink(remote_id, url).validate(self.allowed_link_hosts)

    def check_connection(self):
        # Official list operation. Never log or retain the returned links.
        data = self._request("GET", "/links?limit=1")
        if data.get("object") != "list" or not isinstance(data.get("data"), list):
            raise CulqiAPIError("Culqi returned an unexpected list response")
        return True

    def retrieve_payment(self, remote_id):
        raise AdapterUnavailable("Link-to-charge association and final payment evidence need verification")

    def verify_webhook(self, body, headers):
        raise AdapterUnavailable("Webhook authenticity protocol needs verification")
