from pathlib import Path
import os
import json
from collections import defaultdict

OUTPUT_PATH = Path('/20TB/mohammad/agentic_cg_unsoundness_icse27')
TOOLS = ['soot']
BENCHS = ['cats', 'njr']



def is_amount_25(program_name):

    return program_name in [
        'url8af84d296f_mrcosta_gantt_generator_tgz-pJ8-org_gantt_generator_mrcpsp_RunJ8', 'url70997c83aa_Xilaew_risiko_tgz-pJ8-risiko_XMI2XMLtransformerJ8', 'urldf54784279_tonyj321_Root_Play_tgz-pJ8-hep_io_root_output_demo_HistogramDemoJ8', 'url4e79c36700_JacenLakiir_Tivoo_tgz-pJ8-MainJ8', 'url088e9d87be_jpush_jpush_java_library_tgz-pJ8-cn_jpush_example_JPushClientExampleJ8', 'url78eec0e0b6_Orikuro_vic_tgz-pJ8-net_nexon_vindictus_itemcomparer_inport_StaticImportJ8', 'url633772aa30_isma3l_Tp2_tgz-pJ8-ar_fiuba_tecnicas_framework_Pruebas_Casos1_MainJ8', 'url542ae48576_tonyheadford_m68k_tgz-pJ8-m68k_MonitorJ8', 'url0f7f8f6435_TimmiTBy_Epam_tgz-pJ8-com_epam_electricalappliance_RunnerJ8', 'urlc58b5c5b73_emiliano_fiuba_2013_2c_7510_tgz-pJ8-test_java_FrameworkTestsJ8', 'url901cc5721b_shidasan_KonohaCoffee_tgz-pJ8-org_KonohaScript_KonohaJ8', 'urlbf4fd38f68_rduerig_StrategyBDD_tgz-pJ8-com_strategy_util_TestMainJ8', 'url5a01f2c1d9_core9_core_tgz-pJ8-io_core9_core_boot_BootstrapFrameworkJ8', 'url3426a97ee4_rtreffer_minidns_tgz-pJ8-de_measite_minidns_minidnsrepl_MiniDnsReplJ8', 'url4df616fce9_s3e0n1g1_prototype3_tgz-pJ8-Trading_Engine_MainJ8', 'url28dfd00382_byang223_TurnBAPI_tgz-pJ8-units_upgrades_UpgradeTestJ8', 'url9e7e48285f_nalbachk_MidiToPhaseShift_tgz-pJ8-com_my_main_MainJ8', 'urlac20d257ff_zhongdj_Madz_Stochastic_tgz-pJ8-net_madz_test_stochastic_core_impl_DynamicDemoJ8', 'urlcf7851e92b_Triploblastic_CSCD350_FinalProject_tgz-pJ8-MazeGame_MazeJ8', 'url27db8982cd_leohinterlang_argFace_tgz-pJ8-com_fidelis_argface_ArgSampleJ8', 'url1d5e9d990f_Telsis_jOCP_tgz-pJ8-com_telsis_jocp_sampleApp_BasicOCPAppJ8', 'urlb879d9e865_ogotalski_elevator_tgz-pJ8-ElevatorTaskJ8', 'url270fc4f5ee_ykcilborw_Joust_tgz-pJ8-fri_patterns_interpreter_parsergenerator_examples_HowToOverrideLexerImplJ8', 'url708d477f92_zSakare_SENG3011_tgz-pJ8-test_runner_TestRunnerJ8', 'url400631cdd0_ludaiqian_appliedxml_tgz-pJ8-test_simples_TestJ8'
    ]


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main():

    bench_stats = defaultdict(
        lambda: {
            "true": 0,
            "false": 0,
            "true_dedup": 0,
            "false_dedup": 0,
            "not_run": 0,
            "all_edges": 0,
        }
    )

    tool_stats = defaultdict(
        lambda: {
            "true": 0,
            "false": 0,
            "true_dedup": 0,
            "false_dedup": 0,
            "not_run": 0,
            "all_edges": 0,
        }
    )


    def nested():
        return defaultdict(list)

    detailed = defaultdict(lambda: {
        "true": nested(),
        "false": nested(),
        "not_run": nested(),
        "true_dedup": nested(),
        "false_dedup": nested(),
    })

    for tool in TOOLS:

        is_njr = False

        for bench in BENCHS:

            if bench == "njr":
                is_njr = True

            results_dir = OUTPUT_PATH / f"results_{tool}_{bench}"

            if not results_dir.exists():
                continue

            for program_dir in results_dir.iterdir():

                if not program_dir.is_dir():
                    continue

                edge_dirs = [
                    p for p in program_dir.glob("*/*/*")
                    if p.is_dir()
                ]


                if is_njr and not is_amount_25(program_dir.name):
                    # read each edge's pruning report if exists and check if the pruned edges are from programs in the list of 25 programs. if so, count that as a true for the program in the 25 list. if there are more than one pruned edge from a single program in that list, count the rest as true_dedup for that program. 
                    for edge_dir in edge_dirs:

                        pruning_file = edge_dir / "pruning_report.json"
                        if not pruning_file.exists():
                            continue

                        pruning_data = read_json(pruning_file)
                        if pruning_data is None:
                            continue
                        
                        pipeline_file = edge_dir / "pipeline_result.json"
                        if pipeline_file.exists():
                            pipeline_data = read_json(pipeline_file)
                            success = pipeline_data.get("success", False)
                        
                        else:
                            continue

                        if not success:
                            continue

                        pruned_edges = pruning_data.get("pruned_boundaries", [])

                        # first pruned edge per 25-list program -> true; rest -> true_dedup
                        seen_25_programs = set()

                        for pruned_edge in pruned_edges:
                            pruned_edge_program = pruned_edge.get("program_name")
                            if not pruned_edge_program:
                                continue
                            if 'url' not in pruned_edge_program:        # only njr programs
                                continue
                            if not is_amount_25(pruned_edge_program):   # only the 25-list ones
                                continue

                            if pruned_edge_program not in seen_25_programs:
                                seen_25_programs.add(pruned_edge_program)
                                bench_stats[(tool, bench)]["true"] += 1
                                tool_stats[tool]["true"] += 1
                                detailed[f"{tool}/{bench}"]["true"][pruned_edge_program].append(edge_dir.name)
                            else:
                                bench_stats[(tool, bench)]["true_dedup"] += 1
                                tool_stats[tool]["true_dedup"] += 1
                                detailed[f"{tool}/{bench}"]["true_dedup"][pruned_edge_program].append({
                                    "edge": edge_dir.name,
                                    "pruned_count": 1
                                })


                else:
                    bench_stats[(tool, bench)]["all_edges"] += len(edge_dirs)
                    tool_stats[tool]["all_edges"] += len(edge_dirs)

                    for edge_dir in edge_dirs:

                        pipeline_file = edge_dir / "pipeline_result.json"
                        pruning_file = edge_dir / "pruning_report.json"

                        program = program_dir.name
                        edge = str(edge_dir.relative_to(results_dir))

                        #
                        # NOT RUN (UNCHANGED LOGIC)
                        #
                        if not pipeline_file.exists():
                            bench_stats[(tool, bench)]["not_run"] += 1
                            tool_stats[tool]["not_run"] += 1

                            detailed[f"{tool}/{bench}"]["not_run"][program].append(edge_dir.name)
                            continue

                        pipeline_data = read_json(pipeline_file)

                        if pipeline_data is None:
                            bench_stats[(tool, bench)]["not_run"] += 1
                            tool_stats[tool]["not_run"] += 1

                            detailed[f"{tool}/{bench}"]["not_run"][program].append(edge_dir.name)

                            continue

                        success = pipeline_data.get("success", False)

                        pruned_count = 0
                        if pruning_file.exists():
                            pruning_data = read_json(pruning_file)
                            if pruning_data is not None:

                                pruned_edges = pruning_data.get("pruned_boundaries", [])
                                
                                for pruned_edge in pruned_edges:
                                    pruned_edge_program = pruned_edge.get("program_name")
                                    if 'url' not in pruned_edge_program:
                                        pruned_count += 1
                                    else:
                                        # njr programs
                                        if is_amount_25(pruned_edge_program):
                                            pruned_count += 1                                
                                    

                        #
                        # TRUE (UNCHANGED LOGIC)
                        #
                        if success:
                            bench_stats[(tool, bench)]["true"] += 1
                            bench_stats[(tool, bench)]["true_dedup"] += pruned_count

                            tool_stats[tool]["true"] += 1
                            tool_stats[tool]["true_dedup"] += pruned_count

                            detailed[f"{tool}/{bench}"]["true"][program].append(edge_dir.name)

                            if pruned_count > 0:
                                detailed[f"{tool}/{bench}"]["true_dedup"][program].append({
                                    "edge": edge_dir.name,
                                    "pruned_count": pruned_count
                                })

                        #
                        # FALSE (UNCHANGED LOGIC)
                        #
                        else:
                            bench_stats[(tool, bench)]["false"] += 1
                            bench_stats[(tool, bench)]["false_dedup"] += pruned_count

                            tool_stats[tool]["false"] += 1
                            tool_stats[tool]["false_dedup"] += pruned_count

                            detailed[f"{tool}/{bench}"]["false"][program].append(edge_dir.name)

                            if pruned_count > 0:
                                detailed[f"{tool}/{bench}"]["false_dedup"][program].append({
                                    "edge": edge_dir.name,
                                    "pruned_count": pruned_count
                                })

    #
    # EXACT SAME PRINTING LOGIC (UNCHANGED)
    #
    print("\n=== Per Tool / Benchmark ===")

    for tool in TOOLS:
        for bench in BENCHS:

            s = bench_stats[(tool, bench)]

            display_not_run = (
                s["not_run"]
                - s["true_dedup"]
                - s["false_dedup"]
            )

            total = (
                s["true"]
                + s["false"]
                + display_not_run
                + s["false_dedup"]
                + s["true_dedup"]
            )

            print(
                f"{tool:5s} {bench:7s}\t "
                f"True={s['true']:4d}\t "
                f"False={s['false']:4d}\t "
                f"True_dedup={s['true_dedup']:6d}\t "
                f"False_dedup={s['false_dedup']:6d}\t "
                f"Not_run={display_not_run:4d}\t "
                f"Total={total:4d}\t"
                f"all_edges={s['all_edges']:4d}"
            )

    print("\n=== Per Tool Overall ===")

    for tool in TOOLS:

        s = tool_stats[tool]

        display_not_run = (
            s["not_run"]
            - s["true_dedup"]
            - s["false_dedup"]
        )

        total = (
            s["true"]
            + s["false"]
            + display_not_run
            + s["false_dedup"]
            + s["true_dedup"]
        )

        print(
            f"{tool:5s} "
            f"True={s['true']:4d}\t "
            f"False={s['false']:4d}\t "
            f"True_dedup={s['true_dedup']:6d}\t "
            f"False_dedup={s['false_dedup']:6d}\t "
            f"Not_run={display_not_run:4d}\t "
            f"Total={total:4d}\t"
            f"all_edges={s['all_edges']:4d}"
        )

    # #
    # # WRITE ONLY EXTRA INSPECTION OUTPUT (NO IMPACT ON STATS)
    # #
    # output_dir = OUTPUT_PATH / "failure_analysis"
    # output_dir.mkdir(exist_ok=True)

    # with open(output_dir / "detailed_edges.json", "w") as f:
    #     json.dump(detailed, f, indent=2)


if __name__ == '__main__':
    main()