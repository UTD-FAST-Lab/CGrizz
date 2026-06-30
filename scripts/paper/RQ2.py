#!/usr/bin/env python3
"""
RQ2.py
======
Two analyses on the equivalence groups produced by the pipeline's oracle
deduplication. Run with the scripts venv:

    scripts/venv/bin/python scripts/RQ2.py

Background — equivalence groups
-------------------------------
When a boundary is analyzed successfully (reasoned), its root-cause oracle is
run against the remaining boundaries and prunes ("groups") every boundary that
shares the same root cause. So each successful boundary defines an equivalence
group:

    group = { the reasoned boundary } ∪ { all boundaries it grouped }

A boundary is "analyzable for free" if it falls into a group whose root cause
was already discovered — i.e. some member of its group was actually reasoned.

Groups are built from the pruning_report.json files (success-gated) and merged
with union-find so each boundary lands in exactly one group. Members are
restricted to the program subsets we care about (same as RQ1): the 25 NJR
programs, all cats programs, and the xcorpus subset.

Analysis 1 — generalization to future programs (tab:rq2_recall)
---------------------------------------------------------------
5-fold cross-validation with an 80/20 train/test split over *programs* pooled
across all benchmarks. For each fold, a group counts as "seen" if any of its
members belongs to a training program. Coverage (recall) = fraction of the test
programs' boundaries that fall into a seen group. Reported per tool, averaged
over the 5 folds.

Analysis 2 — train on small programs, test on real-world
--------------------------------------------------------
How well do small/synthetic programs (cats) generalize to real-world programs
(njr, xcorpus)? We count how many real-world boundaries land in a group that
also contains a cats boundary — i.e. real-world boundaries that would be covered
for free by having analyzed cats.
"""

from pathlib import Path
import json
import numpy as np
from sklearn.model_selection import KFold

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')

TOOLS  = ['wala', 'opal', 'soot']
BENCHS = ['cats', 'njr', 'xcorpus']

PIPELINE_FILE = "pipeline_result.json"
PRUNING_FILE  = "pruning_report.json"

N_SPLITS    = 5
RANDOM_SEED = 42

# ── Program subsets we care about (same as RQ1) ───────────────────────────────

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


def discover_programs(bench: str) -> set:
    progs = set()
    for tool in TOOLS:
        d = results_dir(tool, bench)
        if d.exists():
            progs.update(p.name for p in d.iterdir() if p.is_dir())
    return progs


# Filled in main() once program directories are discovered.
CATS_PROGRAMS:    set = set()
XCORPUS_PROGRAMS: set = set()


def bench_of(program_name: str):
    """Benchmark of a program if it is in a subset we care about, else None."""
    if program_name in NJR_25:
        return 'njr'
    if program_name in CATS_PROGRAMS:
        return 'cats'
    if program_name in XCORPUS_SUBSET:
        return 'xcorpus'
    return None


def bench_classify(program_name: str):
    """Benchmark of any program (no subset restriction); None if unknown."""
    if program_name.startswith('url'):
        return 'njr'
    if program_name in XCORPUS_PROGRAMS:
        return 'xcorpus'
    if program_name in CATS_PROGRAMS:
        return 'cats'
    return None


def is_relevant(program_name: str) -> bool:
    return bench_of(program_name) is not None


def keep_unrestricted_realworld(program_name: str) -> bool:
    """cats (subset) + every njr + every xcorpus program."""
    return (program_name in CATS_PROGRAMS
            or program_name.startswith('url')
            or program_name in XCORPUS_PROGRAMS)


# ── Union-find for partitioning members into equivalence groups ───────────────

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:        # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_groups(tool: str, keep=is_relevant) -> list:
    """
    Return equivalence groups for one tool as a list of sets of
    (program_name, boundary_id), keeping only members for which keep(program)
    is true, merged so each boundary belongs to exactly one group.
    """
    uf = UnionFind()

    for bench in BENCHS:
        d = results_dir(tool, bench)
        if not d.exists():
            continue
        for program_dir in d.iterdir():
            if not program_dir.is_dir():
                continue
            for edge_dir in (p for p in program_dir.glob("*/*/*") if p.is_dir()):
                pdata = read_json(edge_dir / PIPELINE_FILE)
                if not pdata or not pdata.get("success", False):
                    continue

                members = set()
                ident = pdata.get("identity", {})
                if ident.get("program_name") and ident.get("boundary_id"):
                    members.add((ident["program_name"], ident["boundary_id"]))

                report = read_json(edge_dir / PRUNING_FILE)
                if report:
                    tb = report.get("triggered_by", {})
                    if tb.get("program_name") and tb.get("boundary_id"):
                        members.add((tb["program_name"], tb["boundary_id"]))
                    for b in report.get("pruned_boundaries", []):
                        if b.get("program_name") and b.get("boundary_id"):
                            members.add((b["program_name"], b["boundary_id"]))

                # Keep only boundaries that pass the membership predicate.
                members = {m for m in members if keep(m[0])}
                if not members:
                    continue

                members = list(members)
                for m in members:
                    uf.find(m)
                for m in members[1:]:
                    uf.union(members[0], m)

    components: dict = {}
    for member in list(uf.parent.keys()):
        components.setdefault(uf.find(member), set()).add(member)
    return list(components.values())


# ── Analysis 1: 5-fold cross-validation recall ────────────────────────────────

def cross_validation_recall(groups: list, verbose: bool = False) -> tuple:
    """Coverage over 5 folds (80/20 split over programs).
    Returns (mean, std) of the per-fold recalls (std is population std)."""
    programs = sorted({p for g in groups for (p, _) in g})
    if len(programs) < N_SPLITS:
        return 0.0, 0.0

    programs_arr = np.array(programs)
    kf = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_SEED)

    recalls = []
    for fold, (train_idx, test_idx) in enumerate(kf.split(programs_arr)):
        train_progs = set(programs_arr[train_idx])
        test_progs  = set(programs_arr[test_idx])

        test_edges = 0
        covered    = 0
        for group in groups:
            seen = any(p in train_progs for (p, _) in group)
            n_test = sum(1 for (p, _) in group if p in test_progs)
            test_edges += n_test
            if seen:
                covered += n_test

        recall = covered / test_edges if test_edges else 0.0
        recalls.append(recall)
        if verbose:
            print(f"    fold {fold + 1}: test_edges={test_edges:4d} "
                  f"covered={covered:4d}  recall={recall:.4f}")

    if not recalls:
        return 0.0, 0.0
    return float(np.mean(recalls)), float(np.std(recalls))


# ── Analysis 2: cats-trained coverage of real-world boundaries ────────────────

def cats_to_realworld_coverage(groups: list, classify=bench_of):
    """
    Count real-world (njr, xcorpus) boundaries that share a group with a cats
    boundary — i.e. would be covered for free by having analyzed cats.
    `classify` maps a program name to its benchmark.
    Returns (covered, total).
    """
    total = covered = 0
    for group in groups:
        has_cats = any(classify(p) == 'cats' for (p, _) in group)
        for (p, _) in group:
            if classify(p) in ('njr', 'xcorpus'):
                total += 1
                if has_cats:
                    covered += 1
    return covered, total


# ── Main ──────────────────────────────────────────────────────────────────────

TOOL_LABEL = {'wala': 'WALA', 'opal': 'OPAL', 'soot': 'Soot'}


def main():
    global CATS_PROGRAMS, XCORPUS_PROGRAMS
    CATS_PROGRAMS    = discover_programs('cats')
    XCORPUS_PROGRAMS = discover_programs('xcorpus')

    tool_groups = {tool: build_groups(tool) for tool in TOOLS}

    # ── Analysis 1 ──
    print("=" * 70)
    print("ANALYSIS 1: 5-fold CV recall, 80/20 train/test  (tab:rq2_recall)")
    print("=" * 70)
    cv = {}
    for tool in TOOLS:
        groups = tool_groups[tool]
        n_groups   = len(groups)
        n_edges    = sum(len(g) for g in groups)
        n_programs = len({p for g in groups for (p, _) in g})
        print(f"\n{TOOL_LABEL[tool]}  (groups={n_groups}, edges={n_edges}, "
              f"programs={n_programs})")
        cv[tool] = cross_validation_recall(groups, verbose=True)
        mean, std = cv[tool]
        print(f"  -> avg coverage = {mean * 100:.1f}%  (std = {std * 100:.1f}%)")

    print("\n--- Table (Tool | Avg. Coverage % | Std %) ---")
    for tool in TOOLS:
        mean, std = cv[tool]
        print(f"{TOOL_LABEL[tool]:5s} & {mean * 100:.1f} $\\pm$ {std * 100:.1f} \\\\")

    # ── Analysis 2 ──
    print("\n" + "=" * 70)
    print("ANALYSIS 2: train on cats, cover real-world (njr + xcorpus)")
    print("=" * 70)
    print("\n[a] Real-world restricted to the studied subset "
          "(25 NJR + xcorpus subset):")
    print(f"{'Tool':6s} {'covered':>8s} {'realworld':>10s} {'coverage%':>10s}")
    agg_cov = agg_tot = 0
    for tool in TOOLS:
        covered, total = cats_to_realworld_coverage(tool_groups[tool])
        agg_cov += covered
        agg_tot += total
        pct = 100 * covered / total if total else 0.0
        print(f"{TOOL_LABEL[tool]:6s} {covered:8d} {total:10d} {pct:9.1f}%")
    overall = 100 * agg_cov / agg_tot if agg_tot else 0.0
    print(f"{'ALL':6s} {agg_cov:8d} {agg_tot:10d} {overall:9.1f}%")

    print("\n[b] Real-world = ALL njr + ALL xcorpus programs (unrestricted):")
    print(f"{'Tool':6s} {'covered':>8s} {'realworld':>10s} {'coverage%':>10s}")
    agg_cov = agg_tot = 0
    for tool in TOOLS:
        groups = build_groups(tool, keep=keep_unrestricted_realworld)
        covered, total = cats_to_realworld_coverage(groups, classify=bench_classify)
        agg_cov += covered
        agg_tot += total
        pct = 100 * covered / total if total else 0.0
        print(f"{TOOL_LABEL[tool]:6s} {covered:8d} {total:10d} {pct:9.1f}%")
    overall = 100 * agg_cov / agg_tot if agg_tot else 0.0
    print(f"{'ALL':6s} {agg_cov:8d} {agg_tot:10d} {overall:9.1f}%")


if __name__ == "__main__":
    main()
