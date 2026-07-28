"""What the CLI prints, for the two return shapes that printed nothing usable.

This is the one surface whose output is routinely piped into another program, and the two
things it printed for a non-table result were a Python ``repr`` — single-quoted, so neither
JSON nor the bordered polars frame the legacy CLI printed — and, for a ``STREAM``, the word
``None``.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from typer.testing import CliRunner

from crocodile.core import capability as capability_module
from crocodile.core.capability import REGISTRY, AssetClass, Capability, Impl, ReturnKind
from crocodile.core.schema.provenance import Provenance
from crocodile.surfaces import cli, dispatch


def test_a_scalar_is_printed_as_json_rather_than_as_a_python_repr(lake: pathlib.Path) -> None:
    """``catalog-summary`` returns a dict, and ``str(dict)`` is not parseable by anything."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["catalog-summary", "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "'" not in result.output, result.output
    assert json.loads(result.output)["channels"] == ["book_snapshot", "trade"]


def test_a_table_that_is_not_a_frame_is_printed_as_json(lake: pathlib.Path) -> None:
    """``catalog-channels`` returns a list, which the frame renderer cannot help with."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["catalog-channels", "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == ["book_snapshot", "trade"]


def test_a_frame_is_still_printed_as_a_frame(lake: pathlib.Path) -> None:
    """The bordered polars table is what an operator reads, and it stays."""
    result = CliRunner().invoke(
        cli.build_app(),
        ["catalog", "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert "│" in result.output, result.output


# ---------------------------------------------------------------------------
# STREAM
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FakeSubscription:
    """Shaped like :class:`~crocodile.capabilities.ops.Subscription`, connecting to nothing.

    A real ``collect`` opens a websocket to a live venue, which is not a thing a test may
    do; what is under test is what the projection does with the object, and that is decided
    entirely by the three fields a surface is allowed to read off it.
    """

    sources: tuple[str, ...]
    channels: tuple[str, ...]
    duration_seconds: float | None

    async def run(self) -> None:
        return None


@pytest.fixture
def _collecting_nothing() -> Iterator[None]:
    """Replace ``collect``'s crypto implementation with one that opens no sockets."""
    original = REGISTRY["collect"]

    def _fake(ctx: Any, params: Any) -> _FakeSubscription:
        return _FakeSubscription(
            sources=tuple(params.sources),
            channels=tuple(params.channels),
            duration_seconds=params.duration_seconds,
        )

    REGISTRY["collect"] = Capability(
        name=original.name,
        summary=original.summary,
        params=original.params,
        returns=original.returns,
        aliases=original.aliases,
        impls={
            asset_class: Impl(fn=_fake, prov=impl.prov, basis=impl.basis)
            for asset_class, impl in original.impls.items()
        },
    )
    capability_module._DECLARED_NAMES.add(original.name)
    try:
        yield
    finally:
        REGISTRY["collect"] = original


def test_a_finished_collection_run_says_what_it_collected(
    lake: pathlib.Path, _collecting_nothing: None
) -> None:
    """A ``STREAM`` run returns ``None``, so rendering the return value printed ``None``.

    Everything worth reporting is known before the run starts — which is the whole reason a
    ``Subscription`` is handed back unstarted — so it is read off there instead.
    """
    result = CliRunner().invoke(
        cli.build_app(),
        ["collect", "--sources", "deribit", "--symbols", "deribit:BTC-PERPETUAL",
         "--channels", "trade,book_snapshot", "--duration-seconds", "1",
         "--asset-class", "crypto", "--data-dir", str(lake)],
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() != "None"
    assert "trade" in result.output and "book_snapshot" in result.output
    assert "deribit" in result.output
    assert "1.0s" in result.output


def test_an_unstarted_subscription_describes_itself_before_it_runs() -> None:
    """Read off the object rather than guessed, and ``None`` for anything that is not one."""
    pending = _FakeSubscription(sources=("deribit",), channels=("trade",), duration_seconds=None)
    assert dispatch.stream_summary(pending) == {
        "sources": ["deribit"],
        "channels": ["trade"],
        "duration_seconds": None,
    }
    assert dispatch.stream_summary({"sources": ["deribit"]}) is None
    assert dispatch.stream_summary(ReturnKind.STREAM) is None
    assert dispatch.stream_summary(Provenance.NATIVE) is None
    assert dispatch.stream_summary(AssetClass.CRYPTO) is None
