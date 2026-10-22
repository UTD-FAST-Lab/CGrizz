"""
pipeline/report_schema.py
=========================
The unsoundness report format (written by fn-edge-analyzer) and its validation.

The report carries three things: the evidence in the target program, the tool
limitation (with the tool code that drops the edge) and the minimal test case.
Validation checks the structure only — never which root cause it is, and there
is no root-cause category — so new root causes are reported as freely as known ones. It also checks that every
tool location in toolCodeEvidence.relevantFiles is "path:line" and exists in
the SA tool source, which makes reports comparable across runs (#5).
"""

import json
import re
from pathlib import Path
from typing import Optional

# key → expected type; nested dicts describe nested objects.
_SCHEMA = {
    "edgeId": str,
    "caller": str,
    "target": str,
    "evidence": list,
    "toolLimitation": {
        "reason": str,
        "toolCodeEvidence": {"relevantFiles": list, "codeSnippet": str},
    },
    "potentialTestCase": {
        "description": str,
        "TestCaseMissingEdge": {"caller": str, "callee": str},
    },
}
_OPTIONAL = {"callerLine"}
_LOCATION = re.compile(r"^(?P<path>[^:\s]+):(?P<line>\d+)")


def tool_location(entry: str) -> Optional[tuple[str, int]]:
    """'path/File.java:98 — note' → ('path/File.java', 98); None if not a location."""
    m = _LOCATION.match(entry.strip()) if isinstance(entry, str) else None
    return (m.group("path"), int(m.group("line"))) if m else None


def _resolve(path: str, tool_dir: Path) -> Optional[Path]:
    """A relevantFiles path, relative to the tool root or with a leading tool-dir prefix."""
    p = Path(path)
    if p.is_absolute():
        return p if p.exists() else None
    parts = p.parts
    for i in range(len(parts)):  # tolerate prefixes such as "tools/wala/" or "tool-src/"
        candidate = tool_dir.joinpath(*parts[i:])
        if candidate.exists():
            return candidate
    return None


def _check(obj: dict, schema: dict, where: str, problems: list[str]) -> None:
    for key, kind in schema.items():
        path = f"{where}{key}"
        if key not in obj:
            problems.append(f"missing field `{path}`")
        elif isinstance(kind, dict):
            if not isinstance(obj[key], dict):
                problems.append(f"`{path}` must be an object")
            else:
                _check(obj[key], kind, path + ".", problems)
        elif not isinstance(obj[key], kind):
            problems.append(f"`{path}` must be a {kind.__name__}")
    allowed = set(schema) | (_OPTIONAL if not where else set())
    for key in obj:
        if key not in allowed:
            problems.append(f"unexpected field `{where}{key}` (do not add fields)")


def validate(report: object, tool_dir: Optional[Path] = None) -> list[str]:
    """Problems with a parsed report; [] if it is valid."""
    if not isinstance(report, dict):
        return ["the report must be a JSON object"]
    problems: list[str] = []
    _check(report, _SCHEMA, "", problems)
    if "callerLine" in report and not isinstance(report["callerLine"], int):
        problems.append("`callerLine` must be an integer")

    evidence = report.get("evidence")
    if isinstance(evidence, list):
        if not evidence:
            problems.append("`evidence` must list at least one program location")
        elif not all(isinstance(e, str) and e.strip() for e in evidence):
            problems.append("`evidence` entries must be non-empty strings (\"path:line — what it shows\")")

    limitation = report.get("toolLimitation")

    files = (limitation.get("toolCodeEvidence") or {}).get("relevantFiles") if isinstance(limitation, dict) else None
    if isinstance(files, list):
        if not files:
            problems.append("`toolLimitation.toolCodeEvidence.relevantFiles` must list at least one tool location")
        for entry in files:
            loc = tool_location(entry)
            if loc is None:
                problems.append(f"relevantFiles entry {entry!r} must start with \"path/to/File.java:<line>\"")
            elif tool_dir is not None and _resolve(loc[0], Path(tool_dir)) is None:
                problems.append(f"relevantFiles entry {entry!r}: {loc[0]} does not exist in the SA tool source {tool_dir}")
    return problems


def validate_file(path: str | Path, tool_dir: Optional[Path] = None) -> list[str]:
    try:
        return validate(json.loads(Path(path).read_text()), tool_dir)
    except FileNotFoundError:
        return [f"{path} does not exist"]
    except Exception as e:
        return [f"{path} is not valid JSON: {e}"]
