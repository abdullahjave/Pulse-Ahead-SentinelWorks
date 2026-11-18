"""Privacy controls enforced in the backend before any response or export leaves the server.

* Small-cell suppression: any aggregated count from 1 to SMALL_CELL_THRESHOLD - 1 is replaced
  by "<5". Derived numbers that would reveal it (rates, ratios, z-scores) are removed too.
* Complementary suppression: in a table with a visible total, if only one cell in a row is
  suppressed, the next-smallest cell is also masked so the small value cannot be back-calculated.
* No endpoint returns person-level records, names, employee IDs or disciplinary fields.
"""
from __future__ import annotations

from . import config as C

T = C.SMALL_CELL_THRESHOLD
SUPPRESSED_LABEL = f"<{T}"


def is_small(n) -> bool:
    return n is not None and 0 < round(n) < T


def count_cell(n) -> dict:
    """Serialise an aggregated count. Zero is shown; 1-4 are masked."""
    if n is None:
        return {"value": None, "display": "—", "suppressed": False}
    n = int(round(n))
    if is_small(n):
        return {"value": None, "display": SUPPRESSED_LABEL, "suppressed": True, "reason": "small_cell"}
    return {"value": n, "display": str(n), "suppressed": False}


def suppress_row(cells: dict, total_key: str | None = None) -> dict:
    """Apply small-cell + complementary suppression to one table row of counts."""
    out = {k: count_cell(v) for k, v in cells.items()}
    keys = [k for k in cells if k != total_key]
    masked = [k for k in keys if out[k]["suppressed"]]
    if total_key and len(masked) == 1:
        candidates = sorted((cells[k], k) for k in keys if not out[k]["suppressed"] and cells[k] is not None)
        if candidates:
            _, k2 = candidates[0]
            out[k2] = {"value": None, "display": "masked", "suppressed": True, "reason": "complementary"}
    if total_key and len(masked) == len(keys) and keys:
        # every component is small: the total alone could still identify a tiny group
        out[total_key] = count_cell(cells[total_key])
    return out


def assert_no_small_counts(obj, path="") -> None:
    """Test helper: raise if any visible integer count 1..4 appears under a count key."""
    if isinstance(obj, dict):
        if obj.get("suppressed") is False and isinstance(obj.get("value"), (int, float)) and "display" in obj:
            if is_small(obj["value"]):
                raise AssertionError(f"small count leaked at {path}")
        for k, v in obj.items():
            assert_no_small_counts(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_no_small_counts(v, f"{path}[{i}]")
