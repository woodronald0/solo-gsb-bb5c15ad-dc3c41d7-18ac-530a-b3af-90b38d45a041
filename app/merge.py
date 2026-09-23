"""Three-way merge of two JSON documents that diverged from a common base.

``base`` is the shared ancestor version; ``left`` and ``right`` are the two
edit lines derived from it.  The merge walks object members recursively:

* a value changed on only one side (relative to ``base``) takes that side;
* a value changed on both sides to the same canonical JSON value takes that
  value;
* values that are objects on all three sides are merged member by member
  over the union of their keys (a member absent from the merged result is
  dropped);
* anything else — both sides changed the same path differently, including a
  deletion racing a modification — is a conflict.

Comparison rules follow the rest of the service:

* equality is decided by canonical JSON bytes, so ``1`` and ``1.0`` (and
  ``true`` and ``1``) are different values;
* a missing object member is distinct from a member set to ``null`` — a
  sentinel tracks presence through the recursion;
* arrays are atomic values: they are never merged element-wise, so the two
  sides diverging anywhere inside an array conflicts the whole array.

The result is either the merged document (when no path conflicts) or the
complete list of conflicts sorted by RFC 6901 pointer — never a partial
merge.  Each conflict carries, for all three sides, a presence marker and
the side's value at the conflicted path.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .canonical import canonical_json_bytes
from .pointers import build_pointer


class _Missing:
    """Sentinel for an absent object member (distinct from JSON ``null``)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


MISSING = _Missing()


def _canonically_equal(a: Any, b: Any) -> bool:
    """Canonical-JSON equality; the ``MISSING`` sentinel equals only itself."""
    if a is MISSING or b is MISSING:
        return a is MISSING and b is MISSING
    return canonical_json_bytes(a) == canonical_json_bytes(b)


def _side(value: Any) -> Dict[str, Any]:
    """Wire shape of one side of a conflict: presence marker plus value."""
    if value is MISSING:
        return {"present": False, "value": None}
    return {"present": True, "value": value}


def _merge_node(
    base: Any,
    left: Any,
    right: Any,
    path: str,
    conflicts: List[Dict[str, Any]],
) -> Any:
    """Merge one value; returns the merged value or ``MISSING``.

    ``MISSING`` means the member takes no slot in the merged object (deleted
    on the winning side, or conflicted — conflicts never produce a partial
    document anyway).
    """
    left_changed = not _canonically_equal(base, left)
    right_changed = not _canonically_equal(base, right)

    if not left_changed:
        # Covers both-unchanged (right is then canonically base as well).
        return right
    if not right_changed:
        return left
    if _canonically_equal(left, right):
        # Both sides made the same edit.
        return left
    if isinstance(base, dict) and isinstance(left, dict) and isinstance(right, dict):
        merged: Dict[str, Any] = {}
        for key in sorted(set(base) | set(left) | set(right)):
            child = _merge_node(
                base.get(key, MISSING),
                left.get(key, MISSING),
                right.get(key, MISSING),
                build_pointer(path, key),
                conflicts,
            )
            if child is not MISSING:
                merged[key] = child
        return merged
    conflicts.append(
        {
            "path": path,
            "base": _side(base),
            "left": _side(left),
            "right": _side(right),
        }
    )
    return MISSING


def merge_documents(base: Any, left: Any, right: Any) -> Dict[str, Any]:
    """Merge *left* and *right* against their common ancestor *base*.

    Returns ``{"merged": document, "conflicts": []}`` when every path could
    be reconciled, otherwise ``{"conflicts": [...]}`` with every conflicted
    RFC 6901 path (sorted) and no partial merge document.
    """
    conflicts: List[Dict[str, Any]] = []
    merged = _merge_node(base, left, right, "", conflicts)
    if conflicts:
        conflicts.sort(key=lambda entry: entry["path"])
        return {"conflicts": conflicts}
    return {"merged": merged, "conflicts": []}
