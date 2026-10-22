"""
pipeline/edge_mapping.py
========================
Maps [UNSOUNDNESS] warnings of an annotated SA tool to the boundaries (missed
edges) of the same program (issue #14):

  explained      a boundary at a call instruction the tool warned about
  boundary_only  missed edges without a warning — run-time-only causes the
                 annotation cannot see, or root causes not annotated yet
  warning_only   warnings at call instructions without a boundary — calls the
                 dynamic analysis did not exercise (or that the tool resolves
                 after all)

A warning and a boundary match when they name the same caller (class and
method name) and the same call instruction: the same pc when both know it,
else the same source line. Several boundaries can share one call instruction
(several missed callees), so counts are given for both.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from pipeline.edges import descriptor_to_classname
from pipeline.warning_checks import warning_lines, warning_records


def _split(method: str) -> tuple[str, str]:
    head = method.split("(")[0]
    return tuple(head.rsplit(".", 1)) if "." in head else ("", head)


def _num(v) -> Optional[int]:
    try:
        n = int(v)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def boundary_records(boundaries: list[dict]) -> list[dict]:
    out = []
    for b in boundaries:
        c, t = b.get("caller", {}), b.get("missedCallee", {})
        out.append({
            "caller_class": descriptor_to_classname(c.get("declaringClass", "")),
            "caller_method": c.get("name", ""),
            "callee": f"{descriptor_to_classname(t.get('declaringClass', ''))}.{t.get('name', '')}",
            "line": _num(b.get("line")),
            "pc": _num(b.get("pc")),
        })
    return out


def load_boundaries(path: str | Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        return []
    return data.get("boundaries", []) if isinstance(data, dict) else data if isinstance(data, list) else []


def _site(caller_class: str, caller_method: str, pc: Optional[int], line: Optional[int]) -> str:
    where = f"pc {pc}" if pc is not None else f"line {line}" if line is not None else "unknown instruction"
    return f"{caller_class}.{caller_method} @ {where}"


def _same_instruction(w: dict, b: dict) -> bool:
    if w["pc"] is not None and b["pc"] is not None:
        return w["pc"] == b["pc"]
    return w["line"] is not None and w["line"] == b["line"]


def map_warnings(warnings: list[dict], boundaries: list[dict]) -> dict:
    """warnings: warning_records(); boundaries: boundary_records()."""
    by_caller = defaultdict(list)
    for w in warnings:
        cls, m = _split(w["caller"])
        by_caller[(cls, m)].append(w)

    explained, boundary_only = [], []
    used: set[int] = set()
    for b in boundaries:
        hits = [w for w in by_caller.get((b["caller_class"], b["caller_method"]), []) if _same_instruction(w, b)]
        site = _site(b["caller_class"], b["caller_method"], b["pc"], b["line"])
        if hits:
            used.update(id(w) for w in hits)
            explained.append({"boundary": f"{site} → {b['callee']}", "description": hits[0]["description"]})
        else:
            boundary_only.append(f"{site} → {b['callee']}")

    warning_only = sorted({
        _site(*_split(w["caller"]), w["pc"], w["line"]) + f" ({w['description']})"
        for w in warnings if id(w) not in used
    })
    warned_sites = {(w["caller"], w["pc"], w["line"]) for w in warnings}
    return {
        "counts": {
            "boundaries": len(boundaries),
            "explained": len(explained),
            "boundary_only": len(boundary_only),
            "warned_instructions": len(warned_sites),
            "warning_only_instructions": len(warning_only),
        },
        "explained": explained,
        "boundary_only": boundary_only,
        "warning_only": warning_only,
    }


def map_files(trace_paths: list[str | Path], boundaries_path: str | Path) -> dict:
    lines: list[str] = []
    for p in trace_paths:
        try:
            lines += warning_lines(Path(p).read_text(errors="replace"))
        except FileNotFoundError:
            pass
    return map_warnings(warning_records(lines), boundary_records(load_boundaries(boundaries_path)))
