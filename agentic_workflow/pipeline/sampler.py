"""
pipeline/sampler.py
===================
Boundary sampler: runs once per (program, tool, algorithm) triple,
before any per-boundary pipelines are spawned.

Reads the full boundaries list, sends compact summaries to a fast model,
and returns the subset worth analyzing — filtered for uniqueness, impact,
and trace diversity.
"""

import json
import logging
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

from config import MODEL_FAST, OUT_SELECTED_BOUNDARIES

log = logging.getLogger("pipeline.sampler")


# ── Compact summary (no PipelineState needed) ─────────────────────────────────

def _boundary_compact(b: dict) -> str:
    """One-block text summary of a boundary dict for use in the sampler prompt."""
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


# ── Main sampler ──────────────────────────────────────────────────────────────

async def run_boundary_sampler(
    boundaries:     list[dict],
    program_name:   str,
    tool_name:      str,
    algorithm:      str,
    output_dir:     Path,
    max_boundaries: int | None = None,
) -> tuple[list[dict], list[int]]:
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
        (selected_boundaries, original_indices)
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

Return JSON only — no explanation outside the JSON block:
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
            # SDK internal message — no content attribute, nothing to collect
            continue
 
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "type") and block.type == "text":
                    collected.append(block.text)
 
        elif isinstance(message, ResultMessage):
            # ResultMessage carries the final text in .result (a string)
            # and metadata like cost, stop_reason, session_id.
            # Use it as the authoritative response if present.
            if message.result:
                final_result = message.result
            log.debug(
                f"[{stage_name}] Result metadata — "
                f"stop_reason={message.stop_reason}, "
                f"turns={message.num_turns}"
            )

    raw = "\n".join(collected)
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
    return selected_boundaries, selected_indices


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

    # Validate indices are in-range integers
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
