"""Internal normalized contract. These fields are NOT a claimed Culqi REST schema.

Creation uses the official REST schema; settlement remains blocked pending verification.
Never infer the hosted-link API from Culqi Orders or the legacy checkout API.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


class ContractError(ValueError):
    pass


class AdapterUnavailable(RuntimeError):
    pass


def minor_units(amount, decimals=2):
    try:
        value = Decimal(str(amount))
        scaled = value * (10 ** decimals)
        if not value.is_finite() or value <= 0 or scaled != scaled.to_integral_value():
            raise ContractError("Amount must be positive with exact currency precision")
        return int(scaled)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError("Invalid amount") from exc


@dataclass(frozen=True)
class LinkRequest:
    reference: str
    company_id: int
    amount_minor: int
    currency: str

    def __post_init__(self):
        if not self.reference or self.company_id <= 0:
            raise ContractError("Missing reference or company")
        if type(self.amount_minor) is not int or self.amount_minor <= 0:
            raise ContractError("Invalid minor-unit amount")
        if self.currency not in {"PEN", "USD"}:
            raise ContractError("Currency not enabled for evaluation")


@dataclass(frozen=True)
class HostedLink:
    remote_id: str
    url: str

    def validate(self, allowed_hosts):
        try:
            parsed = urlsplit(self.url)
            trusted = (parsed.scheme == "https" and parsed.hostname in allowed_hosts
                       and not parsed.username and not parsed.password
                       and parsed.port in (None, 443)
                       and parsed.path.startswith("/pago/"))
        except (TypeError, ValueError):
            trusted = False
        if not self.remote_id or not trusted:
            raise ContractError("Untrusted hosted payment URL")
        return self


@dataclass(frozen=True)
class VerifiedPayment:
    """Produced ONLY by an authenticated API retrieval, never from a public body."""
    remote_id: str
    reference: str
    company_id: int
    amount_minor: int
    currency: str
    state: str

    def validate(self, expected, remote_id):
        if (self.remote_id != remote_id or self.reference != expected.reference or
                self.company_id != expected.company_id or
                type(self.amount_minor) is not int or
                self.amount_minor != expected.amount_minor or self.currency != expected.currency):
            raise ContractError("Payment identity, company, amount or currency mismatch")
        if self.state not in {"pending", "paid", "failed", "cancelled"}:
            raise ContractError("Unknown payment state")
        return self
