"""
pipeline/sampler.py
===================
Boundary sampler: runs once per (program, tool, algorithm) triple,
before any per-boundary pipelines are spawned.

Reads the full boundaries list, sends compact summaries to a fast model,
and returns the subset worth analyzing — filtered for uniqueness, impact,
and trace diversity.

Also provides run_global_dedup_sampler() for cross-program deduplication
after all per-program sampling is done.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from config import MODEL_FAST, OUT_SELECTED_BOUNDARIES, OUT_GLOBAL_SELECTED_BOUNDARIES, JVM_RUNTIME_EXCLUDED_CALLEES
from pipeline.state import PipelineState

log = logging.getLogger("pipeline.sampler")


# ── Library class detection ───────────────────────────────────────────────────

_LIBRARY_PREFIXES = (
    "Ljava/", "Ljavax/", "Lsun/", "Ljdk/",
    "Lcom/sun/", "Lorg/xml/", "Lorg/w3c/", "Lorg/ietf/",
)


def _is_application_class(cls: str) -> bool:
    return bool(cls) and not any(cls.startswith(p) for p in _LIBRARY_PREFIXES)


def _has_application_edge(state: PipelineState) -> bool:
    b = state.boundary
    return (
        _is_application_class(b.get("caller", {}).get("declaringClass", ""))
        and _is_application_class(b.get("missedCallee", {}).get("declaringClass", ""))
    )


# ── JVM-runtime pre-filter ────────────────────────────────────────────────────

def filter_jvm_runtime_boundaries(
    boundaries: list[dict],
) -> tuple[list[dict], list[int]]:
    """
    Remove boundaries whose missedCallee is dispatched by the JVM at runtime
    (GC finalization, class loading, security checks, etc.).

    Returns (filtered_boundaries, original_indices) so callers can preserve
    the original file indices in output directory names after filtering.
    """
    kept: list[dict] = []
    kept_indices: list[int] = []
    excluded = 0

    for i, b in enumerate(boundaries):
        missed  = b.get("missedCallee", {})
        m_name  = missed.get("name", "")
        m_class = missed.get("declaringClass", "")

        should_exclude = any(
            m_name == method and (cls is None or m_class == cls)
            for cls, method in JVM_RUNTIME_EXCLUDED_CALLEES
        )

        if should_exclude:
            excluded += 1
            log.info(f"Pre-filter: excluded boundary {i} ({m_class}.{m_name})")
        else:
            kept.append(b)
            kept_indices.append(i)

    if excluded:
        log.info(f"Pre-filter: removed {excluded}/{len(boundaries)} JVM-runtime boundaries.")

    return kept, kept_indices


# ── Compact summaries ─────────────────────────────────────────────────────────

def _boundary_compact(b: dict) -> str:
    """One-block text summary of a boundary dict for use in the per-program sampler prompt."""
    caller  = b.get("caller", {})
    missed  = b.get("missedCallee", {})
    impact  = b.get("impact", {})
    static  = b.get("staticCalleesAtSite", [])
    traces  = b.get("tracesLeadingToBoundary", [])

    params   = ", ".join(caller.get("parameterTypes", []))
    ret      = caller.get("returnType", "?")
    caller_s = f"{caller.get('declaringClass', '?')}.{caller.get('name', '?')}({params}) → {ret}"
    missed_s = f"{missed.get('declaringClass', '?')}.{missed.get('name', '?')}"

    static_s = "none"
    if static:
        first = f"{static[0].get('declaringClass', '?')}.{static[0].get('name', '?')}"
        extra = f" (+ {len(static) - 1} more)" if len(static) > 1 else ""
        static_s = first + extra

    return (
        f"Caller   : {caller_s}\n"
        f"Missed   : {missed_s}\n"
        f"Impact   : {impact.get('notCovered', '?')}/{impact.get('totalReachable', '?')} not covered\n"
        f"Traces   : {len(traces)}\n"
        f"StaticSaw: {static_s}"
    )


def _boundary_cross_compact(state: PipelineState, rationale: str) -> str:
    """Compact summary for cross-program dedup — caller and missedCallee only."""
    b      = state.boundary
    caller = b.get("caller", {})
    missed = b.get("missedCallee", {})

    params   = ", ".join(caller.get("parameterTypes", []))
    ret      = caller.get("returnType", "?")
    caller_s = f"{caller.get('declaringClass', '?')}.{caller.get('name', '?')}({params}) → {ret}"
    missed_s = f"{missed.get('declaringClass', '?')}.{missed.get('name', '?')}"

    return (
        f"program={state.program_name}\n"
        f"Caller   : {caller_s}\n"
        f"Missed   : {missed_s}\n"
        f"Rationale: {rationale or '(none)'}"
    )


# ── Per-program sampler ───────────────────────────────────────────────────────

async def run_boundary_sampler(
    boundaries:     list[dict],
    program_name:   str,
    tool_name:      str,
    algorithm:      str,
    output_dir:     Path,
    max_boundaries: int | None = None,
) -> tuple[list[dict], list[int], dict]:
    """
    Select a representative, high-value subset of boundaries to analyze.

    Args:
        boundaries:     Full list of boundary entries from boundaries.json.
        program_name:   e.g. "program_A"
        tool_name:      e.g. "WALA"
        algorithm:      e.g. "0-CFA"
        output_dir:     Directory to write selected_boundaries.json into.
                        Scoped to {base}/{program}/{tool}/{algorithm}/.
        max_boundaries: Optional hard cap on selections. None = no cap.

    Returns:
        (selected_boundaries, original_indices, rationale)
        Falls back to the full list if the agent fails or returns empty.
    """
    n = len(boundaries)
    log.info(f"[{program_name}/{tool_name}/{algorithm}] Sampler: {n} boundaries to evaluate.")

    cap_line = f"\nSelect at most {max_boundaries} boundaries.\n" if max_boundaries else ""

    numbered = "\n\n".join(
        f"[{i}]\n{_boundary_compact(b)}"
        for i, b in enumerate(boundaries)
    )

    prompt = f"""\
You are given {n} boundary entries from a static analysis tool ({tool_name} / {algorithm}) \
on program '{program_name}'.

Your task: select the subset most worth analyzing for unsoundness root causes.

Apply these criteria in order:
1. **Uniqueness** — if multiple boundaries share the same caller class AND the same \
kind of missed callee (e.g. all are AbstractX → ConcreteX polymorphism misses), \
keep only the highest-impact representative.
2. **Application Edges** — prefer boundaries with either application callers or application callees otherwise libraries.
2. **Impact** — prefer higher notCovered/totalReachable ratios.
3. **Trace diversity** — if two boundaries share a caller but have different \
tracesLeadingToBoundary counts, keep both (different traces imply different root causes).
{cap_line}
Boundary entries:

{numbered}

Return JSON only:
{{
  "selected": [0, 2, ...],
  "rationale": {{
    "0": "<one sentence why this entry was kept>",
    "2": "<one sentence why this entry was kept>"
  }}
}}"""

    options = ClaudeAgentOptions(
        model=MODEL_FAST,
        allowed_tools=[],
        setting_sources=["user", "project"],
        cwd=str(Path(__file__).parent.parent),
    )

    collected = []
    final_result: Optional[str] = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage):
            continue

        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "type") and block.type == "text":
                    collected.append(block.text)

        elif isinstance(message, ResultMessage):
            if message.result:
                final_result = message.result
            log.debug(
                f" Result metadata — "
                f"stop_reason={message.stop_reason}, "
                f"turns={message.num_turns}"
            )

    raw = "\n".join(collected) or (final_result or "")
    log.info(f"[{program_name}/{tool_name}/{algorithm}] Sampler response: {len(raw)} chars")

    selected_indices, rationale = _parse_sampler_response(raw, n)

    if not selected_indices:
        log.warning(
            f"[{program_name}/{tool_name}/{algorithm}] "
            "Sampler returned empty selection — falling back to all boundaries."
        )
        selected_indices = list(range(n))
        rationale = {}

    selected_boundaries = [boundaries[i] for i in selected_indices]

    _write_sampler_output(
        output_dir=output_dir,
        source_count=n,
        selected_boundaries=selected_boundaries,
        selected_indices=selected_indices,
        rationale=rationale,
    )

    log.info(
        f"[{program_name}/{tool_name}/{algorithm}] "
        f"Sampler selected {len(selected_indices)}/{n}: {selected_indices}"
    )
    return selected_boundaries, selected_indices, rationale


# ── Cross-program dedup sampler ───────────────────────────────────────────────

async def run_global_dedup_sampler(
    states:                list[PipelineState],
    per_program_rationale: dict[int, str],
    output_dir:            Path,
) -> list[PipelineState]:
    """
    Cross-program deduplication pass.

    After per-program sampling, identifies boundaries from different programs
    that share the same unsoundness root cause and keeps one representative.

    Boundaries with application edges are always kept without consulting the LLM.
    Only caller + missedCallee are sent to the model — traces and impact are
    program-specific and inconsistent across programs.
    Pass-1 per-program rationale is included to give the LLM richer signal.

    Returns the deduplicated list of PipelineState objects (structure preserved).
    Falls back to the full states list if the LLM fails or returns empty.
    """
    n = len(states)
    log.info(f"[global] Cross-program dedup: {n} boundaries across programs.")

    if n <= 1:
        return states

    # Partition: application edges are always kept; library-only go to LLM
    forced_states:           list[PipelineState] = []
    candidates:              list[PipelineState] = []
    candidate_global_indices: list[int]          = []

    for global_idx, state in enumerate(states):
        if _has_application_edge(state):
            forced_states.append(state)
        else:
            candidates.append(state)
            candidate_global_indices.append(global_idx)

    log.info(
        f"[global] Application edges forced-kept: {len(forced_states)}, "
        f"candidates for dedup: {len(candidates)}"
    )

    if len(candidates) <= 1:
        return forced_states + candidates

    nc = len(candidates)
    numbered = "\n\n".join(
        f"[{i}]\n{_boundary_cross_compact(candidates[i], per_program_rationale.get(candidate_global_indices[i], ''))}"
        for i in range(nc)
    )

    ta_pairs = sorted({(s.tool_name, s.algorithm) for s in candidates})
    ta_str   = ", ".join(f"{t}/{a}" for t, a in ta_pairs)

    prompt = f"""\
You are given {nc} boundary entries collected across multiple programs analyzed with {ta_str}. \
Each entry describes a call site where the static analysis tool missed a callee.

Your task: identify entries that represent the same unsoundness root cause — same kind of \
caller pattern AND same kind of missed callee — and keep only one representative per pattern.

Apply these criteria in order:
1. **Uniqueness** — if multiple entries share the same caller method name pattern AND the same \
kind of missed callee (e.g. both are virtual dispatch misses to a concrete subclass named similarly), \
keep only the best representative. Use the Rationale field to judge which is more informative.
2. **Distinct patterns** — entries with different call mechanisms (e.g. one is a reflective call miss, \
another is a polymorphism miss) must both be kept even if class names look similar.
3. Do NOT use impact ratios or trace counts — these are program-specific and not comparable across programs.

Boundary entries:

{numbered}

Return JSON only:
{{
  "selected": [0, 2, ...],
  "rationale": {{
    "0": "<one sentence why this entry was kept or chosen as representative>",
    "2": "<one sentence why this entry was kept or chosen as representative>"
  }}
}}"""

    options = ClaudeAgentOptions(
        model=MODEL_FAST,
        allowed_tools=[],
        setting_sources=["user", "project"],
        cwd=str(Path(__file__).parent.parent),
    )

    collected = []
    final_result: Optional[str] = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, SystemMessage):
            continue
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "type") and block.type == "text":
                    collected.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.result:
                final_result = message.result
            log.debug(
                f"[global] Result metadata — "
                f"stop_reason={message.stop_reason}, "
                f"turns={message.num_turns}"
            )

    raw = "\n".join(collected) or (final_result or "")
    log.info(f"[global] Dedup response: {len(raw)} chars")

    selected_indices, rationale = _parse_sampler_response(raw, nc)

    if not selected_indices:
        log.warning("[global] Dedup returned empty selection — keeping all candidates.")
        selected_indices = list(range(nc))
        rationale = {}

    selected_candidates = [candidates[i] for i in selected_indices]
    final_states = forced_states + selected_candidates

    _write_global_dedup_output(
        output_dir=output_dir,
        total_input=n,
        forced_states=forced_states,
        selected_candidates=selected_candidates,
        selected_candidate_indices=selected_indices,
        rationale=rationale,
    )

    log.info(
        f"[global] Dedup: {len(final_states)}/{n} kept "
        f"({len(forced_states)} forced app-edges + {len(selected_candidates)} from LLM)"
    )
    return final_states


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sampler_response(response: str, n: int) -> tuple[list[int], dict]:
    """
    Extract selected indices and rationale from the agent response.
    Returns ([], {}) on any parse error.
    """
    def _try_parse(text: str) -> dict | None:
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return None

    data = _try_parse(response)
    if not data or "selected" not in data:
        log.warning("Sampler: could not parse JSON from agent response.")
        return [], {}

    raw_indices = data.get("selected", [])
    rationale   = {str(k): v for k, v in data.get("rationale", {}).items()}

    valid = []
    for idx in raw_indices:
        if isinstance(idx, int) and 0 <= idx < n:
            valid.append(idx)
        else:
            log.warning(f"Sampler: ignoring out-of-range index {idx!r} (n={n})")

    return valid, rationale


def _write_sampler_output(
    output_dir:          Path,
    source_count:        int,
    selected_boundaries: list[dict],
    selected_indices:    list[int],
    rationale:           dict,
) -> None:
    """Write selected_boundaries.json to the analysis-level output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    enriched = []
    for orig_idx, boundary in zip(selected_indices, selected_boundaries):
        entry = {"original_index": orig_idx, "rationale": rationale.get(str(orig_idx), "")}
        entry.update(boundary)
        enriched.append(entry)

    out = {
        "source_boundaries_count": source_count,
        "selected_count":          len(selected_indices),
        "sampler_model":           MODEL_FAST,
        "boundaries":              enriched,
    }

    out_path = output_dir / OUT_SELECTED_BOUNDARIES
    out_path.write_text(json.dumps(out, indent=2))
    log.info(f"Sampler output → {out_path}")


def _write_global_dedup_output(
    output_dir:              Path,
    total_input:             int,
    forced_states:           list[PipelineState],
    selected_candidates:     list[PipelineState],
    selected_candidate_indices: list[int],
    rationale:               dict,
) -> None:
    """Write global_selected_boundaries.json at the base output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_entries = []
    for state in forced_states:
        selected_entries.append({
            "program":     state.program_name,
            "boundary_id": state.boundary_id,
            "kept_reason": "application_edge",
            "rationale":   "",
        })
    for local_idx, state in zip(selected_candidate_indices, selected_candidates):
        selected_entries.append({
            "program":     state.program_name,
            "boundary_id": state.boundary_id,
            "kept_reason": "selected_by_dedup",
            "rationale":   rationale.get(str(local_idx), ""),
        })

    out = {
        "total_input":    total_input,
        "forced_kept":    len(forced_states),
        "selected_count": len(forced_states) + len(selected_candidates),
        "dedup_model":    MODEL_FAST,
        "selected":       selected_entries,
    }

    out_path = output_dir / OUT_GLOBAL_SELECTED_BOUNDARIES
    out_path.write_text(json.dumps(out, indent=2))
    log.info(f"[global] Dedup output → {out_path}")
