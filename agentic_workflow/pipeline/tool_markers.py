"""
pipeline/tool_markers.py
========================
Unsoundness-marker modules (issue #14): code written once per SA tool, in
tool_markers/<tool>/, that the annotation can use to warn at every call a lost
value reaches ([UNSOUNDNESS-EDGE]), not only at the root cause.

tool_markers/<tool>/marker.json lists the module's files (copied into the tool
at the same relative path) and anchored edits that hook it into the tool. An
anchor must occur exactly once; installing twice changes nothing.
"""

import json
import shutil
from pathlib import Path
from typing import Optional

MARKERS_DIR = Path(__file__).resolve().parent.parent / "tool_markers"


class ToolMarkerError(Exception):
    pass


def spec(tool_name: str) -> Optional[dict]:
    p = MARKERS_DIR / tool_name.lower() / "marker.json"
    return json.loads(p.read_text()) if p.exists() else None


def usage_file(tool_name: str) -> Optional[Path]:
    s = spec(tool_name)
    p = MARKERS_DIR / s["usage"] if s and s.get("usage") else None
    return p if p and p.exists() else None


def install(tool_name: str, tool_dir: str | Path) -> list[str]:
    """Copy the module into tool_dir and apply its edits. Returns the changed files."""
    s = spec(tool_name)
    if s is None:
        return []
    tool_dir = Path(tool_dir)
    src_root = MARKERS_DIR / tool_name.lower()
    changed = []
    for rel in s.get("files", []):
        dest = tool_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_root / rel, dest)
        changed.append(rel)
    return list(dict.fromkeys(changed + apply_edits(s.get("edits", []), tool_dir, f"{tool_name} marker")))


def apply_edits(edits: list[dict], tool_dir: str | Path, what: str = "edit") -> list[str]:
    """Anchored edits: {file, anchor, insert: before|after|replace, text}. Already applied → skipped."""
    tool_dir, changed = Path(tool_dir), []
    for e in edits:
        path = tool_dir / e["file"]
        if not path.exists():
            raise ToolMarkerError(f"{what}: {e['file']} not found in {tool_dir}")
        text = path.read_text()
        if e["text"] in text:
            continue
        count = text.count(e["anchor"])
        if count != 1:
            raise ToolMarkerError(f"{what}: anchor found {count} times in {e['file']} (expected once) — "
                                  "the tool version differs from the one this was written for")
        new = {"before": e["text"] + e["anchor"], "after": e["anchor"] + e["text"], "replace": e["text"]}[e["insert"]]
        path.write_text(text.replace(e["anchor"], new))
        changed.append(e["file"])
    return list(dict.fromkeys(changed))
