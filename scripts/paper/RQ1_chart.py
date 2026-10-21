#!/usr/bin/env python3
"""
RQ1_chart.py
============
Bar chart companion to RQ1_table.py.

Each *reasoned* boundary (a successful boundary that ran the oracle and pruned
others) defines an equivalence group. RQ1_table.py reports the aggregate
"Grouped" count per tool/benchmark; this script breaks that number down per
reasoned boundary.

The chart shows, separately for each tool, one bar per reasoned boundary that
grouped at least one other boundary. Bars are sorted by group size and labelled
R1, R2, ... on the x-axis. The bar height is the number of boundaries that
reasoned boundary grouped.

Grouping mirrors RQ1_table.py exactly:
  * only *successful* pruning boundaries count (success-gated),
  * pruned boundaries are restricted to the program subsets we care about,
  * a pruned boundary is credited once per tool (global dedup) to the first
    reasoned boundary that claims it, so the bar heights sum to the table's
    "Grouped" total.

Run with the scripts venv:

    scripts/venv/bin/python scripts/RQ1_chart.py
"""

from __future__ import annotations

from pathlib import Path
import json
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

PIPELINE_FILE = "pipeline_result.json"
PRUNING_FILE  = "pruning_report.json"

CHART_PATH = Path(__file__).resolve().parent / "RQ1_chart.png"

TOOL_LABEL = {'wala': 'WALA', 'opal': 'OPAL', 'soot': 'Soot'}
TOOL_COLOR = {'wala': '#4C72B0', 'opal': '#DD8452', 'soot': '#55A868'}

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


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def results_dir(tool: str, bench: str) -> Path:
    return OUTPUT_PATH / f"results_{tool}_{bench}"


def discover_cats_programs() -> set[str]:
    progs: set[str] = set()
    for tool in TOOLS:
        d = results_dir(tool, 'cats')
        if d.exists():
            progs.update(p.name for p in d.iterdir() if p.is_dir())
    return progs


def edge_dirs(program_dir: Path) -> list[Path]:
    return [p for p in program_dir.glob("*/*/*") if p.is_dir()]


def grouped_counts_per_tool() -> dict[str, list[int]]:
    """For each tool, the per-reasoned-boundary grouped counts (>0 only)."""
    cats_programs = discover_cats_programs()

    def bench_of(program_name: str) -> str | None:
        if program_name in NJR_25:
            return 'njr'
        if program_name in cats_programs:
            return 'cats'
        if program_name in XCORPUS_SUBSET:
            return 'xcorpus'
        return None

    def is_relevant(bench: str, program_name: str) -> bool:
        if bench == 'njr':
            return program_name in NJR_25
        if bench == 'cats':
            return program_name in cats_programs
        if bench == 'xcorpus':
            return program_name in XCORPUS_SUBSET
        return False

    counts: dict[str, list[int]] = {}

    for tool in TOOLS:
        # Boundaries that have their own verdict (reasoned/failed) must never be
        # counted as grouped — match the table's behaviour.
        verdict_keys: set = set()
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
                    if read_json(edge_dir / PIPELINE_FILE) is not None:
                        verdict_keys.add((program, edge_dir.name))

        # Walk every successful pruning report and credit each pruned boundary
        # once, to the first reasoned boundary that claims it.
        grouped_keys: set = set()
        per_reasoned: list[int] = []

        for bench in BENCHS:
            d = results_dir(tool, bench)
            if not d.exists():
                continue
            for report_path in d.glob("*/*/*/*/" + PRUNING_FILE):
                trigger_dir = report_path.parent
                trigger_result = read_json(trigger_dir / PIPELINE_FILE)
                if not trigger_result or not trigger_result.get("success", False):
                    continue

                report = read_json(report_path)
                if report is None:
                    continue

                claimed = 0
                for entry in report.get("pruned_boundaries", []):
                    prog = entry.get("program_name")
                    b_id = entry.get("boundary_id")
                    if not prog or not b_id:
                        continue
                    if bench_of(prog) is None:
                        continue
                    key = (prog, b_id)
                    if key in verdict_keys or key in grouped_keys:
                        continue
                    grouped_keys.add(key)
                    claimed += 1

                if claimed > 0:
                    per_reasoned.append(claimed)

        per_reasoned.sort(reverse=True)
        counts[tool] = per_reasoned

    return counts


def main():
    counts = grouped_counts_per_tool()

    # Console summary.
    print("=== Grouped boundaries per reasoned boundary ===")
    for tool in TOOLS:
        c = counts[tool]
        print(f"{TOOL_LABEL[tool]:5s}  reasoned-groupers={len(c):3d}  "
              f"total-grouped={sum(c):4d}  "
              f"max={max(c) if c else 0:3d}")

    # One subplot per tool (the R1.. labelling is per-tool).
    fig, axes = plt.subplots(
        len(TOOLS), 1,
        figsize=(12, 9),
        constrained_layout=True,
    )
    if len(TOOLS) == 1:
        axes = [axes]

    for ax, tool in zip(axes, TOOLS):
        c = counts[tool]
        xs = range(1, len(c) + 1)
        ax.bar(xs, c, color=TOOL_COLOR[tool], edgecolor='black', linewidth=0.3)
        ax.set_title(
            f"{TOOL_LABEL[tool]}  "
            f"({len(c)} reasoned boundaries grouped {sum(c)} others)",
            fontsize=11, loc='left',
        )
        ax.set_ylabel("Grouped boundaries")
        ax.set_xlim(0.4, len(c) + 0.6 if c else 1)
        ax.margins(x=0.01)
        ax.grid(axis='y', linestyle=':', alpha=0.5)

        # Label x ticks R1, R2, ...; thin them out when there are many bars.
        if c:
            step = max(1, len(c) // 30)
            ticks = list(range(1, len(c) + 1, step))
            ax.set_xticks(ticks)
            ax.set_xticklabels([f"R{i}" for i in ticks], rotation=90, fontsize=7)

    axes[-1].set_xlabel("Reasoned boundary (sorted by group size)")
    fig.suptitle(
        "RQ1 — boundaries grouped per reasoned boundary, by tool",
        fontsize=13, fontweight='bold',
    )

    # fig.savefig(CHART_PATH, dpi=150)
    # print(f"\nSaved chart -> {CHART_PATH}")


if __name__ == '__main__':
    main()
