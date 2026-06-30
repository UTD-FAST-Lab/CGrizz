#!/usr/bin/env python3
"""
RQ1_feedback.py
===============
Fills two RQ1 tables from the pipeline results:

  1. Failed-case breakdown (tab:rq1_results, "Failed" columns)
     -------------------------------------------------------------
     For every FAILED boundary, the *last* error that ended the pipeline,
     categorized into four stages:

         agent     - an agent stage produced no usable output
                     (report / test-case / oracle generation, or annotation).
         oracle    - oracle check on the REAL target program failed
                     (root-cause instrumentation did not expose the edge).
         boundary  - reproduction / boundary check on the reproducing test
                     case failed.
         trace     - execution trace did not contain the root cause.

     A run may fail and retry several times; only the LAST failure is counted.

  2. Successful-case validation breakdown (tab:rq1_success)
     -------------------------------------------------------------
     For every SUCCESSFUL (reasoned) boundary, how many validation feedbacks
     of each kind were emitted across all its retries (ALL of them, not just
     the last):

         V1 = oracle   check feedbacks
         V2 = boundary check feedbacks
         V3 = trace    check feedbacks
         Final = number of successful cases (S)

How failures/feedbacks are classified
-------------------------------------
The pipeline records structured feedback in pipeline_result.json:

    feedback_history.reproduce_feedback  - appended in exactly two spots:
        * oracle escalation -> the fixed string starting with
          "The root cause hypothesis is not correct"  => ORACLE
        * reproduction check feedback (anything else)  => BOUNDARY
    feedback_history.trace_feedback      - appended in exactly two spots:
        * "Annotation step failed ..."                 => AGENT (annotation)
        * trace check feedback (anything else)         => TRACE

For the *last* failure of a failed run we need chronological order across the
two lists, which the lists alone cannot give. The pipeline.log records the
exact decision points in order, so we read the LAST of these markers:

    "Escalating to report generator"      -> oracle
    "Not reproduced. Feedback recorded"   -> boundary
    "Trace check failed. Feedback recorded" -> trace
    "Annotation failed. Feedback recorded"  -> agent

Immediate (non-retry) failures are read straight from the `reason` field.

Program subsets are identical to RQ1_table.py.
"""

from __future__ import annotations

from pathlib import Path
import json
from collections import defaultdict

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

PIPELINE_FILE = "pipeline_result.json"
LOG_FILE      = "pipeline.log"

FAIL_CATS = ['agent', 'oracle', 'boundary', 'trace']

# ── Program subsets we care about (same as RQ1_table.py) ──────────────────────

NJR_25 = {
    'url8af84d296f_mrcosta_gantt_generator_tgz-pJ8-org_gantt_generator_mrcpsp_RunJ8',
    'url70997c83aa_Xilaew_risiko_tgz-pJ8-risiko_XMI2XMLtransformerJ8',
    'urldf54784279_tonyj321_Root_Play_tgz-pJ8-hep_io_root_output_demo_HistogramDemoJ8',
    'url4e79c36700_JacenLakiir_Tivoo_tgz-pJ8-MainJ8',
    'url088e9d87be_jpush_jpush_java_library_tgz-pJ8-cn_jpush_example_JPushClientExampleJ8',
    'url78eec0e0b6_Orikuro_vic_tgz-pJ8-net_nexon_vindictus_itemcomparer_inport_StaticImportJ8',
    'url633772aa30_isma3l_Tp2_tgz-pJ8-ar_fiuba_tecnicas_framework_Pruebas_Casos1_MainJ8',
    'url542ae48576_tonyheadford_m68k_tgz-pJ8-m68k_MonitorJ8',
    'url0f7f8f6435_TimmiTBy_Epam_tgz-pJ8-com_epam_electricalappliance_RunnerJ8',
    'urlc58b5c5b73_emiliano_fiuba_2013_2c_7510_tgz-pJ8-test_java_FrameworkTestsJ8',
    'url901cc5721b_shidasan_KonohaCoffee_tgz-pJ8-org_KonohaScript_KonohaJ8',
    'urlbf4fd38f68_rduerig_StrategyBDD_tgz-pJ8-com_strategy_util_TestMainJ8',
    'url5a01f2c1d9_core9_core_tgz-pJ8-io_core9_core_boot_BootstrapFrameworkJ8',
    'url3426a97ee4_rtreffer_minidns_tgz-pJ8-de_measite_minidns_minidnsrepl_MiniDnsReplJ8',
    'url4df616fce9_s3e0n1g1_prototype3_tgz-pJ8-Trading_Engine_MainJ8',
    'url28dfd00382_byang223_TurnBAPI_tgz-pJ8-units_upgrades_UpgradeTestJ8',
    'url9e7e48285f_nalbachk_MidiToPhaseShift_tgz-pJ8-com_my_main_MainJ8',
    'urlac20d257ff_zhongdj_Madz_Stochastic_tgz-pJ8-net_madz_test_stochastic_core_impl_DynamicDemoJ8',
    'urlcf7851e92b_Triploblastic_CSCD350_FinalProject_tgz-pJ8-MazeGame_MazeJ8',
    'url27db8982cd_leohinterlang_argFace_tgz-pJ8-com_fidelis_argface_ArgSampleJ8',
    'url1d5e9d990f_Telsis_jOCP_tgz-pJ8-com_telsis_jocp_sampleApp_BasicOCPAppJ8',
    'urlb879d9e865_ogotalski_elevator_tgz-pJ8-ElevatorTaskJ8',
    'url270fc4f5ee_ykcilborw_Joust_tgz-pJ8-fri_patterns_interpreter_parsergenerator_examples_HowToOverrideLexerImplJ8',
    'url708d477f92_zSakare_SENG3011_tgz-pJ8-test_runner_TestRunnerJ8',
    'url400631cdd0_ludaiqian_appliedxml_tgz-pJ8-test_simples_TestJ8',
}

XCORPUS_SUBSET = {
    'javacc-5.0',
    'nekohtml-1.9.14',
    'fitjava-1.1',
}

# ── Marker / pattern constants ────────────────────────────────────────────────

# Last-error markers in pipeline.log, in run_pipeline decision order.
LOG_MARKERS = [
    ("Escalating to report generator",        "oracle"),
    ("Not reproduced. Feedback recorded",     "boundary"),
    ("Trace check failed. Feedback recorded", "trace"),
    ("Annotation failed. Feedback recorded",  "agent"),
]

# Immediate (non-retry) failure reasons -> category (all agent-side).
IMMEDIATE_AGENT_REASONS = (
    "Report generation failed",
    "Test case generation failed",
    "Oracle generation failed",
    "Unexpected pipeline error",
)

ORACLE_FEEDBACK_PREFIX = "The root cause hypothesis is not correct"
ANNOTATION_FEEDBACK_PREFIX = "Annotation step failed"


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def results_dir(tool: str, bench: str) -> Path:
    return OUTPUT_PATH / f"results_{tool}_{bench}"


def discover_cats_programs() -> set:
    progs = set()
    for tool in TOOLS:
        d = results_dir(tool, 'cats')
        if d.exists():
            progs.update(p.name for p in d.iterdir() if p.is_dir())
    return progs


def edge_dirs(program_dir: Path) -> list:
    return [p for p in program_dir.glob("*/*/*") if p.is_dir()]


# ── Classification ────────────────────────────────────────────────────────────

def classify_reproduce(entry: str) -> str:
    return "oracle" if entry.startswith(ORACLE_FEEDBACK_PREFIX) else "boundary"


def classify_trace(entry: str) -> str:
    return "agent" if entry.startswith(ANNOTATION_FEEDBACK_PREFIX) else "trace"


def last_failure_category(edge_dir: Path, pipeline_data: dict) -> str | None:
    """Return the category of the LAST error that ended a failed run."""
    reason = pipeline_data.get("reason") or ""

    # Immediate, non-retry terminations are fully described by `reason`.
    for r in IMMEDIATE_AGENT_REASONS:
        if reason.startswith(r):
            return "agent"

    # Retry-exhausted ("Exceeded maximum attempts ...") — read the log for the
    # last chronological decision marker.
    log_path = edge_dir / LOG_FILE
    if log_path.exists():
        last_cat = None
        try:
            with open(log_path, "r", errors="ignore") as f:
                for line in f:
                    for marker, cat in LOG_MARKERS:
                        if marker in line:
                            last_cat = cat
                            break
        except Exception:
            last_cat = None
        if last_cat is not None:
            return last_cat

    # Fallback: no usable log — infer from feedback_history (loses cross-list
    # ordering; only reached if pipeline.log is missing).
    fh = pipeline_data.get("feedback_history", {}) or {}
    rep = fh.get("reproduce_feedback", []) or []
    tr = fh.get("trace_feedback", []) or []
    if tr:
        return classify_trace(tr[-1])
    if rep:
        return classify_reproduce(rep[-1])
    return "agent"  # nothing recorded — attribute to the agent stage


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    cats_programs = discover_cats_programs()

    def is_relevant(bench: str, program_name: str) -> bool:
        if bench == 'njr':
            return program_name in NJR_25
        if bench == 'cats':
            return program_name in cats_programs
        if bench == 'xcorpus':
            return program_name in XCORPUS_SUBSET
        return False

    # Failed-case last-error counts.
    fail_stats = defaultdict(lambda: {c: 0 for c in FAIL_CATS})
    # Successful-case validation-feedback counts (V1, V2, V3).
    succ_stats = defaultdict(lambda: {
        "oracle": 0, "boundary": 0, "trace": 0,   # V1, V2, V3 (validation feedbacks)
        "n_success": 0,        # number of successful boundaries
        "n_with_feedback": 0,  # successful boundaries with >= 1 feedback (any retry)
        "n_retries": 0,        # total feedback entries (retries), incl. annotation
    })

    for tool in TOOLS:
        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            for program_dir in d.iterdir():
                if not program_dir.is_dir():
                    continue
                if not is_relevant(bench, program_dir.name):
                    continue

                for edge_dir in edge_dirs(program_dir):
                    pipeline_data = read_json(edge_dir / PIPELINE_FILE)
                    if pipeline_data is None:
                        continue

                    if pipeline_data.get("success", False):
                        s = succ_stats[(tool, bench)]
                        s["n_success"] += 1
                        fh = pipeline_data.get("feedback_history", {}) or {}
                        repro = fh.get("reproduce_feedback", []) or []
                        trace = fh.get("trace_feedback", []) or []
                        # retries = every feedback round (oracle/boundary/trace/annotation)
                        n_fb = len(repro) + len(trace)
                        s["n_retries"] += n_fb
                        if n_fb > 0:
                            s["n_with_feedback"] += 1
                        for e in repro:
                            s[classify_reproduce(e)] += 1
                        for e in trace:
                            if classify_trace(e) == "trace":  # skip agent-side annotation
                                s["trace"] += 1
                    else:
                        cat = last_failure_category(edge_dir, pipeline_data)
                        if cat:
                            fail_stats[(tool, bench)][cat] += 1

    _print_failure_table(fail_stats)
    _print_success_table(succ_stats)
    _print_success_retry_stats(succ_stats)


# ── Output helpers ────────────────────────────────────────────────────────────

TOOL_LABEL  = {'wala': 'WALA', 'opal': 'OPAL', 'soot': 'Soot'}
BENCH_LABEL = {'cats': 'Cats', 'njr': 'NJR', 'xcorpus': 'XCorpus'}


def _print_failure_table(fail_stats):
    def total(s):
        return sum(s[c] for c in FAIL_CATS)

    print("\n" + "=" * 78)
    print("FAILED-CASE LAST-ERROR BREAKDOWN  (tab:rq1_results, Failed columns)")
    print("=" * 78)
    print(f"{'Bench':8s} {'Tool':5s}  "
          f"{'Agent':>6s} {'Oracle':>6s} {'Boundary':>8s} {'Trace':>6s} {'Total':>6s}")
    grand = {c: 0 for c in FAIL_CATS}
    for bench in BENCHS:
        for tool in TOOLS:
            s = fail_stats[(tool, bench)]
            for c in FAIL_CATS:
                grand[c] += s[c]
            print(f"{BENCH_LABEL[bench]:8s} {TOOL_LABEL[tool]:5s}  "
                  f"{s['agent']:6d} {s['oracle']:6d} {s['boundary']:8d} "
                  f"{s['trace']:6d} {total(s):6d}")
        print()
    print(f"{'TOTAL':14s}  "
          f"{grand['agent']:6d} {grand['oracle']:6d} {grand['boundary']:8d} "
          f"{grand['trace']:6d} {total(grand):6d}")

    print("\n--- LaTeX (Agent & Oracle & Boundary & Trace & Failed-Total) ---")
    for bench in BENCHS:
        print(f"% {BENCH_LABEL[bench]}")
        for tool in TOOLS:
            s = fail_stats[(tool, bench)]
            print(f"& {TOOL_LABEL[tool]:5s} & {s['agent']} & {s['oracle']} & "
                  f"{s['boundary']} & {s['trace']} & {total(s)} \\\\")
    print("% Total")
    print(f"& {'Total':5s} & {grand['agent']} & {grand['oracle']} & "
          f"{grand['boundary']} & {grand['trace']} & {total(grand)} \\\\")


def _print_success_table(succ_stats):
    def total(s):
        return s['oracle'] + s['boundary'] + s['trace']

    print("\n" + "=" * 78)
    print("SUCCESS VALIDATION-FEEDBACK BREAKDOWN  (tab:rq1_success)")
    print("=" * 78)
    print(f"{'Bench':8s} {'Tool':5s}  "
          f"{'V1(orc)':>7s} {'V2(bnd)':>7s} {'V3(trc)':>7s} {'Total':>6s}")
    grand = {'oracle': 0, 'boundary': 0, 'trace': 0}
    for bench in BENCHS:
        for tool in TOOLS:
            s = succ_stats[(tool, bench)]
            for k in grand:
                grand[k] += s[k]
            print(f"{BENCH_LABEL[bench]:8s} {TOOL_LABEL[tool]:5s}  "
                  f"{s['oracle']:7d} {s['boundary']:7d} {s['trace']:7d} "
                  f"{total(s):6d}")
        print()
    print(f"{'TOTAL':14s}  "
          f"{grand['oracle']:7d} {grand['boundary']:7d} {grand['trace']:7d} "
          f"{total(grand):6d}")

    print("\n--- LaTeX (V1 & V2 & V3 & Total) ---")
    for bench in BENCHS:
        print(f"% {BENCH_LABEL[bench]}")
        for tool in TOOLS:
            s = succ_stats[(tool, bench)]
            print(f"& {TOOL_LABEL[tool]:5s} & {s['oracle']} & {s['boundary']} & "
                  f"{s['trace']} & {total(s)} \\\\")
    print("% Total")
    print(f"& {'Total':5s} & {grand['oracle']} & {grand['boundary']} & "
          f"{grand['trace']} & {total(grand)} \\\\")


def _print_success_retry_stats(succ_stats):
    print("\n" + "=" * 78)
    print("SUCCESS RETRY STATS  (retry = any feedback round before success)")
    print("=" * 78)
    print(f"{'Bench':8s} {'Tool':5s}  "
          f"{'#success':>8s} {'#w/feedback':>11s} {'%w/feedback':>11s} "
          f"{'#retries':>8s} {'avg retries':>11s}")

    def emit(label, s):
        n = s['n_success']
        wf = s['n_with_feedback']
        rt = s['n_retries']
        pct = 100 * wf / n if n else 0.0
        avg = rt / n if n else 0.0
        print(f"{label:14s} "
              f"{n:8d} {wf:11d} {pct:10.1f}% {rt:8d} {avg:11.2f}")

    agg = {'n_success': 0, 'n_with_feedback': 0, 'n_retries': 0}
    for bench in BENCHS:
        for tool in TOOLS:
            emit(f"{BENCH_LABEL[bench]:8s} {TOOL_LABEL[tool]}", succ_stats[(tool, bench)])
            for k in agg:
                agg[k] += succ_stats[(tool, bench)][k]
        print()
    emit("ALL", agg)


if __name__ == '__main__':
    main()
