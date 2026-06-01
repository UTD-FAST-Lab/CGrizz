"""
pipeline/stages.py
==================
All pipeline stage functions and the main run_pipeline() orchestrator.

Each stage_*() function:
  - Takes state: PipelineState as its only argument
  - Calls run_agent() from utils
  - Mutates state with its outputs
  - Returns bool (success/fail) or tuple[bool, str] (result, feedback)

run_pipeline() wires the stages together, handles the retry loops,
and writes the final report.

No SCG/DCG file paths are used here. All call graph context comes from
state.boundary — the structured boundary entry parsed from boundaries.json.
"""

import asyncio
import json
import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.agents import build_agent_options
from pipeline.analysis import run_analysis_interface, run_annotated_sa
from pipeline.utils import (
    run_agent,
    parse_json_from_response,
    write_final_report,
    print_success_summary,
    print_failure_summary,
    find_agent_jar,
    run_oracle_on_program,
    extract_symptoms,
)
from config import (
    MAX_LOOPS,
    ANNOTATION_MAX_RETRIES,
    OUT_REPORT,
    OUT_ORACLE,
    OUT_TARGET_ORACLE,
    OUT_TEST_CASE_DIR,
    OUT_ANNOTATED_TOOL_DIR,
    OUT_EXECUTION_TRACE,
    OUT_STATICCG_DIR,
)

log = logging.getLogger("pipeline")


# ── Per-edge file logging ──────────────────────────────────────────────────────

class _EdgeLogFilter(logging.Filter):
    """Passes only records whose message contains this edge's label."""
    def __init__(self, label: str):
        super().__init__()
        self._label = label

    def filter(self, record: logging.LogRecord) -> bool:
        return self._label in record.getMessage()


# ── Stage 3: Unsoundness reasoning report ─────────────────────────────────────

async def stage_generate_report(state: PipelineState) -> bool:
    """
    Delegates to the fn-edge-analyzer agent (via the Task tool) to produce a
    structured JSON unsoundness report for this boundary entry.

    On retry iterations, accumulated feedback from failed reproduction and
    trace checks is injected into the task prompt.
    """
    options       = build_agent_options(state)
    report_path   = state.out(OUT_REPORT)
    feedback      = _build_feedback_section(state)
    retry_context = _build_retry_context(state)

    response = await run_agent(
        prompt=f"""\
Use the fn-edge-analyzer agent to analyze the boundary entry below and write a
structured unsoundness report. Invoke it via the Task tool.

## Task for fn-edge-analyzer

A static analysis tool ({state.tool_name} / {state.algorithm}) missed a callee that
was observed dynamically. Analyze why and write a JSON report to: {report_path}

### Context

Program under analysis : {state.target_program_path}
Static analysis tool   : {state.sa_tool_path}
Tool / Algorithm       : {state.tool_name} / {state.algorithm}
Main class             : {state.main_class}

### Boundary entry

{state.boundary_summary()}


{feedback}
{retry_context}

## After fn-edge-analyzer completes

Return JSON only:
{{
  "report_path": "{report_path}",
  "root_cause_hypothesis": "<1–2 sentence summary of the root cause>",
  "sa_tool_area": "<suspected class or component in the SA tool>"
}}""",
        options=options,
        stage_name=f"{state.label()} GenerateReport",
    )

    result = parse_json_from_response(response)
    log.info(f"{state.label()} Report generation result: {result}")
    if result and "report_path" in result:
        state.unsoundness_report = result["report_path"]
        return True

    if Path(report_path).exists():
        state.unsoundness_report = report_path
        log.warning(f"{state.label()} Could not parse agent response, but found report at {report_path}. Proceeding with it.")
        return True

    log.error(f"{state.label()} Report generation failed.")
    return False


# ── Stage 4: Test case generation ─────────────────────────────────────────────

async def stage_generate_test_case(state: PipelineState) -> bool:
    """
    Delegates to the test-case-generator agent to create two minimal Maven projects:
    one reproducing the unsoundness, one without the root cause.
    """
    options = build_agent_options(state)
    out_dir = state.out(OUT_TEST_CASE_DIR)

    state.reproducing_test_case_path     = out_dir + "/reproducing"
    state.non_reproducing_test_case_path = out_dir + "/non_reproducing"

    if state.loop_count > 0:
        for d in [state.reproducing_test_case_path, state.non_reproducing_test_case_path]:
            if d and Path(d).exists():
                shutil.rmtree(Path(d))
                log.info(f"{state.label()} Deleted stale test case dir: {d}")

    response = await run_agent(
        prompt=f"""\
Use the test-case-generator agent to generate test cases for this boundary.

## Unsoundness report

{state.unsoundness_report}

## Output directories

Reproducing test case     : {state.reproducing_test_case_path}/
Non-reproducing test case: {state.non_reproducing_test_case_path}/

IMPORTANT: The agent must write ALL files exclusively inside the two output directories
listed above. It must NOT create, modify, or write any file outside those directories
(not in the current working directory, not in any parent directory, nowhere else).

Return the JSON result from the agent directly.
## Return

Return JSON only:
{{
  "reproducing": "true | false (if generation failed)",
  "non_reproducing": "true | false (if generation failed or skipped)",
}}


""",
        options=options,
        stage_name=f"{state.label()} TestCaseGeneration",
    )

    result = parse_json_from_response(response)
    log.info(f"{state.label()} Test case generation result: {result}")
    if result:
        # repro = bool(result.get("reproducing", "false").lower() == "true")
        repro = bool(result.get("reproducing", False))
        # non_repro = bool(result.get("non_reproducing", "false").lower() == "true")
        non_repro = bool(result.get("non_reproducing", False))
        if not non_repro:
            log.warning(f"{state.label()} Non-reproducing test case generation failed or skipped.")
            state.non_reproducing_test_case_path = None
        if repro:
            log.info(f"{state.label()} Reproducing test case generated.")
            return True

    log.error(f"{state.label()} Test case generation failed.")
    return False


# ── Stage 5: Oracle generation ────────────────────────────────────────────────

async def stage_generate_oracle(state: PipelineState) -> bool:
    """
    Invokes the instrumentation agent to generate an oracle for the reproducing
    test case. The agent writes oracle.json into the reproducing test case directory.
    """
    options     = build_agent_options(state)
    oracle_path = str(Path(state.reproducing_test_case_path) / OUT_ORACLE)
    instrumentation_path = str(Path(state.reproducing_test_case_path) / 'instrumentation')

    if state.loop_count > 0:
        if instrumentation_path and Path(instrumentation_path).exists():
            shutil.rmtree(Path(instrumentation_path))
            log.info(f"{state.label()} Deleted stale instrumentation dir: {instrumentation_path}")


    response = await run_agent(
        prompt=f"""\
Use the instrumentation agent to generate an oracle for this boundary.
Invoke it via the Task tool.

## Inputs

Unsoundness report          : {state.unsoundness_report}
ASM instrumentation template: /home/mohammad/projects/AgenticCGEvaluation/instrumentation_template/instrumentation
Reproducing test case       : {state.reproducing_test_case_path}/
Oracle output path          : {oracle_path}

Return JSON only:
{{
  "oracle_path": "{oracle_path}",
  "success": true | false
}}""",
        options=options,
        stage_name=f"{state.label()} OracleGeneration",
    )

    if Path(oracle_path).exists():
        state.oracle_path = oracle_path
        state.instrumentation = instrumentation_path
        log.info(f"{state.label()} Oracle generated at {oracle_path}")
        return True

    result = parse_json_from_response(response)
    if result and result.get("success"):
        state.oracle_path = result.get("oracle_path", oracle_path)
        state.instrumentation = instrumentation_path
        log.info(f"{state.label()} Oracle generated at {state.oracle_path}")
        return True

    log.error(f"{state.label()} Oracle generation failed — {OUT_ORACLE} not found.")
    return False


# ── Stage 5b: Oracle check on real program ────────────────────────────────────

async def stage_check_oracle_on_program(state: PipelineState) -> tuple[bool, str]:
    """
    Run the built instrumentation agent JAR against the actual target program and
    verify that the boundary's caller→callee edge appears in the oracle's symptoms.

    This confirms the root cause pattern captured by the instrumentation actually
    manifests in the real program, not just in the test case.

    Fail-open: if program_jar_path is absent or the oracle run fails for infra
    reasons, returns (True, "") so the pipeline continues.

    Returns:
        (found: bool, feedback: str)  — feedback is non-empty only on failure
    """
    if not state.program_jar_path:
        log.info(f"{state.label()} No program_jar_path — skipping oracle check on real program")
        return True, ""

    agent_jar = find_agent_jar(state)
    if not agent_jar:
        log.warning(f"{state.label()} Agent JAR not found — skipping oracle check on real program")
        return True, ""

    oracle_out = state.output_dir / OUT_TARGET_ORACLE
    ok = await run_oracle_on_program(agent_jar, state.program_jar_path, state.main_class, oracle_out)

    if not ok:
        log.warning(f"{state.label()} Oracle run against real program failed — skipping boundary check (fail-open)")
        return False, "Oracle run against real program failed"

    state.target_oracle_path = str(oracle_out)
    symptoms = extract_symptoms(oracle_out)

    if not symptoms:
        log.info(f"{state.label()} Oracle on real program produced no symptoms — skipping boundary check")
        return False, "Oracle on real program produced no symptoms"

    b = state.boundary
    caller_class  = b.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
    caller_method = b.get("caller", {}).get("name", "")
    callee_class  = b.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
    callee_method = b.get("missedCallee", {}).get("name", "")

    if (caller_class, caller_method, callee_class, callee_method) in symptoms:
        log.info(f"{state.label()} Oracle check on real program passed — boundary edge found in symptoms.")
        return True, ""

    msg = (
        f"The instrumentation was run against the real target program but the boundary edge "
        f"({caller_class}.{caller_method} → {callee_class}.{callee_method}) "
        f"did not appear in the oracle symptoms. "
        f"The root cause pattern does not correctly capture the actual unsoundness. "
        f"Please refine the root cause hypothesis so that "
        f"this specific call site becomes visible as a symptom."
    )
    log.error(f"{state.label()} {msg}")
    return False, msg


# ── Stage 6: Reproduction check ───────────────────────────────────────────────

async def stage_check_reproduction(state: PipelineState) -> tuple[bool, str]:
    """
    Run the CG analysis interface on the reproducing test case, then check
    whether the expected caller→callee edge from the unsoundness report
    appears in the boundary output (edges present in dyncg, absent in staticcg).

    No agent is needed — this is a direct lookup in the boundary JSON files.

    Returns:
        (reproduced: bool, feedback: str)
    """
    state.staticcg_path, state.dyncg_path, state.boundary_path = \
        run_analysis_interface(state)

    # Read oracle from the test case directory
    # Oracle format: {"RC:method@pc": {"method": ..., "pc": ..., "symptoms": [...]}, ...}
    try:
        oracle = json.loads(Path(state.oracle_path).read_text())
    except Exception as e:
        msg = f"Could not read oracle: {e}"
        log.error(f"{state.label()} {msg}")
        return False, msg

    # RC entries are top-level values (keys are "RC:..." strings)
    rc_entries = list(oracle.values())
    if not rc_entries:
        msg = "Oracle does not contain any root causes."
        log.error(f"{state.label()} {msg}")
        return False, msg

    # check for symptoms of root causes in the oracle
    all_symptoms = []
    for rc in rc_entries:
        symptoms = rc.get("symptoms", [])
        all_symptoms.extend(symptoms)
    if not all_symptoms:
        msg = "Oracle root causes do not contain any symptoms."
        log.error(f"{state.label()} {msg}")
        return False, msg
    
    # Search boundary files for the symptoms
    for symptom in all_symptoms:
        expected_caller = symptom.get("caller", "")
        expected_callee = symptom.get("callee", "")
        if expected_caller and expected_callee:
            if not _is_reachable(expected_caller, state.staticcg_path):
                continue
            log.info(f"{state.label()} Checking for edge in boundary: {expected_caller} → {expected_callee}")
            reproduced, feedback = _edge_in_boundary(state.boundary_path, expected_caller, expected_callee)
            if not reproduced:
                log.error(f"{state.label()} Edge not found for symptom: {feedback}")
                return False, feedback
        else:
            msg = "Symptom in oracle missing caller or callee information."
            log.error(f"{state.label()} {msg}")
            return False, msg
    
    log.info(f"{state.label()} Reproduction check passed — edge found in boundary.")
    return True, "Reproduction check passed — edge found in boundary."


# ── Stage 7: Root cause annotation ────────────────────────────────────────────

async def stage_root_cause_annotation(state: PipelineState) -> tuple[bool, str]:
    """
    Root-cause-annotator agent adds targeted logging to the SA tool,
    then runs the annotated tool on the test case to produce the execution trace.

    Returns:
        (success: bool, feedback: str)  — feedback is non-empty only on failure
    """
    options       = build_agent_options(state)
    annotated_out = state.out(OUT_ANNOTATED_TOOL_DIR)

    annotated_out_path = Path(annotated_out)

    # if annotated_out_path.exists():
    #     shutil.rmtree(annotated_out_path)
    #     log.info(f"{state.label()} Deleted stale instrumentation dir: {annotated_out_path}")

    if not annotated_out_path.exists():
        log.info(f"{state.label()} Copying SA tool to {annotated_out} ...")
        await asyncio.to_thread(shutil.copytree, state.sa_tool_path, annotated_out)
        log.info(f"{state.label()} SA tool copy done.")        

    annot_feedback = _build_annotation_feedback(state)

    annot_response = await run_agent(
        prompt=f"""\
Use the root-cause-annotator agent to annotate the SA tool for this boundary.
Invoke it via the Task tool.

## Unsoundness report

{state.unsoundness_report}

## SA tool source

{annotated_out}/
{annot_feedback}

Return the JSON result from the agent directly.

Return JSON only:

{{
  "annotated_tool_path": "<source_dir>/",
  "annotated_file":      "<relative/path/to/ModifiedFile.java>",
  "injected_at_line":    <line_number>,
  "skipped":             false,
  "skip_reason":         null
}}

If the log already exists for the same reason (`skipped: true`):

{{
  "annotated_tool_path": "<source_dir>/",
  "annotated_file":      null,
  "injected_at_line":    null,
  "skipped":             true,
  "skip_reason":         "Existing [UNSOUNDNESS] log with same reason found at <file>:<line>"
}}""",
        options=options,
        stage_name=f"{state.label()} RootCauseAnnotation",
    )

    annot_result = parse_json_from_response(annot_response)
    log.info(f"{state.label()} Annotation result: {annot_result}")
    if not annot_result or "annotated_tool_path" not in annot_result:
        log.error(f"{state.label()} Annotation failed.")
        return False, "Annotation step failed — could not parse result."

    state.annotated_sa_tool_path = annot_result["annotated_tool_path"]
    state.annotated_file         = annot_result.get("annotated_file")
    state.injected_at_line       = annot_result.get("injected_at_line")

    if annot_result.get("skipped"):
        log.info(f"{state.label()} Annotation skipped: {annot_result.get('skip_reason')}")
    else:
        log.info(
            f"{state.label()} Annotated {state.annotated_file} "
            f"at line {state.injected_at_line}"
        )

    # Run annotated tool → produces the execution trace consumed by stage_check_trace
    state.modified_analysis_path = run_annotated_sa(state)
    return True, ""


# ── Stage 8: Trace check ───────────────────────────────────────────────────────

async def stage_check_trace(state: PipelineState) -> tuple[bool, str]:
    """
    Checks whether the execution trace produced by the annotated SA tool
    contains the expected [UNSOUNDNESS] log entries confirming the root cause.

    Returns:
        (trace_contains_logs: bool, feedback: str)
    """
    options        = build_agent_options(state)
    trace_log_path = str(Path(state.reproducing_test_case_path) / OUT_STATICCG_DIR / OUT_EXECUTION_TRACE)
    oracle_path    = state.oracle_path
    expected_pattern = "[UNSOUNDNESS]"

    # read the execution trace log
    try:
        trace_log = Path(trace_log_path).read_text()
        state.execution_trace_path = trace_log_path  # store for annotation feedback on retry
        unsoundness_lines = [line for line in trace_log.splitlines() if expected_pattern in line]
        log.info(f"{state.label()} Found {len(unsoundness_lines)} lines with pattern [UNSOUNDNESS] in execution trace.")
    except Exception as e:
        msg = f"Could not read execution trace log: {e}"
        log.error(f"{state.label()} {msg}")
        return False, msg

    # check for existance of pattern in the trace log
    if len(unsoundness_lines) == 0:
        log.error(f"{state.label()} Trace check failed — no lines with pattern [UNSOUNDNESS] found in execution trace.")
        return False, "Trace check failed — no lines with pattern [UNSOUNDNESS] found in execution trace."

    # read the root causes from the oracle
    # Oracle format: {"RC:method@pc": {"method": ..., "pc": ..., "symptoms": [...]}, ...}
    rc_methods = set()
    try:
        oracle = json.loads(Path(oracle_path).read_text())
        for rc in oracle.values():
            rc_methods.add(rc.get("method"))
    except Exception as e:
        msg = f"Could not read oracle for root cause methods: {e}"
        log.error(f"{state.label()} {msg}")

    # check that all reachable root cause methods apear in the unsoundness lines
    for method in rc_methods:
        if not _is_reachable(method, state.staticcg_path):
            log.warning(f"The method is not reachable for root cause: {method}")
            continue
        if not _rc_in_trace(method, unsoundness_lines):
            log.error(f"{state.label()} Trace check failed — expected root cause method '{method}' not found in execution trace.")
            return False, f"Trace check failed — expected root cause method '{method}' not found in execution trace."

    log.info(f"{state.label()} Trace check passed — expected logs found in execution trace.")
    return True, "Trace check passed — expected logs found in execution trace."


# ── Stage 9: Refine unsoundness report ───────────────────────────────────────

async def stage_refine_report(state: PipelineState) -> bool:
    """
    After trace confirmation, update the unsoundness report to replace the initial
    hypothesis with the confirmed code location found in the execution trace.
    Non-critical: failures are logged but do not halt the pipeline.
    """

    if not (state.unsoundness_report and Path(state.unsoundness_report).exists()):
        log.info(f"{state.label()} No report file — skipping report refinement.")
        return True

    options = build_agent_options(state)
    line_hint = f" at line {state.injected_at_line}" if state.injected_at_line else ""
    annot_hint = (
        f"\nAnnotated file : {state.annotated_sa_tool_path}/{state.annotated_file}{line_hint}"
        if state.annotated_file else ""
    )

    await run_agent(
        prompt=f"""\
The annotation and trace check have confirmed the root cause. Update the unsoundness
report to reflect the confirmed findings.

## Current unsoundness report
Path: {state.unsoundness_report}
(Read this file first.)

## Confirmed tool limitation location
{annot_hint}

## Task
1. Read the current report JSON.
2. Update these fields only (preserve ALL other fields verbatim):
   - `toolCodeEvidence.relevantFiles`: confirm or correct file and line number
   - `toolLimitation`: mention the confirmed code site
4. Overwrite the report at the same path: {state.unsoundness_report}

DO NOT change the format or add any field. 

Return JSON only:
{{
  "report_path": "{state.unsoundness_report}",
  "refined": true
}}""",
        options=options,
        stage_name=f"{state.label()} RefineReport",
    )

    log.info(f"{state.label()} Report refinement complete.")
    return True


# ── Stage 10: Generalize instrumentation oracle ───────────────────────────────

async def stage_generalize_instrumentation(state: PipelineState) -> bool:
    """
    After trace confirmation, try to extend the ASM oracle to cover the full family
    of methods that exhibit the same root cause, making deduplication more effective.
    Non-critical: failures are logged but do not halt the pipeline.
    """
    if not (state.execution_trace_path and Path(state.execution_trace_path).exists()):
        log.info(f"{state.label()} No execution trace — skipping instrumentation generalization.")
        return True
    if not (state.instrumentation and Path(state.instrumentation).exists()):
        log.info(f"{state.label()} No instrumentation dir — skipping generalization.")
        return True

    options = build_agent_options(state)

    await run_agent(
        prompt=f"""\
The trace has confirmed the root cause pattern. Try to generalize the oracle
so it captures more methods exhibiting the same root cause in other programs.

## Execution trace
Path: {state.execution_trace_path}
(Read to find [UNSOUNDNESS] lines — they show which methods fired the pattern.)

## Oracle JSON
Path: {state.oracle_path}
(Read to see which root causes and symptoms are currently captured.)

## Instrumentation source
Path: {state.instrumentation}/
(Read the source to understand the current instrumentation.)

## Unsoundness report
Path: {state.unsoundness_report}
(Read for the pattern_class and root cause description.)

## Task
Examine the [UNSOUNDNESS] lines and the oracle. Identify if there are OTHER methods
in the same class or pattern family that follow the same root cause pattern but are
not yet captured (e.g. multiple Unsafe.* methods, multiple reflection APIs, etc.).

If you find additional methods to instrument:
1. Edit the instrumentation source under {state.instrumentation}/ to instrument them.
2. Rebuild: `mvn -q package -f {state.instrumentation}/pom.xml`
3. Re-run the oracle against the test case to regenerate oracle.json:
   `java -javaagent:<rebuilt_jar> -cp <test_classpath> <main_class>`
4. Overwrite {state.oracle_path} with the extended oracle.

If the oracle already covers everything relevant, or generalization is not applicable,
do nothing and return generalized=false.

Return JSON only:
{{
  "generalized": true | false,
  "added_methods": ["<class.method>"]
}}""",
        options=options,
        stage_name=f"{state.label()} GeneralizeInstrumentation",
    )

    log.info(f"{state.label()} Instrumentation generalization complete.")
    return True


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(state: PipelineState) -> None:
    state.output_dir.mkdir(parents=True, exist_ok=True)
    log_path     = state.output_dir / "pipeline.log"
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    file_handler.addFilter(_EdgeLogFilter(state.label()))
    logging.getLogger("pipeline").addHandler(file_handler)

    try:
        await _run_pipeline_inner(state)
    finally:
        logging.getLogger("pipeline").removeHandler(file_handler)
        file_handler.close()


async def _run_pipeline_inner(state: PipelineState) -> None:
    log.info(f"{state.label()} {'=' * 50}")
    log.info(f"{state.label()} PIPELINE START  {state.boundary_id}")
    log.info(f"{state.label()} {'=' * 50}")

    # Stages 3–8: main retry loop (each PipelineState holds exactly one boundary entry)
    while state.loop_count <= MAX_LOOPS:
        label = f"Loop {state.loop_count}" if state.loop_count > 0 else "Initial run"
        log.info(f"{state.label()} {'─' * 40} {label}")

        # Stage 3: Generate unsoundness report
        if not await stage_generate_report(state):
            write_final_report(state, success=False, reason="Report generation failed.")
            return

        # Stage 4: Generate test case
        if not await stage_generate_test_case(state):
            write_final_report(state, success=False, reason="Test case generation failed.")
            return

        # Stage 5: Generate oracle
        if not await stage_generate_oracle(state):
            write_final_report(state, success=False, reason="Oracle generation failed.")
            return

        # Stage 5b: Check oracle on real program
        oracle_prog_ok, oracle_prog_feedback = await stage_check_oracle_on_program(state)
        if not oracle_prog_ok:
            state.reproduce_feedback.append(oracle_prog_feedback)
            log.info(f"{state.label()} Oracle check on real program failed. Feedback recorded.")
            if state.loop_count >= MAX_LOOPS:
                break
            state.loop_count += 1
            continue  # → back to stage 3 with feedback

        # Stage 6: Check reproduction
        reproduced, repr_feedback = await stage_check_reproduction(state)
        if not reproduced:
            state.reproduce_feedback.append(repr_feedback)
            log.info(f"{state.label()} Not reproduced. Feedback recorded.")
            if state.loop_count >= MAX_LOOPS:
                break
            state.loop_count += 1
            continue   # → back to stage 3 with reproduce_feedback injected

        log.info(f"{state.label()} Reproduced. Proceeding to annotation...")

        # Stages 7–8: inner annotation retry loop
        # Retries only the annotation + trace steps; stages 3–6 are NOT re-run here.
        state.annot_attempt = 0
        while state.annot_attempt <= ANNOTATION_MAX_RETRIES:
            annot_label = (
                f"Annotation attempt {state.annot_attempt + 1}/{ANNOTATION_MAX_RETRIES + 1}"
            )
            log.info(f"{state.label()} {'·' * 30} {annot_label}")

            # Stage 7: Root cause annotation + run annotated SA tool
            annot_ok, annot_fb = await stage_root_cause_annotation(state)
            if not annot_ok:
                state.trace_feedback.append(annot_fb)
                log.info(f"{state.label()} Annotation failed. Feedback recorded.")
                if state.annot_attempt >= ANNOTATION_MAX_RETRIES:
                    break
                state.annot_attempt += 1
                continue   # retry annotation only — stages 3–6 untouched

            # Stage 8: Check execution trace
            trace_ok, trace_fb = await stage_check_trace(state)
            if trace_ok:
                # Stage 9: Refine report with confirmed trace evidence
                await stage_refine_report(state)
                # Stage 10: Generalize instrumentation oracle
                # await stage_generalize_instrumentation(state)
                write_final_report(
                    state,
                    success=True,
                    reason="Root cause identified and confirmed via execution trace.",
                )
                print_success_summary(state)
                return

            state.trace_feedback.append(trace_fb)
            log.info(f"{state.label()} Trace check failed. Feedback recorded.")
            if state.annot_attempt >= ANNOTATION_MAX_RETRIES:
                break
            state.annot_attempt += 1
            # Loop back to stage 7 with updated trace_feedback — stages 3–6 untouched

        # Annotation retries exhausted — propagate to outer loop or fail
        if state.loop_count >= MAX_LOOPS:
            break
        state.loop_count += 1
        # → back to stage 3 with all feedback injected (rare: only if outer loop allows it)

    # Max loops reached
    write_final_report(
        state,
        success=False,
        reason=(
            f"Exceeded maximum attempts (outer loops: {state.loop_count}, "
            f"annotation attempts: {state.annot_attempt}). "
            f"Reproduce feedbacks: {state.reproduce_feedback}. "
            f"Trace feedbacks: {state.trace_feedback}."
        ),
    )
    print_failure_summary(state)


# ── Private helpers ───────────────────────────────────────────────────────────

def _build_feedback_section(state: PipelineState) -> str:
    """Build the feedback block injected into the report prompt on retries."""
    if not state.has_reproduce_feedback() and not state.has_trace_feedback():
        return ""

    lines = ["\n## Feedback from previous attempts\n"]
    if state.has_reproduce_feedback():
        lines.append("### Test case did not reproduce the unsoundness:")
        lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.reproduce_feedback)]
    if state.has_trace_feedback():
        lines.append("\n### Execution trace did not contain expected logs:")
        lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.trace_feedback)]
    lines.append("\nRevise your root cause hypothesis and test case strategy accordingly.")

    return "\n".join(lines)


def _build_retry_context(state: PipelineState) -> str:
    """
    Build the context block injected into the report prompt on retries.
    Surfaces the previous report, the reproducing test case, and the annotated
    SA tool file so the agent can read and revise its earlier analysis.
    Returns an empty string on the first (non-retry) run.
    """
    if state.loop_count == 0:
        return ""

    lines = [
        "\n## Context from previous attempt\n",
        "IMPORTANT — FORMAT PRESERVATION: This is a retry. You MUST read the previous",
        "report file listed below, apply the feedback, and write the revised analysis",
        "back to the SAME output path using the EXACT SAME JSON schema as the original.",
        "Do not add, remove, or rename any top-level keys. Do not change the format.",
        "Only update field VALUES based on the feedback above.\n",
    ]

    if state.unsoundness_report and Path(state.unsoundness_report).exists():
        lines += [
            "### Previous unsoundness report",
            "",
            f"Path: {state.unsoundness_report}",
            "(Read this file to review your previous analysis before revising it based on the feedback above.)",
        ]

    if state.reproducing_test_case_path and Path(state.reproducing_test_case_path).exists():
        lines += [
            "",
            "### Reproducing test case",
            "",
            f"Path: {state.reproducing_test_case_path}",
            "(Read the Java source files here to understand how the test case was structured.)",
        ]

    if state.oracle_path and Path(state.oracle_path).exists() and state.instrumentation and Path(state.instrumentation).exists():
        lines += [
            "",
            "### Oracle for reproducing test case",
            "",
            f"Path: {state.oracle_path}",
            f"Path: {state.instrumentation}",
            "(Read this file to review the expected root causes and symptoms that the agent identified for the test case using the ASM instrumentation)",
        ]

    if state.target_oracle_path and Path(state.target_oracle_path).exists():
        lines += [
            "",
            "### Oracle produced by running instrumentation against the real target program",
            "",
            f"Path: {state.target_oracle_path}",
            "(This oracle was produced by running the instrumentation JAR against the actual target program. "
            "The boundary edge was NOT found in its symptoms — use it to understand what the instrumentation "
            "does observe at runtime and refine the root cause pattern accordingly.)",
        ]


    if state.annotated_sa_tool_path and state.annotated_file:
        line_hint = f" (line {state.injected_at_line})" if state.injected_at_line else ""
        lines += [
            "",
            "### Previously annotated SA tool file",
            "",
            f"Annotated Tool Path: {state.annotated_sa_tool_path}, File: {state.annotated_file}{line_hint}",
            "(Read this file to see the [UNSOUNDNESS] log that was injected. "
            "Use it to validate and refine your root cause hypothesis.)",
        ]

    return "\n".join(lines)


def _build_annotation_feedback(state: PipelineState) -> str:
    """
    Build the feedback block injected into the annotation stage prompt on retries.
    Includes: prior trace failure messages, actual [UNSOUNDNESS] lines from the last
    trace, oracle root cause methods, and the exact log format the checker expects.
    """
    if not state.has_trace_feedback():
        return ""

    lines = ["\n## Feedback from previous annotation attempts\n"]
    lines += [f"- Attempt {i+1}: {fb}" for i, fb in enumerate(state.trace_feedback)]

    if state.execution_trace_path and Path(state.execution_trace_path).exists():
        try:
            trace_text = Path(state.execution_trace_path).read_text()
            unsoundness_lines = [l for l in trace_text.splitlines() if "[UNSOUNDNESS]" in l]
            if unsoundness_lines:
                lines.append("\n### [UNSOUNDNESS] lines found in previous execution trace:")
                lines += [f"  {l}" for l in unsoundness_lines]
            else:
                lines.append("\n### Previous execution trace: no [UNSOUNDNESS] lines found at all.")
        except Exception:
            pass

    if state.oracle_path and Path(state.oracle_path).exists():
        try:
            oracle = json.loads(Path(state.oracle_path).read_text())
            lines.append("\n### Oracle enclosing root cause methods (these MUST appear in the trace):")
            for rc_key, rc_val in oracle.items():
                method = rc_val.get("method", "?")
                lines.append(f"{method}")

            lines.append(f"This oracle is for the test case at path (ONLY see the /src directory of the test case if needed): {state.reproducing_test_case_path}")
        except Exception:
            pass

    if state.annotated_file and state.injected_at_line:
        lines.append("\n### Previous annotation was injected at this location:")
        lines.append(f"File Path: {state.annotated_file}")
        lines.append(f"Injected at line: {state.injected_at_line}")

    lines += [
        "\n### Required log format (the trace checker parses this):",
        f"  [UNSOUNDNESS] <pattern_class> | caller=<caller_method_fullname> | site=<site>",
        "\nRevise the injection point so the log fires with the correct caller format.",
    ]

    return "\n".join(lines)


def _descriptor_to_classname(descriptor: str) -> str:
    """Convert a JVM class descriptor to a dotted class name.
    e.g. 'Lcom/example/App;' → 'com.example.App'
    Already-dotted names (no slashes) are returned as-is.
    """
    return descriptor.lstrip("L").rstrip(";").replace("/", ".")


def _entry_matches(entry: dict, expected_caller: str, expected_callee: str) -> bool:
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

    caller_class  = _descriptor_to_classname(caller.get("declaringClass", ""))
    caller_method = caller.get("name", "")
    callee_class  = _descriptor_to_classname(callee.get("declaringClass", ""))
    callee_method = callee.get("name", "")

    caller_match = caller_class in expected_caller and caller_method in expected_caller
    callee_match = callee_class in expected_callee and callee_method in expected_callee
    return caller_match and callee_match


def _edge_in_boundary(
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
        if _entry_matches(entry, expected_caller, expected_callee):
            caller = entry.get("caller", {})
            callee = entry.get("missedCallee", {})
            return True, (
                f"Edge confirmed in {Path(boundary_path).name}: "
                f"{_descriptor_to_classname(caller.get('declaringClass','?'))}"
                f".{caller.get('name','?')} → "
                f"{_descriptor_to_classname(callee.get('declaringClass','?'))}"
                f".{callee.get('name','?')}"
            )

    return False, (
        f"Edge not found in boundary output. "
        f"Expected: {expected_caller} → {expected_callee}. "
        f"Searched {Path(boundary_path).name}."
    )

def _rc_in_trace(expected_method: str, trace_log_lines: list[str]) -> bool:
    
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

def _is_reachable(expected_caller: str, staticcg_path: str) -> bool:
    """Check whether a caller method appears in the static call graph."""
    try:
        data = json.loads(Path(staticcg_path).read_text())
        reachable_methods = data.get("reachableMethods", [])
    except Exception as e:
        log.warning(f"Failed reading static CG file {staticcg_path}: {e}")
        return False

    for method in reachable_methods:
        caller = method.get("method", {})
        caller_class  = _descriptor_to_classname(caller.get("declaringClass", ""))
        caller_method = caller.get("name", "")
        if caller_class in expected_caller and caller_method in expected_caller:
            return True

    log.warning(f"{expected_caller} is not reachable in the static call graph or the format is not correct")
    return False
