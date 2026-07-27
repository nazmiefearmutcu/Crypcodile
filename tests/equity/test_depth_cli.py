from typer.testing import CliRunner

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import DepthProfile
from crocodile.equity.legacy.cli import app

_TAIL = provenance_fields("yahoo_1m_vap", {"n_volume_bars": 195})

runner = CliRunner()


def test_depth_cli_prints_labeled_synth(monkeypatch):
    prof = DepthProfile(
        source="synth", symbol="synth:AAPL", symbol_raw="AAPL", local_ts=1,
        asset_class=AssetClass.EQUITY, source_ts=None,
        prov=_TAIL.prov, prov_basis=_TAIL.prov_basis,
        prov_confidence=_TAIL.prov_confidence, prov_inputs=_TAIL.prov_inputs,
        bids=[(99.0, 10.0)], asks=[(101.0, 8.0)], reference_price=100.0, depth=2,
    )

    class _FakeSource:
        async def snapshot(self, symbol): return prof

    monkeypatch.setattr("crocodile.equity.depth.select.select_depth_source", lambda **k: _FakeSource())
    # also patch the name imported into cli if imported at call-time
    import crocodile.equity.depth as depthpkg
    monkeypatch.setattr(depthpkg, "select_depth_source", lambda **k: _FakeSource())

    result = runner.invoke(app, ["depth", "AAPL", "--no-persist"])
    assert result.exit_code == 0
    assert "SYNTHETIC" in result.stdout
    assert "99.0" in result.stdout and "101.0" in result.stdout
