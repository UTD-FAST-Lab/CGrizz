#!/usr/bin/env python3
"""
RQ1_table.py
============
Fills the RQ1 results table (tab:rq1_results) in the paper.

For every (benchmark, tool) it reports, restricted to the program subsets we
care about:

    Total     = Reasoned + Grouped + Failed
    Reasoned  = boundaries we ran the pipeline on successfully
                (pipeline_result.json -> success == true)
    Grouped   = boundaries deduplicated ("pruned") by some *successful*
                boundary of the same tool. The pruning boundary may live in a
                different benchmark (e.g. a NJR boundary can be grouped by a
                cats boundary) — the only requirement is that the boundary that
                pruned it is successful.
    Failed    = boundaries we analyzed but did not succeed on
                (pipeline_result.json -> success == false)

Boundaries with no pipeline_result.json and that were never grouped are still
"pending" (left to analyze) and are reported separately — they are NOT part of
the Total column.

This mirrors the orchestrator's deduplication model: a successful boundary
writes a pruning_report.json listing the boundaries it pruned (across programs /
benchmarks of the same tool). Grouping is gathered globally from those reports.

Program subsets:
    NJR     -> the 25 fixed programs (NJR_25 below)
    Cats    -> all cats programs found on disk
    XCorpus -> a fixed subset (XCORPUS_SUBSET below)
"""

from __future__ import annotations

from pathlib import Path
import json
from collections import defaultdict

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

# Table layout order.
TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

PIPELINE_FILE = "pipeline_result.json"
PRUNING_FILE  = "pruning_report.json"

# ── Program subsets we care about ─────────────────────────────────────────────

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

# xcorpus subset we care about (same across all tools).
XCORPUS_SUBSET = {
    'javacc-5.0',
    'nekohtml-1.9.14',
    'fitjava-1.1',
}


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def results_dir(tool: str, bench: str) -> Path:
    return OUTPUT_PATH / f"results_{tool}_{bench}"


def discover_cats_programs() -> set[str]:
    """Union of all cats program directory names across tools."""
    progs: set[str] = set()
    for tool in TOOLS:
        d = results_dir(tool, 'cats')
        if d.exists():
            progs.update(p.name for p in d.iterdir() if p.is_dir())
    return progs


def edge_dirs(program_dir: Path) -> list[Path]:
    """Boundary directories: <program>/<TOOL>/<algo>/<boundary>."""
    return [p for p in program_dir.glob("*/*/*") if p.is_dir()]


def main():
    cats_programs = discover_cats_programs()

    # program_name -> benchmark, but only for the programs we care about.
    def bench_of(program_name: str) -> str | None:
        if program_name in NJR_25:
            return 'njr'
        if program_name in cats_programs:
            return 'cats'
        if program_name in XCORPUS_SUBSET:
            return 'xcorpus'
        return None  # not in any subset we care about

    def is_relevant(bench: str, program_name: str) -> bool:
        if bench == 'njr':
            return program_name in NJR_25
        if bench == 'cats':
            return program_name in cats_programs
        if bench == 'xcorpus':
            return program_name in XCORPUS_SUBSET
        return False

    stats = defaultdict(lambda: {
        "reasoned": 0,   # success == true
        "failed":   0,   # success == false
        "grouped":  0,   # deduplicated by a successful boundary
        "pending":  0,   # no result file and not grouped (left to analyze)
    })

    # Track which boundaries already have a verdict (reasoned/failed) so a
    # grouped entry is never double-counted, and grouped is deduplicated.
    #   verdict_keys[tool] = {(program_name, boundary_id), ...}
    verdict_keys: dict[str, set] = defaultdict(set)
    #   grouped_keys[tool] = {(program_name, boundary_id), ...}
    grouped_keys: dict[str, set] = defaultdict(set)
    # Remember edge dirs lacking a result, to settle "pending" after grouping.
    #   no_result[tool] = {(program_name, boundary_id): bench}
    no_result: dict[str, dict] = defaultdict(dict)

    # ── Pass 1: reasoned / failed / (tentative) pending, per relevant program ──
    for tool in TOOLS:
        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            for program_dir in d.iterdir():
                if not program_dir.is_dir():
                    continue
                program = program_dir.name
                if not is_relevant(bench, program):
                    continue

                for edge_dir in edge_dirs(program_dir):
                    b_id = edge_dir.name
                    pipeline_data = read_json(edge_dir / PIPELINE_FILE)

                    if pipeline_data is None:
                        # No (or unreadable) result file -> tentatively pending.
                        no_result[tool][(program, b_id)] = bench
                        continue

                    verdict_keys[tool].add((program, b_id))
                    if pipeline_data.get("success", False):
                        stats[(tool, bench)]["reasoned"] += 1
                    else:
                        stats[(tool, bench)]["failed"] += 1

    # ── Pass 2: grouped — scan every pruning report of the tool, globally ──
    # A grouped boundary is one pruned by a *successful* boundary. The pruning
    # boundary may be in a different benchmark of the same tool.
    for tool in TOOLS:
        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            # report lives at <program>/<TOOL>/<algo>/<boundary>/pruning_report.json
            for report_path in d.glob("*/*/*/*/" + PRUNING_FILE):
                trigger_dir = report_path.parent

                # The pruning boundary must itself be successful.
                trigger_result = read_json(trigger_dir / PIPELINE_FILE)
                if not trigger_result or not trigger_result.get("success", False):
                    continue

                report = read_json(report_path)
                if report is None:
                    continue

                for entry in report.get("pruned_boundaries", []):
                    prog = entry.get("program_name")
                    b_id = entry.get("boundary_id")
                    if not prog or not b_id:
                        continue
                    target_bench = bench_of(prog)
                    if target_bench is None:
                        continue  # pruned program not in any subset we care about

                    key = (prog, b_id)
                    if key in verdict_keys[tool]:
                        continue  # already counted as reasoned/failed
                    if key in grouped_keys[tool]:
                        continue  # already counted as grouped

                    grouped_keys[tool].add(key)
                    stats[(tool, target_bench)]["grouped"] += 1

    # ── Settle pending: drop ones that turned out to be grouped ──
    for tool in TOOLS:
        for (prog, b_id), bench in no_result[tool].items():
            if (prog, b_id) in grouped_keys[tool]:
                continue  # grouped, not pending
            stats[(tool, bench)]["pending"] += 1

    # ── Output ──
    def total(s):  # Total column = Reasoned + Grouped + Failed
        return s["reasoned"] + s["grouped"] + s["failed"]

    def fmt_row(label, s):
        return (
            f"{label:28s} "
            f"Total={total(s):4d}  "
            f"Reasoned={s['reasoned']:4d}  "
            f"Grouped={s['grouped']:4d}  "
            f"Failed={s['failed']:4d}     "
            f"(pending={s['pending']:4d})"
        )

    print("\n=== Per Benchmark / Tool ===")
    for bench in BENCHS:
        for tool in TOOLS:
            print(fmt_row(f"{bench:8s} {tool}", stats[(tool, bench)]))
        print()

    print("=== Per Tool (overall, across the three benchmarks) ===")
    grand = {"reasoned": 0, "failed": 0, "grouped": 0, "pending": 0}
    for tool in TOOLS:
        agg = {"reasoned": 0, "failed": 0, "grouped": 0, "pending": 0}
        for bench in BENCHS:
            for k in agg:
                agg[k] += stats[(tool, bench)][k]
        for k in grand:
            grand[k] += agg[k]
        print(fmt_row(tool, agg))
    print(fmt_row("TOTAL", grand))

    # ── LaTeX rows (Total, Reasoned, Grouped, Failed-Total) ──
    tool_label = {'wala': 'WALA', 'opal': 'OPAL', 'soot': 'Soot'}
    bench_label = {'cats': 'Cats', 'njr': 'NJR', 'xcorpus': 'XCorpus'}
    print("\n=== LaTeX rows (Total | Reasoned | Grouped | Failed Total) ===")
    print("% columns: Tool & Total & Reasoned & Grouped & ... & Failed-Total")
    for bench in BENCHS:
        print(f"% --- {bench_label[bench]} ---")
        for tool in TOOLS:
            s = stats[(tool, bench)]
            print(
                f"& {tool_label[tool]:5s} & {total(s)} & "
                f"{s['reasoned']} & {s['grouped']} & "
                f" & & & & {s['failed']} \\\\"
            )

    # Grand total across all tools and benchmarks.
    g = {"reasoned": 0, "failed": 0, "grouped": 0}
    for bench in BENCHS:
        for tool in TOOLS:
            for k in g:
                g[k] += stats[(tool, bench)][k]
    print("% --- Total ---")
    g_total = g["reasoned"] + g["grouped"] + g["failed"]
    print(
        f"& {'Total':5s} & {g_total} & "
        f"{g['reasoned']} & {g['grouped']} & "
        f" & & & & {g['failed']} \\\\"
    )


if __name__ == '__main__':
    main()
