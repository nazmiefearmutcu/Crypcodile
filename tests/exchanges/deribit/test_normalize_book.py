import json
import pathlib

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.records import BookDelta, BookSnapshot

FIX = pathlib.Path(__file__).parent / "fixtures" / "book.json"


def test_book_snapshot_then_delta_with_delete():
    msgs = json.loads(FIX.read_text())
    snap = next(iter(normalize_message(msgs[0], local_ts=1)))
    assert isinstance(snap, BookSnapshot)
    assert snap.sequence_id == 100 and snap.is_snapshot
    assert (100.0, 5.0) in snap.bids

    delta = next(iter(normalize_message(msgs[1], local_ts=2)))
    assert isinstance(delta, BookDelta)
    assert delta.seq_id == 101 and delta.prev_seq_id == 100
    # action=delete normalized to amount 0.0 (canonical removal)
    assert (99.0, 0.0) in delta.bids
    assert (100.0, 7.0) in delta.bids
