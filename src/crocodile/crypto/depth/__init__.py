"""Crypto depth: the ladder the venue already published, read back out of the lake.

The mirror of :mod:`crocodile.equity.depth`, and the package layout is the argument. That
one holds a ``DepthSource`` protocol and two implementations of it because an equity ladder
has to be *fetched* and there is more than one way to fetch it. This one holds a single
function because a crypto ladder is already stored, and a protocol over one lake read would
be a shape borrowed from the other market rather than earned in this one.
"""

from crocodile.crypto.depth.book_slice import LakeQuery, depth_from_book_snapshots

__all__ = ["LakeQuery", "depth_from_book_snapshots"]
