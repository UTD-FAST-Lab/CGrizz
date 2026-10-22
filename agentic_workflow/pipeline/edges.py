"""
pipeline/edges.py
=================
Matching helpers for call-graph edges and method names across the boundary,
static CG and execution-trace formats.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("pipeline")


def descriptor_to_classname(descriptor: str) -> str:
    """Convert a JVM class descriptor to a dotted class name.
    e.g. 'Lcom/example/App;' → 'com.example.App'
    Already-dotted names (no slashes) are returned as-is.
    """
    return descriptor.lstrip("L").rstrip(";").replace("/", ".")


def entry_matches(entry: dict, expected_caller: str, expected_callee: str) -> bool:
    """
    Check whether a structured boundary entry matches the expected caller and
    callee strings from TestCaseMissingEdge.

    Expected format:  'com.example.App.runParser()'
    Boundary format:  caller/missedCallee dicts with declaringClass + name fields.

    Matching is done by checking that both the normalized class name and the
    method name of the boundary entry appear in the expected string, so minor
    differences in parameter format do not cause false negatives.
    """
    caller  = entry.get("caller", {})
    callee  = entry.get("missedCallee", {})

    caller_class  = descriptor_to_classname(caller.get("declaringClass", ""))
    caller_method = caller.get("name", "")
    callee_class  = descriptor_to_classname(callee.get("declaringClass", ""))
    callee_method = callee.get("name", "")

    caller_match = caller_class in expected_caller and caller_method in expected_caller
    callee_match = callee_class in expected_callee and callee_method in expected_callee
    return caller_match and callee_match


def edge_in_boundary(
    boundary_path: str,
    expected_caller: str,
    expected_callee: str,
) -> tuple[bool, str]:
    """
    Parse the JSON file at boundary_path and check whether any entry structurally matches
    the expected caller → callee edge.

    Returns:
        (found: bool, feedback: str)
    """
    try:
        data = json.loads(Path(boundary_path).read_text())
        entries = data.get("boundaries", [])
    except Exception as e:
        log.warning(f"Failed reading boundary file {boundary_path}: {e}")
        return False, f"Could not read boundary file {boundary_path}: {e}"

    for entry in entries:
        if entry_matches(entry, expected_caller, expected_callee):
            caller = entry.get("caller", {})
            callee = entry.get("missedCallee", {})
            return True, (
                f"Edge confirmed in {Path(boundary_path).name}: "
                f"{descriptor_to_classname(caller.get('declaringClass','?'))}"
                f".{caller.get('name','?')} → "
                f"{descriptor_to_classname(callee.get('declaringClass','?'))}"
                f".{callee.get('name','?')}"
            )

    return False, (
        f"Edge not found in boundary output. "
        f"Expected: {expected_caller} → {expected_callee}. "
        f"Searched {Path(boundary_path).name}."
    )


def rc_in_trace(expected_method: str, trace_log_lines: list[str]) -> bool:
    
    ex_classname = expected_method.split("(")[0].rsplit(".", 1)[0] if "." in expected_method else None
    ex_methodname = expected_method.split("(")[0].rsplit(".", 1)[1] if "." in expected_method else None

    if expected_method and expected_method: 
        for line in trace_log_lines:
            if ex_classname in line and ex_methodname in line:
                return True
        return False
    else:
        log.warning("expected root cause method is not in expected format")
        return False


def is_reachable(expected_caller: str, staticcg_path: str) -> bool:
    """Check whether a caller method appears in the static call graph."""
    try:
        data = json.loads(Path(staticcg_path).read_text())
        reachable_methods = data.get("reachableMethods", [])
    except Exception as e:
        log.warning(f"Failed reading static CG file {staticcg_path}: {e}")
        return False

    for method in reachable_methods:
        caller = method.get("method", {})
        caller_class  = descriptor_to_classname(caller.get("declaringClass", ""))
        caller_method = caller.get("name", "")
        if caller_class in expected_caller and caller_method in expected_caller:
            return True

    log.warning(f"{expected_caller} is not reachable in the static call graph or the format is not correct")
    return False
