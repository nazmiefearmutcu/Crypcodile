"""Namespace-blind element access, shared by the two ownership/holdings parsers.

Every EDGAR XML attachment this package reads spells its element names three different
ways across the corpus, and none of the three is a version the parser can pin. A Form 4
``ownershipDocument`` carries no namespace at all. A 13F ``informationTable`` carries
``http://www.sec.gov/edgar/document/thirteenf/informationtable``, sometimes as the default
namespace and sometimes bound to a prefix the filing agent chose — ``ns1:infoTable``,
``n1:infoTable`` and bare ``infoTable`` are all live in EDGAR today, produced by different
agents against the same schema. A 13F ``primary_doc.xml`` carries a *third* namespace,
``http://www.sec.gov/edgar/thirteenffiler``.

``ElementTree.find("infoTable")`` matches exactly one of those and silently returns
``None`` for the others, which is the failure this module exists to remove: a parser that
returns zero holdings for a real information table looks identical to a manager who filed
an empty one. So every lookup below matches on the *local* name and ignores whatever
namespace the tag arrived under.

Nothing here validates. Parsing is done with :mod:`xml.etree.ElementTree`, which expands
internal entities and is therefore exposed to an entity-expansion bomb; the payloads
reaching it come from ``https://www.sec.gov`` over TLS or from a fixture checked into this
repository, and no caller-supplied document reaches it. A parser handed a hostile document
would need ``defusedxml``, which is not a dependency of this project.
"""

from __future__ import annotations

from collections.abc import Iterator
from xml.etree.ElementTree import Element

__all__ = ["child", "children", "descendants", "local_name", "text_of"]


def local_name(tag: str) -> str:
    """Return ``tag`` without its ``{namespace}`` prefix.

    ``ElementTree`` reports a namespaced tag in Clark notation —
    ``{http://www.sec.gov/edgar/thirteenffiler}periodOfReport`` — and collapses any prefix
    the document used into it, which is what makes matching on the local name enough to
    cover the ``ns1:``/``n1:``/bare spellings at once.
    """
    return tag.rsplit("}", 1)[-1]


def children(element: Element, name: str) -> Iterator[Element]:
    """Yield the direct children of ``element`` whose local name is ``name``."""
    for candidate in element:
        if local_name(candidate.tag) == name:
            yield candidate


def child(element: Element | None, name: str) -> Element | None:
    """Return the first direct child named ``name``, or ``None``.

    ``element`` is allowed to be ``None`` so a chain of optional lookups reads as one
    expression instead of four guards; a missing ancestor and a missing child are the same
    answer to the caller, which is "the filing did not state this".
    """
    if element is None:
        return None
    return next(children(element, name), None)


def descendants(element: Element, name: str) -> Iterator[Element]:
    """Yield every descendant of ``element`` whose local name is ``name``, in document order.

    Used where the containing element's own name is not worth pinning — a 13F cover page
    nests ``filingManager`` under ``formData`` under ``edgarSubmission``, and two of those
    three levels differ between the live schema versions in the corpus.
    """
    for candidate in element.iter():
        if candidate is not element and local_name(candidate.tag) == name:
            yield candidate


def text_of(element: Element | None, *path: str) -> str | None:
    """Return the stripped text at ``element`` followed by ``path``, or ``None``.

    Empty and whitespace-only text answers ``None`` rather than ``""``. An EDGAR attachment
    routinely carries an element with no content where the filer had nothing to state —
    ``<transactionPricePerShare/>`` on a gift — and an empty string would flow into a
    ``float()`` as a parse failure indistinguishable from malformed input, or into a record
    as a reported blank.

    Form 4 wraps almost every leaf in a ``<value>`` element so a footnote reference can sit
    beside it, so a bare ``text_of(node)`` on such a leaf finds only whitespace. Callers
    pass ``"value"`` as the last path segment where the schema has one; passing it where the
    schema does not is harmless and returns ``None``.
    """
    for name in path:
        element = child(element, name)
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None
