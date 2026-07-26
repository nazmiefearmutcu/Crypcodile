import pytest

from crocodile.core.config import Settings
from crocodile.core.errors import ConfigError


def test_defaults_need_no_environment():
    assert Settings.from_env({}).data_dir.name == "data"


def test_prefixed_variables_win():
    s = Settings.from_env({"CROCODILE_DATA_DIR": "/tmp/lake"})
    assert str(s.data_dir) == "/tmp/lake"


def test_legacy_names_are_accepted_with_a_warning():
    with pytest.warns(DeprecationWarning, match="CRYPCODILE_DATA_DIR"):
        s = Settings.from_env({"CRYPCODILE_DATA_DIR": "/tmp/old"})
    assert str(s.data_dir) == "/tmp/old"


def test_prefixed_name_beats_legacy_without_warning(recwarn):
    s = Settings.from_env({"CROCODILE_DATA_DIR": "/tmp/new", "STOCKODILE_DATA_DIR": "/tmp/old"})
    assert str(s.data_dir) == "/tmp/new"
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_conflicting_legacy_names_are_an_error():
    with pytest.raises(ConfigError, match="conflicting"):
        Settings.from_env({"CRYPCODILE_DATA_DIR": "/a", "STOCKODILE_DATA_DIR": "/b"})


def test_agreeing_legacy_names_are_not_an_error():
    with pytest.warns(DeprecationWarning):
        s = Settings.from_env({"CRYPCODILE_DATA_DIR": "/same", "STOCKODILE_DATA_DIR": "/same"})
    assert str(s.data_dir) == "/same"


def test_known_names_are_enumerable():
    names = Settings.known_names()
    assert "CROCODILE_DATA_DIR" in names
    assert all(n.startswith("CROCODILE_") for n in names)


def test_secrets_do_not_leak_through_repr():
    s = Settings.from_env({"CROCODILE_ALPACA_API_SECRET": "hunter2"})
    assert "hunter2" not in repr(s)
    assert s.alpaca_api_secret == "hunter2"
