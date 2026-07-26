"""One place that knows what an environment variable means.

Crypcodile read 16 environment variables and Stockodile 23, each at its point of
use, listed nowhere. ``Settings`` replaces that with one object and one prefix.
Legacy ``CRYPCODILE_*`` / ``STOCKODILE_*`` names are honoured for one minor
version, with a deprecation warning.

``Settings`` reads; it does not parse. Every value but ``data_dir`` stays the
string the environment supplied, because the meaning of ``"true"`` or of a
comma-separated URL list belongs to the code that consumes it, not to the code
that finds it. What this module centralises is *which name carries which
setting* — the question that previously required grepping for ``os.environ``.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import msgspec

from crocodile.core.errors import ConfigError

__all__ = ["Settings"]

PREFIX: Final = "CROCODILE_"
"""The one prefix Crocodile reads."""

LEGACY_PREFIXES: Final = ("CRYPCODILE_", "STOCKODILE_")
"""Accepted for one minor version, then removed. Deprecated, not supported."""

_SECRET_SUFFIXES: Final = ("_key", "_keys", "_secret", "_token", "_password")
"""A field whose name ends in one of these carries a credential.

Deriving the redaction set from the names means a new credential is redacted the
moment it is named like one, rather than the moment someone remembers to add it
to a list.
"""


class Settings(msgspec.Struct, frozen=True):
    """Everything Crocodile reads from the environment.

    Construct with :meth:`from_env`; the defaults below are what you get when the
    environment says nothing.
    """

    data_dir: Path = Path("data")
    """Root of the local lake. The only field parsed into anything but a string."""

    home: str | None = None
    """State directory for payment records and IPC files. ``None`` means ``~/.crocodile``.

    Resolving the default here would bake ``$HOME`` into the object at construction
    time; the consumer expands it. Note that the bare name ``HOME`` is never read —
    only ``CROCODILE_HOME`` and the two legacy spellings — so there is no clash with
    the POSIX variable.
    """

    alpaca_api_key: str | None = None
    """Alpaca market-data key ID."""

    alpaca_api_secret: str | None = None
    """Alpaca market-data secret."""

    alpaca_feed: str = "iex"
    """``iex`` (free) or ``sip`` (paid, full consolidated tape)."""

    finnhub_api_key: str | None = None
    """Finnhub token."""

    finnhub_free_tier: str = "true"
    """Whether to apply free-tier rate limits. Parsed by the connector."""

    coingecko_api_key: str | None = None
    """CoinGecko Pro key. Absent means the public rate-limited endpoint."""

    msn_money_api_key: str | None = None
    """MSN Money apikey query parameter."""

    stooq_zip_path: str | None = None
    """Path to a pre-downloaded Stooq bulk archive, bypassing the HTTP fetch."""

    sec_user_agent: str = "Crocodile/0.1 (contact@crocodile.org)"
    """SEC EDGAR requires a contactable User-Agent; it blocks requests without one."""

    base_rpc_url: str = "https://base-rpc.publicnode.com"
    """Single Base L2 JSON-RPC endpoint, used when ``base_rpc_urls`` is empty."""

    base_rpc_urls: str = ""
    """Comma-separated Base endpoints to rotate across. Split by the connector."""

    custom_pools_ipc_file: str | None = None
    """Where the CLI and the on-chain connector exchange custom pool definitions."""

    admin_token: str | None = None
    """Bearer token for the API server's admin routes.

    Crypcodile spelled this ``ADMIN_API_KEY`` and Stockodile ``ADMIN_TOKEN``; they
    were the same setting under two names and are one name now.
    """

    metrics_token: str | None = None
    """Bearer token guarding the Prometheus metrics route."""

    api_keys: str | None = None
    """JSON object mapping provider to a list of keys, for the rotating key pool."""

    api_keys_file: str | None = None
    """Path to the same JSON, for when it is too large or too secret for the environment."""

    payments_file: str | None = None
    """Ledger of x402 payments. ``None`` means the default under :attr:`home`."""

    recipient_wallet: str = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    """Address x402 payments are sent to. The default is a well-known test account."""

    price_usdc: str = "0.001"
    """USDC charged per metered API request."""

    trust_forwarded_for: str = "false"
    """Whether to believe ``X-Forwarded-For`` when rate-limiting by client IP.

    Believing it behind no proxy lets any caller forge its own address and evade
    the limiter, so it defaults off and must be turned on deliberately.
    """

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Read every known name out of ``env``, defaulting to :data:`os.environ`.

        Raises:
            ConfigError: Two legacy prefixes disagree about the same setting.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        values: dict[str, Any] = {}
        for field in cls.__struct_fields__:
            raw = _lookup(source, _VARS[field])
            if raw is not None:
                values[field] = Path(raw) if field in _PATH_FIELDS else raw
        return cls(**values)

    @classmethod
    def known_names(cls) -> tuple[str, ...]:
        """Every environment variable Crocodile reads, in its canonical spelling."""
        return tuple(sorted(PREFIX + _VARS[field] for field in cls.__struct_fields__))

    def __repr__(self) -> str:
        """Redact credentials, so logging a ``Settings`` cannot leak one.

        msgspec's generated ``__repr__`` prints every field, which puts secrets into
        any log line, exception context, or crash dump that renders the object. A
        redacted field still reports whether it is *set*, since that is usually the
        question being debugged.
        """
        parts = [
            f"{field}=<redacted>"
            if field in _SECRET_FIELDS and getattr(self, field) is not None
            else f"{field}={getattr(self, field)!r}"
            for field in self.__struct_fields__
        ]
        return f"{type(self).__name__}({', '.join(parts)})"


_VARS: Final[Mapping[str, str]] = {
    "data_dir": "DATA_DIR",
    "home": "HOME",
    "alpaca_api_key": "ALPACA_API_KEY",
    "alpaca_api_secret": "ALPACA_API_SECRET",
    "alpaca_feed": "ALPACA_FEED",
    "finnhub_api_key": "FINNHUB_API_KEY",
    "finnhub_free_tier": "FINNHUB_FREE_TIER",
    "coingecko_api_key": "COINGECKO_API_KEY",
    "msn_money_api_key": "MSN_MONEY_API_KEY",
    "stooq_zip_path": "STOOQ_ZIP_PATH",
    "sec_user_agent": "SEC_USER_AGENT",
    "base_rpc_url": "BASE_RPC_URL",
    "base_rpc_urls": "BASE_RPC_URLS",
    "custom_pools_ipc_file": "CUSTOM_POOLS_IPC_FILE",
    "admin_token": "ADMIN_TOKEN",
    "metrics_token": "METRICS_TOKEN",
    "api_keys": "API_KEYS",
    "api_keys_file": "API_KEYS_FILE",
    "payments_file": "PAYMENTS_FILE",
    "recipient_wallet": "RECIPIENT_WALLET",
    "price_usdc": "PRICE_USDC",
    "trust_forwarded_for": "TRUST_FORWARDED_FOR",
}
"""Field name to unprefixed variable name.

Written out rather than derived by upper-casing, because the variable names are a
public contract with every existing deployment and must not follow a Python
field rename. :meth:`Settings.from_env` iterates the struct's fields and indexes
this mapping, so a field added without an entry raises ``KeyError`` on the first
read rather than being silently unreadable.
"""

_PATH_FIELDS: Final = frozenset({"data_dir"})

_SECRET_FIELDS: Final = frozenset(
    field for field in Settings.__struct_fields__ if field.endswith(_SECRET_SUFFIXES)
)


def _lookup(env: Mapping[str, str], name: str) -> str | None:
    """Resolve one setting across the canonical prefix and the two legacy ones.

    A present ``CROCODILE_*`` wins outright and warns about nothing, so migrating is
    a matter of adding the new name rather than removing the old one.
    """
    canonical = PREFIX + name
    if canonical in env:
        return env[canonical]

    legacy = {
        prefix + name: env[prefix + name] for prefix in LEGACY_PREFIXES if prefix + name in env
    }
    if not legacy:
        return None

    distinct = set(legacy.values())
    if len(distinct) > 1:
        # Naming the variables and not their values: one of these could be a
        # credential, and an exception message travels further than the object does.
        raise ConfigError(
            f"conflicting values for {canonical}: "
            f"{' and '.join(sorted(legacy))} disagree. Set {canonical} instead."
        )

    warnings.warn(
        f"{', '.join(sorted(legacy))} is deprecated and will be removed in the next "
        f"minor version; use {canonical}.",
        DeprecationWarning,
        stacklevel=3,
    )
    return distinct.pop()
