import pytest

from crocodile.core.store.migrate import migrate_lake


def test_renames_legacy_partition_directories(tmp_path):
    (tmp_path / "exchange=binance" / "channel=trade").mkdir(parents=True)
    (tmp_path / "provider=yahoo" / "channel=bar").mkdir(parents=True)

    renamed = migrate_lake(tmp_path)

    assert (tmp_path / "source=binance" / "channel=trade").is_dir()
    assert (tmp_path / "source=yahoo" / "channel=bar").is_dir()
    assert not (tmp_path / "exchange=binance").exists()
    assert renamed == 2


def test_is_idempotent(tmp_path):
    (tmp_path / "exchange=okx").mkdir(parents=True)
    assert migrate_lake(tmp_path) == 1
    assert migrate_lake(tmp_path) == 0


def test_refuses_to_clobber_an_existing_target(tmp_path):
    (tmp_path / "exchange=okx").mkdir(parents=True)
    (tmp_path / "source=okx").mkdir(parents=True)
    with pytest.raises(FileExistsError, match="okx"):
        migrate_lake(tmp_path)


def test_ignores_unrelated_directories(tmp_path):
    (tmp_path / "notes").mkdir()
    assert migrate_lake(tmp_path) == 0
    assert (tmp_path / "notes").is_dir()


def test_missing_lake_is_not_an_error(tmp_path):
    """A data_dir that was never written to migrates to zero, not to a traceback.

    `crocodile migrate-lake` is the kind of command run defensively, often
    against a path that has no lake yet. Raising there would make the safe
    thing look like the broken thing.
    """
    assert migrate_lake(tmp_path / "never-written") == 0


def test_leaves_the_contents_untouched(tmp_path):
    """The migration is a rename: no Parquet file is read, decoded or rewritten."""
    part = tmp_path / "exchange=binance" / "channel=trade" / "date=2024-01-01" / "bucket=0"
    part.mkdir(parents=True)
    (part / "part-abc.parquet").write_bytes(b"PAR1-not-really-parquet")

    migrate_lake(tmp_path)

    moved = tmp_path / "source=binance" / "channel=trade" / "date=2024-01-01" / "bucket=0"
    assert (moved / "part-abc.parquet").read_bytes() == b"PAR1-not-really-parquet"


def test_a_file_named_like_a_partition_is_left_alone(tmp_path):
    """Only directories are partitions. A stray file keeps its name."""
    (tmp_path / "exchange=binance").write_text("not a partition")

    assert migrate_lake(tmp_path) == 0
    assert (tmp_path / "exchange=binance").is_file()
