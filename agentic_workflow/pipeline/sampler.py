"""
pipeline/sampler.py
===================
Boundary sampler: runs once per (program, tool, algorithm) triple,
before any per-boundary pipelines are spawned.

Reads the full boundaries list, sends compact summaries to a fast model,
and returns the subset worth analyzing — filtered for uniqueness, impact,
and trace diversity.

Also provides run_incremental_representative_filter() for scalable
cross-program deduplication: processes one program at a time against a
persistent representatives_registry.json, so the LLM never sees more
than (existing reps + one program's boundaries) at once.

run_global_dedup_sampler() is deprecated; use the incremental filter instead.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    query,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from config import MODEL_FAST, OUT_SELECTED_BOUNDARIES, OUT_GLOBAL_SELECTED_BOUNDARIES, OUT_REPRESENTATIVES_REGISTRY, JVM_RUNTIME_EXCLUDED_CALLEES
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


# ── JDK-caller pre-filter ────────────────────────────────────────────────────

def filter_jdk_caller_boundaries(
    boundaries: list[dict],
) -> tuple[list[dict], list[int]]:
    """
    Remove boundaries where the caller is a JDK/library class.

    This drops both jdk→jdk and jdk→application edges, keeping only
    boundaries where the caller belongs to application code.

    Returns (filtered_boundaries, original_indices) so callers can preserve
    the original file indices in output directory names after filtering.
    """
    kept: list[dict] = []
    kept_indices: list[int] = []
    excluded = 0

    for i, b in enumerate(boundaries):
        caller_class = b.get("caller", {}).get("declaringClass", "")
        if any(caller_class.startswith(p) for p in _LIBRARY_PREFIXES):
            excluded += 1
            log.info(f"Pre-filter: excluded JDK-caller boundary {i} ({caller_class})")
        else:
            kept.append(b)
            kept_indices.append(i)

    if excluded:
        log.info(f"Pre-filter: removed {excluded}/{len(boundaries)} JDK-caller boundaries.")

    return kept, kept_indices


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
    log.warning(
        "run_global_dedup_sampler is deprecated; "
        "use run_incremental_representative_filter instead."
    )
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


# ── Incremental cross-program registry ───────────────────────────────────────

def load_representatives_registry(registry_path: Path) -> dict:
    """
    Load an existing representatives_registry.json, or return an empty
    registry structure if the file does not exist or is corrupt (fail-open).
    """
    empty = {
        "schema_version":         "1",
        "created_at":             datetime.now().isoformat(),
        "last_updated_at":        datetime.now().isoformat(),
        "programs_processed":     [],
        "total_representatives":  0,
        "total_similar_recorded": 0,
        "representatives":        [],
    }
    if not registry_path.exists():
        log.info(f"Registry not found at {registry_path} — starting fresh.")
        return empty
    try:
        data = json.loads(registry_path.read_text())
        log.info(
            f"Registry loaded from {registry_path}: "
            f"{data.get('total_representatives', 0)} reps, "
            f"{len(data.get('programs_processed', []))} programs processed."
        )
        return data
    except json.JSONDecodeError:
        log.warning(f"Registry at {registry_path} is corrupted — starting fresh.")
        return empty


def save_representatives_registry(registry_path: Path, registry: dict) -> None:
    """
    Persist the registry to disk, recomputing derived counts first.
    """
    registry["last_updated_at"]       = datetime.now().isoformat()
    registry["total_representatives"] = len(registry["representatives"])
    registry["total_similar_recorded"] = sum(
        len(r.get("similar_edges", [])) for r in registry["representatives"]
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2))
    log.info(
        f"Registry saved → {registry_path} "
        f"({registry['total_representatives']} reps, "
        f"{registry['total_similar_recorded']} similar recorded)"
    )


async def run_incremental_representative_filter(
    new_states:            list[PipelineState],
    registry:              dict,
    per_program_rationale: dict[int, str],
    output_dir:            Path,
) -> tuple[list[PipelineState], dict]:
    """
    Incremental cross-program deduplication against an existing registry.

    For each boundary in new_states (from a single new program):
    - Application edges (both caller and missedCallee are app classes): always
      added to the registry as new representatives and passed through to the pipeline.
    - Library-only edges: compared via LLM against existing registry reps.
      If similar to an existing rep: recorded in that rep's similar_edges list,
      excluded from the pipeline.
      If novel: added as a new representative, included in the pipeline.

    Falls back to passing all candidates through as new representatives if the
    LLM call fails or returns unparseable JSON (fail-open).

    Args:
        new_states:            States from ONE program (post per-program sampler).
        registry:              Current registry dict. Mutated in-place; caller must
                               call save_representatives_registry() afterwards.
        per_program_rationale: Maps index-in-new_states → per-program sampler rationale.
        output_dir:            Used only for logging context; no file is written here.

    Returns:
        (states_to_run, registry) — states_to_run is the subset that should enter
        the pipeline; registry is the same object, updated in-place.
    """
    if not new_states:
        return [], registry

    program_label = (
        f"{new_states[0].program_name}/"
        f"{new_states[0].tool_name}/"
        f"{new_states[0].algorithm}"
    )
    log.info(f"[{program_label}] Incremental filter: {len(new_states)} boundaries.")

    # Partition: application edges forced-kept; library-only go to LLM
    forced:     list[tuple[int, PipelineState]] = []
    candidates: list[tuple[int, PipelineState]] = []

    for local_idx, state in enumerate(new_states):
        if _has_application_edge(state):
            forced.append((local_idx, state))
        else:
            candidates.append((local_idx, state))

    log.info(
        f"[{program_label}] Application edges (forced): {len(forced)}, "
        f"candidates for LLM dedup: {len(candidates)}"
    )

    # Snapshot reps from prior programs before we mutate the registry
    prior_reps = list(registry["representatives"])

    # Register forced states as new reps unconditionally
    for local_idx, state in forced:
        rep = _make_lean_rep_entry(
            state,
            per_program_rationale.get(local_idx, ""),
            "application_edge",
            len(registry["representatives"]),
        )
        registry["representatives"].append(rep)
        log.info(f"[{program_label}] New rep (app-edge): {rep['rep_id']} = {state.boundary_id}")

    states_to_run: list[PipelineState] = [state for _, state in forced]

    # Early exit: nothing to compare, or no prior reps exist yet
    if not candidates or not prior_reps:
        for local_idx, state in candidates:
            rep = _make_lean_rep_entry(
                state,
                per_program_rationale.get(local_idx, ""),
                "novel_by_llm",
                len(registry["representatives"]),
            )
            registry["representatives"].append(rep)
            log.info(
                f"[{program_label}] New rep (no prior reps to compare): "
                f"{rep['rep_id']} = {state.boundary_id}"
            )
        states_to_run.extend(state for _, state in candidates)
        return states_to_run, registry

    # Build LLM prompt
    # TODO: tune MAX_REPS_IN_PROMPT if registry grows large (e.g. cap at 50 most recent)
    nc           = len(candidates)
    reps_block   = "\n\n".join(_rep_compact_for_prompt(r) for r in prior_reps)
    cands_block  = "\n\n".join(
        f"[{i}]\n{_boundary_cross_compact(state, per_program_rationale.get(local_idx, ''))}"
        for i, (local_idx, state) in enumerate(candidates)
    )
    ta_str = f"{new_states[0].tool_name} / {new_states[0].algorithm}"

    prompt = f"""\
You are a static analysis expert maintaining a registry of representative \
call-graph boundary patterns found across multiple programs.

EXISTING REPRESENTATIVES (read-only — do NOT re-add these):

{reps_block}

---

NEW BOUNDARIES from program '{new_states[0].program_name}' ({ta_str}):

{cands_block}

---

For each new boundary [0] through [{nc - 1}], decide:
- SIMILAR to an existing rep: the same unsoundness root-cause mechanism \
(same caller dispatch kind AND same missed callee relationship). \
Output: {{"similar_to": "<rep_id>"}}
- NOVEL: no existing rep covers this mechanism. \
Output: {{"novel": true}}

Do NOT use impact ratios or trace counts. Focus on: caller dispatch \
mechanism (virtual, interface, reflection, lambda) and whether the same \
SA tool fix would resolve both boundaries.

Return JSON only:
{{
  "decisions": {{
    "0": {{"similar_to": "rep_0001"}},
    "1": {{"novel": true}}
  }},
  "rationale": {{
    "0": "Same virtual dispatch on a Handler-like pair; rep_0001 covers this",
    "1": "Executor→Runnable interface indirection; distinct from existing reps"
  }}
}}"""

    options = ClaudeAgentOptions(
        model=MODEL_FAST,
        allowed_tools=[],
        setting_sources=["user", "project"],
        cwd=str(Path(__file__).parent.parent),
    )

    collected    = []
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
                f"[{program_label}] Incremental filter — "
                f"stop_reason={message.stop_reason}, turns={message.num_turns}"
            )

    raw = "\n".join(collected) or (final_result or "")
    log.info(f"[{program_label}] Incremental filter response: {len(raw)} chars")

    valid_rep_ids                   = {r["rep_id"] for r in prior_reps}
    decisions, rationale_map        = _parse_incremental_filter_response(raw, nc, valid_rep_ids)

    if not decisions:
        # Fallback: pass all candidates through as new reps
        log.warning(
            f"[{program_label}] Incremental filter: parse failed — treating all candidates as novel."
        )
        for local_idx, state in candidates:
            rep = _make_lean_rep_entry(
                state,
                per_program_rationale.get(local_idx, ""),
                "novel_by_llm",
                len(registry["representatives"]),
            )
            registry["representatives"].append(rep)
        states_to_run.extend(state for _, state in candidates)
        return states_to_run, registry

    # Apply LLM decisions
    n_similar = 0
    n_novel   = 0
    for i, (local_idx, state) in enumerate(candidates):
        decision = decisions.get(i, {"novel": True})

        if "similar_to" in decision:
            rep_id = decision["similar_to"]
            for rep in registry["representatives"]:
                if rep["rep_id"] == rep_id:
                    rep["similar_edges"].append({
                        "program":              state.program_name,
                        "tool":                 state.tool_name,
                        "algorithm":            state.algorithm,
                        "boundary_id":          state.boundary_id,
                        "boundary_index":       state.boundary_index,
                        "similarity_rationale": rationale_map.get(i, ""),
                        "recorded_at":          datetime.now().isoformat(),
                    })
                    break
            log.info(
                f"[{program_label}] {state.boundary_id} → similar to {rep_id} (skipped from pipeline)"
            )
            n_similar += 1
        else:
            rep = _make_lean_rep_entry(
                state,
                per_program_rationale.get(local_idx, ""),
                "novel_by_llm",
                len(registry["representatives"]),
            )
            registry["representatives"].append(rep)
            states_to_run.append(state)
            log.info(f"[{program_label}] New rep (novel): {rep['rep_id']} = {state.boundary_id}")
            n_novel += 1

    log.info(
        f"[{program_label}] Incremental filter done: "
        f"{n_novel} novel (new reps), {n_similar} similar (skipped), "
        f"{len(forced)} forced app-edges → {len(states_to_run)} pass to pipeline"
    )
    return states_to_run, registry


# ── Incremental filter helpers ────────────────────────────────────────────────

def _make_lean_rep_entry(
    state:       PipelineState,
    rationale:   str,
    kept_reason: str,
    rep_index:   int,
) -> dict:
    """Build a lean representative entry from a PipelineState."""
    b      = state.boundary
    caller = b.get("caller", {})
    missed = b.get("missedCallee", {})
    return {
        "rep_id":                f"rep_{rep_index:04d}",
        "added_at":              datetime.now().isoformat(),
        "program":               state.program_name,
        "tool":                  state.tool_name,
        "algorithm":             state.algorithm,
        "boundary_id":           state.boundary_id,
        "boundary_index":        state.boundary_index,
        "kept_reason":           kept_reason,
        "per_program_rationale": rationale,
        "caller_class":          caller.get("declaringClass", ""),
        "caller_method":         caller.get("name", ""),
        "missed_class":          missed.get("declaringClass", ""),
        "missed_method":         missed.get("name", ""),
        "similar_edges":         [],
    }


def _rep_compact_for_prompt(rep: dict) -> str:
    """One-block text summary of a registry rep for the incremental filter prompt."""
    return (
        f"[{rep['rep_id']}]\n"
        f"program={rep['program']} / {rep['tool']} / {rep['algorithm']}\n"
        f"Caller  : {rep['caller_class']}.{rep['caller_method']}\n"
        f"Missed  : {rep['missed_class']}.{rep['missed_method']}\n"
        f"Rationale: {rep.get('per_program_rationale') or '(none)'}"
    )


def _parse_incremental_filter_response(
    response:      str,
    n:             int,
    valid_rep_ids: set[str],
) -> tuple[dict[int, dict], dict[int, str]]:
    """
    Parse the incremental filter LLM response.
    Returns (decisions, rationale) where decisions[i] is one of:
      {"similar_to": "rep_XXXX"} or {"novel": True}
    Returns ({}, {}) on any parse error.
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
    if not data or "decisions" not in data:
        log.warning("Incremental filter: could not parse JSON from LLM response.")
        return {}, {}

    raw_decisions = data.get("decisions", {})
    raw_rationale = data.get("rationale", {})

    decisions: dict[int, dict] = {}
    rationale: dict[int, str]  = {}

    for key, decision in raw_decisions.items():
        try:
            idx = int(key)
        except (ValueError, TypeError):
            continue
        if not (0 <= idx < n):
            log.warning(f"Incremental filter: ignoring out-of-range index {key!r} (n={n})")
            continue

        if "similar_to" in decision:
            rep_id = decision["similar_to"]
            if rep_id not in valid_rep_ids:
                log.warning(
                    f"Incremental filter: unknown rep_id {rep_id!r} for [{key}], treating as novel."
                )
                decisions[idx] = {"novel": True}
            else:
                decisions[idx] = {"similar_to": rep_id}
        elif decision.get("novel"):
            decisions[idx] = {"novel": True}
        else:
            log.warning(
                f"Incremental filter: unrecognized decision for [{key}], treating as novel."
            )
            decisions[idx] = {"novel": True}

        rationale[idx] = raw_rationale.get(key, raw_rationale.get(str(idx), ""))

    return decisions, rationale
