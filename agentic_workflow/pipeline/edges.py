"""
pipeline/edges.py
=================
Matching helpers for call-graph edges and method names across the boundary,
static CG and execution-trace formats.
"""

import json
import re
import logging
from pathlib import Path
from typing import Optional

from config import JVM_RUNTIME_EXCLUDED_CALLEES

log = logging.getLogger("pipeline")


def descriptor_to_classname(descriptor: str) -> str:
    """Convert a JVM class descriptor to a dotted class name.
    e.g. 'Lcom/example/App;' → 'com.example.App'
    Already-dotted names (no slashes) are returned as-is.
    """
    return descriptor.lstrip("L").rstrip(";").replace("/", ".")


def is_jvm_runtime_boundary(boundary: dict) -> bool:
    """
    The missed callee is called by the JVM itself (class loading, finalization, <clinit>, ...),
    not by the program's code at the call site, so no tool warning can be expected for it.
    """
    missed = boundary.get("missedCallee", {})
    m_class, m_name = missed.get("declaringClass", ""), missed.get("name", "")
    return any((cls is None or m_class == cls) and (method is None or m_name == method)
               for cls, method in JVM_RUNTIME_EXCLUDED_CALLEES) or bool(re.search(r'/\d+;?$', m_class))


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


def edge_in_cg(cg_path: str, expected_caller: str, expected_callee: str) -> Optional[bool]:
    """
    Is caller → callee an edge of a JCG call graph (reachableMethods[].callSites[].targets[])?
    None if the file cannot be read or has another shape — callers then say "unknown".
    Matching is the same substring match as entry_matches.
    """
    try:
        methods = json.loads(Path(cg_path).read_text()).get("reachableMethods", [])
    except Exception:
        return None
    saw_call_sites = False
    for m in methods:
        caller = m.get("method", {})
        if not (descriptor_to_classname(caller.get("declaringClass", "")) in expected_caller
                and caller.get("name", "") in expected_caller):
            continue
        for site in m.get("callSites", []) or []:
            saw_call_sites = True
            for t in site.get("targets", []) or []:
                if (descriptor_to_classname(t.get("declaringClass", "")) in expected_callee
                        and t.get("name", "") in expected_callee):
                    return True
    return False if saw_call_sites or methods else None


def cg_edges(cg_path: str) -> Optional[set[str]]:
    """All edges of a JCG call graph as 'caller @line/pc -> target' strings; None if unreadable."""
    try:
        methods = json.loads(Path(cg_path).read_text()).get("reachableMethods", [])
    except Exception:
        return None

    def name(m: dict) -> str:
        return (f"{descriptor_to_classname(m.get('declaringClass', ''))}.{m.get('name', '')}"
                f"({','.join(m.get('parameterTypes', []))})")
    edges = set()
    for rm in methods:
        caller = name(rm.get("method", {}))
        # A synthetic root (WALA's FakeRootClass) numbers its call sites in discovery order.
        synthetic = "FakeRootClass" in caller
        for site in rm.get("callSites", []) or []:
            at = "synthetic" if synthetic else f"{site.get('line')}/{site.get('pc')}"
            for t in site.get("targets", []) or []:
                edges.add(f"{caller} @{at} -> {name(t)}")
    return edges


def boundary_edges(boundary_path: str, limit: int = 10) -> list[str]:
    """'Caller.m → Callee.n' for the first missed edges in a boundaries.json."""
    try:
        entries = json.loads(Path(boundary_path).read_text()).get("boundaries", [])
    except Exception:
        return []
    out = []
    for e in entries[:limit]:
        c, t = e.get("caller", {}), e.get("missedCallee", {})
        out.append(f"{descriptor_to_classname(c.get('declaringClass', '?'))}.{c.get('name', '?')} → "
                   f"{descriptor_to_classname(t.get('declaringClass', '?'))}.{t.get('name', '?')}")
    return out


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


def _class_and_name(method: str) -> tuple[str, str]:
    """'pkg.Cls.m(desc)' → ('pkg.Cls', 'm')."""
    head = method.split("(")[0]
    return tuple(head.rsplit(".", 1)) if "." in head else ("", head)


def method_reachable(method: str, staticcg_path: str) -> Optional[bool]:
    """Whether the method (exact class and name) is reachable in the call graph; None if unreadable."""
    try:
        data = json.loads(Path(staticcg_path).read_text())
    except Exception:
        return None
    cls, name = _class_and_name(method)
    return any(descriptor_to_classname(m.get("method", {}).get("declaringClass", "")) == cls
               and m.get("method", {}).get("name") == name for m in data.get("reachableMethods", []))


REFLECTION_ACCESSOR_PREFIXES = ("sun.reflect.", "jdk.internal.reflect.")


def reflective_edge_in_boundary(boundary_path: str, callee: str) -> bool:
    """A missed edge to callee from a JDK reflection accessor (how call graphs show reflective calls)."""
    cls, name = _class_and_name(callee)
    try:
        entries = json.loads(Path(boundary_path).read_text()).get("boundaries", [])
    except Exception:
        return False
    return any(descriptor_to_classname(e.get("caller", {}).get("declaringClass", "")).startswith(REFLECTION_ACCESSOR_PREFIXES)
               and descriptor_to_classname(e.get("missedCallee", {}).get("declaringClass", "")) == cls
               and e.get("missedCallee", {}).get("name") == name for e in entries)
