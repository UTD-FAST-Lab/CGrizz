"""
pipeline/state.py
=================
Single source of truth for all data flowing through the pipeline.
Stages read from and write to this object — nothing is passed as
loose arguments between stages.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PipelineState:
    # ── Identity (program × tool × algorithm × boundary) ──────────────────────
    program_name:    str   # e.g. "program_A"
    tool_name:       str   # e.g. "WALA"
    algorithm:       str   # e.g. "0-CFA"
    boundary_index:  int   # position in the boundaries list (0-based)
    boundary_id:     str   # human-readable: "Handler.dispatch→ConcreteHandler.handlePost"

    # ── Inputs (set at startup, never mutated) ─────────────────────────────────
    target_program_path: str
    main_class:          str   # e.g. "com.example.Main"
    sa_tool_path:        str
    boundary:            dict   # the full parsed boundary entry from boundaries.json
    output_dir:          Path   # already scoped per program/tool/algorithm/boundary_N

    # ── Stage outputs (populated as pipeline progresses) ──────────────────────
    unsoundness_report:             Optional[str] = None
    reproducing_test_case_path:     Optional[str] = None
    non_reproducing_test_case_path: Optional[str] = None
    oracle_path:                    Optional[str] = None   # oracle.json inside reproducing test case dir
    target_oracle_path:             Optional[str] = None   # oracle.json produced by running instrumentation against real program
    oracle_run_output:              Optional[str] = None   # combined stdout+stderr from last oracle run on real program
    program_jar_path:               Optional[str] = None   # JAR file or dir for real program classpath (set by orchestrator)
    staticcg_path:                  Optional[str] = None   # static CG from analysis interface
    dyncg_path:                     Optional[str] = None   # dynamic CG from analysis interface
    boundary_path:                  Optional[str] = None   # missed edges dir from interface
    annotated_sa_tool_path:         Optional[str] = None
    modified_analysis_path:         Optional[str] = None   # annotated SA tool output (SCG')
    execution_trace_path:           Optional[str] = None   # execution trace log from annotated SA tool

    # ── Feedback accumulated across retries ────────────────────────────────────
    reproduce_feedback:   list[str] = field(default_factory=list)
    trace_feedback:       list[str] = field(default_factory=list)
    oracle_feedback:      list[str] = field(default_factory=list)  # feedback for instrumentation agent during oracle refinement

    # ── Loop counters ──────────────────────────────────────────────────────────
    loop_count:          int = 0     # outer loop: increments only on reproduce/oracle failures
    annot_attempt:       int = 0     # inner loop: increments on annotation/trace failures
    oracle_refine_count: int = 0     # oracle-only refinement attempts within current outer iteration

    # ── Retry mode flags ──────────────────────────────────────────────────────
    report_oracle_failure: bool = False  # True when outer retry was triggered by oracle check failure

    # ── Annotation info (populated after stage 6a) ────────────────────────────
    annotated_file:   Optional[str] = None   # relative path within annotated_sa_tool_path
    injected_at_line: Optional[int] = None   # line number of the injected [UNSOUNDNESS] log
    instrumentation:  Optional[str] = None   # instrumentation dir inside reproducing test case

    # ── Convenience helpers ────────────────────────────────────────────────────

    def out(self, filename: str) -> str:
        """Return an absolute output path for a given filename."""
        return str(self.output_dir / filename)

    def label(self) -> str:
        """Short identity label for log prefixes."""
        return f"[{self.program_name}/{self.tool_name}/{self.algorithm}/b{self.boundary_index}]"

    def boundary_summary(self) -> str:
        """Structured text summary of the boundary entry for use in prompts."""
        b = self.boundary
        caller = b.get("caller", {})
        missed = b.get("missedCallee", {})
        impact = b.get("impact", {})
        line   = b.get("line", "?")
        pc     = b.get("pc", "?")

        caller_sig = (
            f"{caller.get('declaringClass', '?')}.{caller.get('name', '?')}"
            f"({', '.join(caller.get('parameterTypes', []))})"
            f" → {caller.get('returnType', '?')}"
        )
        missed_sig = (
            f"{missed.get('declaringClass', '?')}.{missed.get('name', '?')}"
        )

        static_callees = b.get("staticCalleesAtSite", [])
        static_sigs = [
            f"  - {sc.get('declaringClass', '?')}.{sc.get('name', '?')}"
            for sc in static_callees
        ]

        traces = b.get("tracesLeadingToBoundary", [])
        trace_lines = []
        for i, trace in enumerate(traces):
            steps = " → ".join(
                f"{m.get('declaringClass', '?')}.{m.get('name', '?')}"
                for m in trace
            )
            trace_lines.append(f"  Trace {i+1}: {steps}")

        lines = [
            f"Caller site : {caller_sig}",
            f"Missed callee: {missed_sig}",
            f"Line         : {line}",
            f"PC           : {pc}",
            f"SA tool     : {self.tool_name} / {self.algorithm}",
            f"Impact      : {impact.get('notCovered', '?')} of "
                           f"{impact.get('totalReachable', '?')} reachable methods not covered",
            "",
            "Static callees known at site (SA tool saw these, missed the concrete target):",
        ] + static_sigs + [
            "",
            "Traces leading to this boundary:",
        ] + trace_lines

        return "\n".join(lines)

    def boundary_json(self) -> str:
        """Full boundary entry as indented JSON string for use in prompts."""
        return json.dumps(self.boundary, indent=2)

    def has_reproduce_feedback(self) -> bool:
        return len(self.reproduce_feedback) > 0

    def has_trace_feedback(self) -> bool:
        return len(self.trace_feedback) > 0

    def has_oracle_feedback(self) -> bool:
        return len(self.oracle_feedback) > 0