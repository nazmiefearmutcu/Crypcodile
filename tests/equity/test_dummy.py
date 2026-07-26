def test_dummy():
    print("HELLO FROM DUMMY TEST")
    assert True


def test_package_version_matches_pyproject() -> None:
    """The importable package version matches the version the distribution declares."""
    import re
    from pathlib import Path

    import crocodile

    # tests/equity/ -> tests/ -> repo root, where the merged pyproject.toml lives.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
    assert m is not None
    assert crocodile.__version__ == m.group(1)
