"""A capability may only read a channel its own asset class can put something into.

The registry could already say that every capability is available for both asset classes,
that every basis has a registered confidence formula, and that every retired name still
resolves. Nothing said that the *data* an implementation needs could ever exist. So eleven
shipped equity implementations read three channels — ``options_chain``, ``macro_series`` and
``insider`` — that no shipped equity ingest path could write, and every gate stayed green
while ``iv-surface``, ``open-interest``, ``perp-basis`` and ``whale-alerts`` returned zero
rows on any lake this product can build, and ``spot-future-basis`` returned one row whose
``carry_pct`` was null under a ``prov_confidence`` of 0.667.

That is the codebase's own recorded defect, twice. ``capabilities/analytics.py`` defines it
on ``slippage`` as "one object reading a channel no equity provider writes, under a
declaration naming a basis its code path could not reach", and ``tests/capabilities/
test_market.py`` records the same thing for ``open-interest`` — whose fix swapped
``open_interest`` for ``options_chain``, which was equally unwritable. Recording a defect
twice and catching it neither time is what a missing gate looks like.

The property
------------
For every capability implementation, and for every asset-class-owned module that reads the
lake on one's behalf: the channels read are channels some shipped ingest path for that asset
class can write.

"Shipped ingest path" is the operative half. ``TreasuryYieldClient`` could build a
``MacroSeries`` from the day it was written; it had zero call sites in ``src/``, and
``collect``, ``collect-market`` and ``backfill`` resolve sources through the two provider
factories. A channel is writable when a *registered* connector can produce it, which is why
:func:`writable_channels` starts from those registries and not from a search for record
constructors.

Two derivations, and why neither is enough alone
------------------------------------------------
**Reads** are declared, on :attr:`~crocodile.core.capability.Impl.reads`. The alternative
was to scan the SQL each implementation's module authors, and it does not reach: the equity
carry reads ``macro_series`` two modules below the capability, ``price_leg`` scans a channel
held in a loop variable over a module constant so no table name appears at the call site,
and ``core.analytics.volsurface`` reads ``options_chain`` on behalf of *both* asset classes
from one file that cannot be attributed to either. A scan goes blind exactly where the
indirection is, which is where the defect was.

**Writes** are derived, not declared, wherever the connector has not declared: equity
providers state :attr:`~crocodile.equity.providers.base.Provider.supported_channels` and are
gated to (``test_provider_channels.py``), while the crypto connectors have not been through
that exercise, so their write-set is read off the record types their package constructs.
Both are mechanical from the registry, which is what stops this gate becoming a pair of
claims agreeing with each other.

**A coarse module sweep** backs the declarations up where a scan *can* see. It attributes
every channel named in the SQL of a module under ``crocodile.equity.analytics`` or
``crocodile.crypto.analytics`` to that package's asset class — a much weaker claim than
per-implementation attribution, and one that needs no authoring at all, so it holds for
modules nobody remembered to declare for. It is the half that would have caught the original
defect on its own: ``oi_aggregator.py`` reads ``FROM "options_chain"`` and lives under
``crocodile.equity``.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
from collections.abc import Iterable

import msgspec
import pytest

from crocodile.capabilities import load_all
from crocodile.core.capability import REGISTRY, AssetClass, Impl
from crocodile.core.schema import records as record_module
from crocodile.core.schema.enums import CHANNEL_SUCCESSORS, Channel

load_all()

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
_PKG = _SRC / "crocodile"

_KNOWN_CHANNELS = frozenset(channel.value for channel in Channel)

_TABLE_IN_SQL = re.compile(r"\b(?:FROM|JOIN)\s+\"?([a-z_][a-z0-9_]*)\"?", re.IGNORECASE)
"""Table names in a SQL string literal.

Deliberately loose about what a statement is: ``FROM read_parquet(...)``, ``FROM grid`` and
``FROM generate_series(...)`` all match and are then discarded by the
:data:`_KNOWN_CHANNELS` filter, because a name that is not a channel cannot be an unwritable
one. The filter is what makes a permissive regex safe here.
"""

_EXTRA_CRYPTO_WRITERS = ("crocodile/crypto/exchanges/ccxt_universal",)
"""Shipped crypto writers that are not entries in ``crypto.exchanges.factory._REGISTRY``.

``collect-market`` builds a :class:`CCXTConnector` directly for every ccxt-supported venue —
"every ccxt-supported venue must use the universal connector here", per ``ops.py`` — so it is
a write path the factory registry does not name. Listed rather than discovered, because the
thing that makes it a write path is a branch in ``collect_market``, and a discovery rule
loose enough to find it would also find modules that merely build records for tests.
"""


def _canonical(name: str) -> str:
    """Resolve a retired tag to the channel whose record absorbed it."""
    return CHANNEL_SUCCESSORS.get(name.lower(), name.lower())


def _record_class_channels() -> dict[str, str]:
    """Record class name → the channel tag it is written to."""
    out: dict[str, str] = {}
    for attr in dir(record_module):
        obj = getattr(record_module, attr)
        if isinstance(obj, type) and issubclass(obj, msgspec.Struct):
            tag = getattr(obj.__struct_config__, "tag", None)
            if isinstance(tag, str) and tag in _KNOWN_CHANNELS:
                out[attr] = tag
    return out


def _channels_constructed_under(package_dir: pathlib.Path) -> set[str]:
    """Channels whose record type is *constructed* somewhere in ``package_dir``.

    The fallback write-set for a connector that has not declared one. Construction and not
    import: ``core.schema.records`` defines every record type and writes none of them, and a
    connector that imports ``Trade`` for a type annotation has not thereby produced one.
    """
    by_name = _record_class_channels()
    found: set[str] = set()
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                channel = by_name.get(node.func.id)
                if channel is not None:
                    found.add(channel)
    return found


def _equity_write_sets() -> dict[str, set[str]]:
    from crocodile.equity.providers.factory import _REGISTRY as equity_registry

    out: dict[str, set[str]] = {}
    for name, cls in equity_registry.items():
        declared = cls.supported_channels
        if declared is not None:
            out[name] = {_canonical(channel) for channel in declared}
            continue
        module = pathlib.Path(inspect.getfile(cls)).parent
        out[name] = _channels_constructed_under(module)
    return out


def _crypto_write_sets() -> dict[str, set[str]]:
    from crocodile.crypto.exchanges.factory import _REGISTRY as crypto_registry

    packages = {
        name: _PKG.parent / (module.replace(".", "/") + ".py")
        for name, (module, _cls) in crypto_registry.items()
    }
    out = {name: _channels_constructed_under(path.parent) for name, path in packages.items()}
    for relative in _EXTRA_CRYPTO_WRITERS:
        out[relative.rsplit("/", 1)[-1]] = _channels_constructed_under(_SRC / relative)
    return out


def writable_channels(asset_class: AssetClass) -> frozenset[str]:
    """Every channel some registered ingest path for ``asset_class`` can write."""
    sets = _equity_write_sets() if asset_class is AssetClass.EQUITY else _crypto_write_sets()
    union: set[str] = set()
    for channels in sets.values():
        union |= channels
    return frozenset(union)


def channels_read_in_source(source: str, *, filename: str = "<string>") -> frozenset[str]:
    """Channels a piece of Python names as something to read out of the lake.

    Two forms, both syntactic: a table in a SQL string literal, and the first positional
    argument of a ``.scan(...)`` call. It sees no further than the text it is handed — that
    is the whole reason :attr:`~crocodile.core.capability.Impl.reads` is authored rather than
    derived from this — so its job here is to be *sound* rather than complete: everything it
    finds really is a read, and the tests using it are written as "everything found must be
    writable", never as "everything read was found".
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for name in _TABLE_IN_SQL.findall(node.value):
                if _canonical(name) in _KNOWN_CHANNELS:
                    found.add(_canonical(name))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "scan" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if _canonical(first.value) in _KNOWN_CHANNELS:
                        found.add(_canonical(first.value))
    return frozenset(found)


def _modules_under(relative: str) -> list[pathlib.Path]:
    return [
        path
        for path in sorted((_PKG / relative).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _impls() -> list[tuple[str, AssetClass, Impl]]:
    return [
        (name, asset_class, impl)
        for name, capability in sorted(REGISTRY.items())
        for asset_class, impl in sorted(capability.impls.items(), key=lambda kv: kv[0].value)
    ]


def _ids(rows: Iterable[tuple[str, AssetClass, Impl]]) -> list[str]:
    return [f"{name}-{asset_class.value}" for name, asset_class, _ in rows]


# ---------------------------------------------------------------------------
# Guard the guards: every assertion below is an emptiness away from vacuous.
# ---------------------------------------------------------------------------


def test_the_write_sets_are_not_empty() -> None:
    """An empty write-set would fail everything; a missing one would pass everything.

    Both failure modes have to be ruled out by name, because the interesting assertions are
    subset checks and a subset check against a set that was never populated is the kind of
    green this file exists to distrust.
    """
    equity = _equity_write_sets()
    crypto = _crypto_write_sets()
    assert len(equity) >= 9, f"only {len(equity)} equity providers resolved"
    assert len(crypto) >= 10, f"only {len(crypto)} crypto connectors resolved"
    for name, channels in {**equity, **crypto}.items():
        if name == "superchain":
            # The one connector whose own package constructs no record: it subclasses
            # `BaseOnchainConnector` and inherits its normalisation, so the records it
            # produces are built in ``base_onchain``'s package and counted there. That is
            # harmless because the write-set is a *union* across connectors — every channel
            # superchain can emit is already in it — and it is named here rather than
            # silently tolerated, so a second empty write-set is a failure and not a
            # precedent.
            continue
        assert channels, f"{name} resolved to an empty write-set"


def test_the_reader_scan_finds_the_reads_it_is_pointed_at() -> None:
    """Guard the scanner itself against a regex that stopped matching.

    Both forms, and one negative: a name that is not a channel must not become one, or the
    sweep below would fail on ``FROM generate_series``.
    """
    assert channels_read_in_source('x = "SELECT * FROM options_chain WHERE a = ?"') == {
        "options_chain"
    }
    assert channels_read_in_source('x = \'SELECT * FROM "insider"\'') == {"insider"}
    assert channels_read_in_source('catalog.scan("macro_series", s, a, b)') == {"macro_series"}
    assert channels_read_in_source('x = "SELECT * FROM option_quote"') == {"options_chain"}
    assert channels_read_in_source('x = "SELECT * FROM generate_series(1)"') == frozenset()


def test_at_least_one_implementation_declares_what_it_reads() -> None:
    """Otherwise the per-implementation gate is a loop over nothing."""
    assert [name for name, _ac, impl in _impls() if impl.reads]


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "asset_class", "impl"), _impls(), ids=_ids(_impls()))
def test_an_implementation_reads_only_what_its_asset_class_can_write(
    name: str, asset_class: AssetClass, impl: Impl
) -> None:
    """The property, per implementation.

    Before this change the equity halves of ``iv-surface``, ``term-structure``, ``vol-skew``,
    ``risk-reversal``, ``open-interest`` and ``perp-basis`` failed on ``options_chain``;
    ``spot-future-basis``, ``funding-apr`` and ``perp-basis`` on ``macro_series``; and
    ``whale-alerts`` on ``insider``.
    """
    writable = writable_channels(asset_class)
    unwritable = sorted(set(impl.reads) - writable)
    assert not unwritable, (
        f"{name} [{asset_class.value}] reads {unwritable}, which no registered "
        f"{asset_class.value} provider can write, so it can only ever return an empty "
        f"answer under a provenance tail that says otherwise. Either wire a source that "
        f"writes the channel into the ingest registry, or make the capability's absence "
        f"honest with Provenance.UNAVAILABLE / CapabilityUnavailable."
    )


@pytest.mark.parametrize(("name", "asset_class", "impl"), _impls(), ids=_ids(_impls()))
def test_a_declared_read_set_is_spelled_in_channels(
    name: str, asset_class: AssetClass, impl: Impl
) -> None:
    """A misspelt read is a claim no write-set can ever satisfy, and no gate can interpret."""
    unknown = sorted(set(impl.reads) - _KNOWN_CHANNELS)
    assert not unknown, f"{name} [{asset_class.value}] declares reads={unknown}, not Channels"
    retired = sorted(channel for channel in impl.reads if channel in CHANNEL_SUCCESSORS)
    assert not retired, (
        f"{name} [{asset_class.value}] declares the retired tag(s) {retired}; a partition is "
        f"read under the surviving name"
    )


@pytest.mark.parametrize(("name", "asset_class", "impl"), _impls(), ids=_ids(_impls()))
def test_a_read_visible_in_the_function_itself_is_declared(
    name: str, asset_class: AssetClass, impl: Impl
) -> None:
    """Anti-drift, over the part of the read-set a scan can actually see.

    Bounded to the implementation's own source on purpose. A whole-module scan would
    attribute every ``FROM`` in ``capabilities/analytics.py`` to all twenty of the
    implementations declared in it, and a gate that fires for the wrong reason gets
    suppressed. What this catches is the case where somebody adds a ``catalog.scan("x")``
    to an adapter and does not add ``x`` to the declaration beside it.
    """
    found = channels_read_in_source(inspect.getsource(impl.fn), filename=impl.fn.__name__)
    missing = sorted(found - set(impl.reads))
    assert not missing, (
        f"{name} [{asset_class.value}] reads {missing} in the body of "
        f"{impl.fn.__module__}.{impl.fn.__name__} and does not declare it"
    )


@pytest.mark.parametrize(
    ("relative", "asset_class"),
    [
        ("equity/analytics", AssetClass.EQUITY),
        ("crypto/analytics", AssetClass.CRYPTO),
    ],
)
def test_an_asset_class_analytics_module_reads_only_what_that_class_can_write(
    relative: str, asset_class: AssetClass
) -> None:
    """The sweep that needs no declaration, and the one that would have caught this alone.

    A module under ``crocodile.equity`` serves equities — that is what the package boundary
    means — so any channel its SQL names is a channel equity ingest has to be able to write.
    ``oi_aggregator.py`` selects ``FROM "options_chain"``, ``carry.py`` from ``macro_series``
    and ``filings.py`` from ``insider``: three failures, from three files, with nothing
    declared anywhere.
    """
    writable = writable_channels(asset_class)
    offenders: dict[str, list[str]] = {}
    for path in _modules_under(relative):
        unwritable = sorted(
            channels_read_in_source(path.read_text(), filename=str(path)) - writable
        )
        if unwritable:
            offenders[str(path.relative_to(_PKG))] = unwritable
    assert not offenders, (
        f"these {asset_class.value} modules read channels no registered {asset_class.value} "
        f"provider writes: {offenders}"
    )


def test_a_shared_analytics_read_is_claimed_by_some_implementation() -> None:
    """``core/analytics`` serves both classes, so the sweep above cannot judge it.

    :mod:`crocodile.core.analytics.volsurface` reads ``options_chain`` for crypto *and* for
    equities out of one file, and a package-boundary rule has nothing to say about which. The
    only thing that can attribute it is a declaration, so the requirement here is that one
    exists: every channel the shared analytics read must appear in some implementation's
    :attr:`~crocodile.core.capability.Impl.reads`, which is what puts it in front of the
    per-asset-class gate above.
    """
    shared: set[str] = set()
    for path in _modules_under("core/analytics"):
        shared |= channels_read_in_source(path.read_text(), filename=str(path))
    declared = {channel for _n, _ac, impl in _impls() for channel in impl.reads}
    unclaimed = sorted(shared - declared)
    assert not unclaimed, (
        f"core/analytics reads {unclaimed} and no implementation declares it, so no gate "
        f"knows which asset class has to be able to write it"
    )


def test_an_equity_declaration_is_backed_by_a_record_the_package_builds() -> None:
    """A provider cannot declare a channel whose record its own package never constructs.

    The declarations feed the write-set the whole gate rests on, so an aspirational one would
    turn this file green by assertion. This is the mechanical check on them, and it is the
    reason the fallback in :func:`_equity_write_sets` is a *fallback* rather than an
    alternative: both derivations are computed for every declaring provider and the declared
    one has to be a subset.
    """
    from crocodile.equity.providers.factory import _REGISTRY as equity_registry

    offenders: dict[str, list[str]] = {}
    for name, cls in equity_registry.items():
        declared = cls.supported_channels
        if declared is None:
            continue
        built = _channels_constructed_under(pathlib.Path(inspect.getfile(cls)).parent)
        overclaimed = sorted({_canonical(channel) for channel in declared} - built)
        if overclaimed:
            offenders[name] = overclaimed
    assert not offenders, (
        f"these providers declare channels whose record type their package never "
        f"constructs: {offenders}"
    )


def test_the_four_channels_the_defect_was_about_are_writable_now() -> None:
    """The regression test for the fix itself, named rather than implied.

    Every assertion above is a subset check, and a subset check goes green when a set
    shrinks. If ``yahoo``, ``treasury`` and ``sec_edgar`` left the provider registry, the
    reads would have to leave the declarations too and every gate here would pass on the way
    back to where this started.
    """
    writable = writable_channels(AssetClass.EQUITY)
    for channel in ("options_chain", "macro_series", "insider", "holding_13f"):
        assert channel in writable, (
            f"no registered equity provider writes {channel!r}; the capabilities that read "
            f"it are back to answering nothing"
        )
