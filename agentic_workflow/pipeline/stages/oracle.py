"""
pipeline/stages/oracle.py
==========================
Stage 5 (generate the oracle on the test case) and stage 5b (check it on the
real program), plus the feedback for oracle refinement.
"""

import json
import logging
import shutil
from pathlib import Path

from pipeline.state import PipelineState
from pipeline.agents import build_agent_options
from pipeline.agent_runner import run_agent, parse_json_from_response
from pipeline.oracle import find_agent_jar, run_oracle_on_program, extract_symptoms
from config import OUT_ORACLE, OUT_TARGET_ORACLE

log = logging.getLogger("pipeline")


ORACLE_RUN_EXECUTION_ERROR = "ORACLE_RUN_EXECUTION_ERROR"

# ── Stage 5: Oracle generation ────────────────────────────────────────────────

async def stage_generate_oracle(state: PipelineState, refine: bool = False) -> bool:
    """
    Invokes the instrumentation agent to generate an oracle for the reproducing
    test case. The agent writes oracle.json into the reproducing test case directory.

    refine=True  → called from the oracle refinement sub-loop: keep existing
                   instrumentation, inject oracle feedback into the prompt.
    refine=False → called fresh (after report generator or on first run): delete
                   stale instrumentation when loop_count > 0.
    """
    options     = build_agent_options(state)
    oracle_path = str(Path(state.reproducing_test_case_path) / OUT_ORACLE)
    instrumentation_path = str(Path(state.reproducing_test_case_path) / 'instrumentation')

    if not refine and state.loop_count > 0:
        if instrumentation_path and Path(instrumentation_path).exists():
            shutil.rmtree(Path(instrumentation_path))
            log.info(f"{state.label()} Deleted stale instrumentation dir: {instrumentation_path}")

    oracle_fb = "\n".join(state.oracle_feedback) if refine and state.has_oracle_feedback() else ""

    # run_output_section = ""
    # if refine and state.oracle_run_output:
    #     run_output_section = (
    #         f"\n## Execution output from last real-program run (stdout+stderr)\n"
    #         f"{state.oracle_run_output}\n"
    #         f"Use this output to diagnose why the instrumentation failed or produced no oracle output.\n"
    #         f"**NEVER RUN the instrumentation against the real program**. You can only modify the instrumentation source and re-run the oracle on the Reproducing test case to see how the oracle output changes.\n",
    #     )

    response = await run_agent(
        prompt=f"""\
Use the instrumentation agent to generate an oracle for this boundary.
Invoke it via the Task tool.

## Inputs

Unsoundness report          : {state.unsoundness_report}
Reproducing test case       : {state.reproducing_test_case_path}/
Oracle output path          : {oracle_path}
{oracle_fb}
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
        state.oracle_path = oracle_path
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
        return False, "No program_jar_path"

    agent_jar = find_agent_jar(state)
    if not agent_jar:
        log.warning(f"{state.label()} Agent JAR not found — skipping oracle check on real program")
        return False, "Agent JAR not found"

    oracle_out = state.output_dir / OUT_TARGET_ORACLE
    ok, crashed, run_output, log_path = await run_oracle_on_program(agent_jar, state.program_jar_path, state.main_class, oracle_out)
    state.oracle_run_output = run_output
    state.oracle_run_log_path = str(log_path) if log_path else None

    state.target_oracle_path = str(oracle_out)
    symptoms = extract_symptoms(oracle_out)

    if not symptoms:
        if crashed:
            log.warning(f"{state.label()} Oracle run crashed on real program (non-zero exit code)")
            return False, ORACLE_RUN_EXECUTION_ERROR
        log.info(f"{state.label()} Oracle on real program produced no symptoms")
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

# ── Private helpers ───────────────────────────────────────────────────────────

async def generate_oracle_testcase(state: PipelineState) -> bool:
    """ generate the oracle.json file in the reproducing test case directory"""

    agent_jar = find_agent_jar(state)
    if not agent_jar:
        log.warning(f"{state.label()} Agent JAR not found — cannot generate oracle.json in test case dir.")
        return False

    # read the jcg.conf file (a json file read) from reproducing test case dir to get the main class
    jcg_conf_path = Path(state.reproducing_test_case_path) / "jcg.conf"
    if not jcg_conf_path.exists():
        log.warning(f"{state.label()} jcg.conf not found in reproducing test case dir — cannot generate oracle.json.")
        return False
    try:
        jcg_conf = json.loads(jcg_conf_path.read_text())
        mainclass = jcg_conf.get("main")
    except Exception as e:
        log.warning(f"{state.label()} Could not read jcg.conf: {e} — cannot generate oracle.json.")
        return False
    
    # get the jar file path from the target of the reproducing test case (anything with .jar extension in the target directory)
    target_jar = None
    target_dir = Path(state.reproducing_test_case_path) / "target"
    if target_dir.exists():
        for file in target_dir.iterdir():
            if file.is_file() and file.suffix == ".jar":
                target_jar = file
                break

    if not target_jar:
        log.warning(f"{state.label()} Target JAR not found — cannot generate oracle.json.")
        return False

    ok, _, run_output, _ = await run_oracle_on_program(agent_jar, str(target_jar), mainclass, Path(state.oracle_path))
    if not ok:
        log.warning(f"{state.label()} Oracle run against test case failed — oracle.json may not be generated.")
        return False
    else:
        log.info(f"{state.label()} Oracle.json generated in reproducing test case directory.")
        return True

def build_oracle_feedback_section(state: PipelineState, oracle_prog_feedback: str) -> str:
    """
    Build a per-attempt feedback entry for the instrumentation agent during oracle
    refinement retries. Tells the agent whether symptoms were produced and whether
    the oracle format is correct, without leaking report-generator context.

    The returned string is appended to state.oracle_feedback; all accumulated entries
    are later joined and injected into the stage_generate_oracle prompt.
    """
    if not oracle_prog_feedback:
        return ""

    attempt = state.oracle_refine_count
    lines = [f"\n## Oracle check feedback (attempt {attempt})\n"]

    lines += [
        "## Important notes for oracle refinement\n",
        "1. You should only modify the existing instrumentaiton implementation and you should NOT create a new one\n",
        "2. You should never change the name of the output file when running the instrumentation against the reproducing test case\n",
        "3. You should never run the instrumentation against the real program directly. You can only modify the instrumentation source and re-run the oracle on the Reproducing test case to see how the oracle output changes.\n\n"
    ]

    execution_error = oracle_prog_feedback == ORACLE_RUN_EXECUTION_ERROR
    no_symptoms = "produced no symptoms" in oracle_prog_feedback
    edge_missing = "did not appear in the oracle symptoms" in oracle_prog_feedback

    if execution_error:
        lines += [
            "The instrumentation JAR **crashed or threw an exception** when run against the real target program.",
            "This is a code-level bug in the instrumentation, not a logic issue with the ASM visitor pattern.\n",
            "Common causes to look for in the execution log:",
            "  - ClassNotFoundException: a class referenced in the instrumentation is not on the real program's classpath (check package names and imports)",
            "  - NoSuchMethodError: method signature mismatch between the instrumentation and the runtime class (check descriptor strings in visitMethodInsn calls)",
            "  - VerifyError / ClassFormatError: the generated bytecode is invalid — check that you are not corrupting the operand stack and that visitMaxs/visitEnd are called correctly",
            "  - NullPointerException or other runtime crash inside a visitor method",
        ]
        if state.oracle_run_log_path and Path(state.oracle_run_log_path).exists():
            lines += [
                f"\nThe full JVM execution log (stdout+stderr) is at:\n  {state.oracle_run_log_path}",
                "Read this file to find the exact exception and stack trace.",
            ]
        else:
            lines += ["\n(Execution log not available — check the pipeline log for details.)"]
    elif no_symptoms:
        lines += [
            "The instrumentation ran against the real program without crashing but produced **no symptoms**.",
            "The ASM visitor executed without errors yet did not intercept the intended call sites.\n",
            "This is a logic issue, not a crash. Common causes:",
            "  - The visitor's owner/name/descriptor filter does not match the actual method signature in the real program's bytecode (verify with javap -p -verbose)",
            "  - The instrumented class is not loaded at runtime (wrong package, not on classpath, or loaded by a different classloader)",
            "  - The call site uses INVOKEINTERFACE or invokedynamic but the visitor only handles INVOKEVIRTUAL (or vice versa)",
            "  - The root cause class is never instantiated in the real program's execution path",
        ]
        if state.oracle_run_log_path and Path(state.oracle_run_log_path).exists():
            lines += [
                f"\nThe full execution log (may contain agent startup diagnostics) is at:\n  {state.oracle_run_log_path}",
                "Read it to confirm the Java agent loaded correctly and the JVM started normally.",
            ]
        else:
            lines += ["\n(Execution log not available — check the pipeline log for details.)"]
    elif edge_missing:
        # Symptoms were produced but the boundary edge was not among them.
        oracle_path = state.output_dir / OUT_TARGET_ORACLE
        oracle_format_ok = False
        if oracle_path and Path(oracle_path).exists():
            try:
                oracle_data = json.loads(Path(oracle_path).read_text())
                # check if the methods in the symptoms are in the expected "package.classname.method" format
                for rc in oracle_data.values():
                    symptoms = rc.get("symptoms", [])
                    for symptom in symptoms:
                        caller = symptom.get("caller", "")
                        callee = symptom.get("callee", "")
                        caller_ok = isinstance(caller, str) and len(caller.split(".")) >= 3
                        callee_ok = isinstance(callee, str) and len(callee.split(".")) >= 3
                        if caller_ok and callee_ok:
                            oracle_format_ok = True
                            break
                    if oracle_format_ok:
                        break
                
            except Exception:
                oracle_format_ok = False

        b = state.boundary
        caller_class  = b.get("caller", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
        caller_method = b.get("caller", {}).get("name", "")
        callee_class  = b.get("missedCallee", {}).get("declaringClass", "").lstrip("L").rstrip(";").replace("/", ".")
        callee_method = b.get("missedCallee", {}).get("name", "")

        if oracle_format_ok:
            lines += [
                "The oracle.json format is correct and symptoms were produced, but the "
                f"boundary edge ({caller_class}.{caller_method} → {callee_class}.{callee_method}) "
                "did not appear among them.",
                "The instrumentation does not capture the intended boundary edge. "
                "Refine the ASM visitor so it correctly intercepts this specific call site.",
            ]
        else:
            lines += [
                "The oracle.json format may be incorrect. Verify it matches the expected schema:",
                "package.classname.method for both symptoms and root causes."
            ]
    else:
        lines += [
            f"Oracle check failure: {oracle_prog_feedback}",
            "Please refine the instrumentation to correctly capture the boundary edge.",
        ]

    return "\n".join(lines)

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
