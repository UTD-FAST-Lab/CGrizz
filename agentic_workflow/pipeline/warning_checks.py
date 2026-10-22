"""
pipeline/warning_checks.py
==========================
Checks that the [UNSOUNDNESS] warning injected into the SA tool is specific to
the root-cause pattern (issue #4), on top of the trace check that it fires at
all:

  selectivity — on the reproducing test case the warning may fire for only a
                small share of the analysed (reachable) methods; a warning in
                a generic place fires for most of them.
  negative    — on the non-reproducing test case (same program minus the root
                cause) the warning must not fire for any application method.

Pure functions over trace lines and files; the stage runs the tool.
"""

import json
import re
from pathlib import Path
from typing import Optional

WARNING_TAG = "[UNSOUNDNESS]"
_CALLER = re.compile(r"caller=(.*?)\s*(?:\||$)")
_DESCRIPTION = re.compile(r"\[UNSOUNDNESS\]\s*(.*?)\s*\|\s*caller=")
_PLACEHOLDER = re.compile(r"<[^>]*>|pattern_class|TODO", re.IGNORECASE)
_FIELD = re.compile(r"\|\s*(\w+)=([^|]*)")


def warning_lines(trace_text: str) -> list[str]:
    return [line for line in trace_text.splitlines() if WARNING_TAG in line]


def warning_callers(lines: list[str]) -> list[str]:
    """Distinct caller= values, in order of first appearance."""
    seen: dict[str, None] = {}
    for line in lines:
        m = _CALLER.search(line)
        if m and m.group(1):
            seen.setdefault(m.group(1).strip(), None)
    return list(seen)


def warning_descriptions(lines: list[str]) -> list[str]:
    """Distinct problem descriptions printed between [UNSOUNDNESS] and '| caller='."""
    seen: dict[str, None] = {}
    for line in lines:
        m = _DESCRIPTION.search(line)
        seen.setdefault(m.group(1).strip() if m else "", None)
    return list(seen)


def _int(value: Optional[str]) -> Optional[int]:
    try:
        n = int(str(value).strip())
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def warning_records(lines: list[str]) -> list[dict]:
    """
    Parsed warnings: {description, caller, site, line, pc}. line / pc are None
    when missing or -1 (unknown).
    """
    out = []
    for raw in lines:
        if WARNING_TAG not in raw:
            continue
        fields = {k: v.strip() for k, v in _FIELD.findall(raw[raw.index(WARNING_TAG):])}
        m = _DESCRIPTION.search(raw)
        out.append({
            "description": m.group(1).strip() if m else "",
            "caller": fields.get("caller", ""),
            "site": fields.get("site", ""),
            "line": _int(fields.get("line")),
            "pc": _int(fields.get("pc")),
            "has_location_fields": "line" in fields and "pc" in fields,
        })
    return out


def check_location(lines: list[str]) -> tuple[bool, str]:
    """Every warning names the call instruction: line= and pc= present, at least one of them known."""
    records = warning_records(lines)
    missing = [r for r in records if not r["has_location_fields"]]
    unknown = [r for r in records if r["has_location_fields"] and r["line"] is None and r["pc"] is None]
    if not missing and not unknown:
        return True, ""
    what = ("has no `line=` / `pc=` fields" if missing else "prints line=-1 and pc=-1 (both unknown)")
    return False, (
        f"[annotation / trace check] Location check failed — the [UNSOUNDNESS] log {what}. Each warning must say "
        "which call instruction of the caller is affected, so it can be matched to a missed edge: "
        "[UNSOUNDNESS] <description> | caller=<method> | site=<site> | line=<source line> | pc=<bytecode offset>. "
        "Use -1 only for a value the tool really does not have, and at least one of the two must be known "
        "(see the per-tool hints in your instructions)."
    )


def check_description(lines: list[str], max_chars: int) -> tuple[bool, str]:
    """The warning must tell the tool's user, in a few words, what went wrong."""
    descriptions = warning_descriptions(lines)
    bad = [d for d in descriptions if not d or len(d) > max_chars or _PLACEHOLDER.search(d)]
    if not bad:
        return True, ""
    shown = bad[0] or "<nothing>"
    return False, (
        f"[annotation / trace check] Description check failed — the [UNSOUNDNESS] log printed the description {shown!r} "
        f"({len(bad[0])} characters). It must be a very short, plain statement of what the tool could not do, "
        f"at most {max_chars} characters, so a user of the tool understands the warning without reading the "
        f"report — e.g. \"Class.forName name is not a constant\" or \"Proxy class missing from class hierarchy\". "
        f"Keep the format: [UNSOUNDNESS] <description> | caller=<method> | site=<site>."
    )


def test_case_packages(test_case_dir: Path) -> set[str]:
    """Java packages declared by a test case's sources (src/main/java/...)."""
    pkgs = set()
    src = Path(test_case_dir) / "src" / "main" / "java"
    for f in src.rglob("*.java") if src.exists() else []:
        rel = f.parent.relative_to(src)
        if rel.parts:
            pkgs.add(".".join(rel.parts))
    return pkgs


def _in_packages(caller: str, pkgs: set[str]) -> bool:
    cls = caller.split("(")[0].rsplit(".", 1)[0] if "." in caller else caller
    return any(cls == p or cls.startswith(p + ".") for p in pkgs)


def reachable_method_count(staticcg_path: Optional[str]) -> int:
    try:
        return len(json.loads(Path(staticcg_path).read_text()).get("reachableMethods", []))
    except Exception:
        return 0


def check_selectivity(lines: list[str], staticcg_path: Optional[str], max_fraction: float) -> tuple[bool, str]:
    """The warning fires for at most max_fraction of the reachable methods."""
    callers = warning_callers(lines)
    reachable = reachable_method_count(staticcg_path)
    if not reachable or not callers:
        return True, ""
    fraction = len(callers) / reachable
    if fraction <= max_fraction:
        return True, ""
    sample = "\n".join(f"  {c}" for c in callers[:15])
    return False, (
        f"[annotation / trace check] Selectivity check failed — the [UNSOUNDNESS] log fired for {len(callers)} distinct callers, "
        f"{fraction:.0%} of the {reachable} methods the tool analysed (limit {max_fraction:.0%}). "
        f"It is placed on a path every (or most) call sites take, so it does not identify this pattern. "
        f"Move it behind the condition that is specific to the root cause (the branch where the tool gives "
        f"up for this pattern). Sample of callers:\n{sample}"
    )


def check_negative(non_repro_lines: list[str], non_repro_dir: Path) -> tuple[bool, str]:
    """The warning must not fire for application methods of the non-reproducing test case."""
    pkgs = test_case_packages(non_repro_dir)
    if not pkgs:
        return True, ""
    hits = [c for c in warning_callers(non_repro_lines) if _in_packages(c, pkgs)]
    if not hits:
        return True, ""
    sample = "\n".join(f"  {c}" for c in hits[:15])
    return False, (
        f"[annotation / trace check] Negative check failed — the [UNSOUNDNESS] log also fired on the NON-reproducing test case "
        f"({non_repro_dir}), which is the same program without the root cause, for these application "
        f"methods:\n{sample}\n"
        f"The tool resolves the edge there, so the log must not fire. Guard it with the condition that "
        f"distinguishes the root cause (compare the two test cases' sources)."
    )
