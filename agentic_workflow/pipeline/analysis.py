"""
pipeline/analysis.py
====================
Wrappers for the external tool invocations.

Two responsibilities:
  1. run_analysis_interface()  — run the CG analysis interface on the reproducing
                                 test case; produces staticcg/, dyncg/, and boundary/
                                 output directories.
  2. run_annotated_sa()        — run the annotated SA tool on the test case to
                                 produce the modified analysis output (SCG') and
                                 execution trace log.

"""

import logging
from pathlib import Path

from pipeline.state import PipelineState
from config import (
    OUT_STATICCG_DIR,
    OUT_DYNCG_DIR,
    OUT_BOUNDARY_DIR,
    OUT_MODIFIED_ANALYSIS,
)

log = logging.getLogger("pipeline")


def run_analysis_interface(state: PipelineState) -> tuple[str, str, str]:
    """
    Run the CG analysis interface on the reproducing test case.

    The interface generates three output directories:
      staticcg/   — static call graph produced by the SA tool
      dyncg/      — dynamic call graph collected at runtime
      boundary/   — edges present in dyncg but absent in staticcg (missed edges)

    Used in stage 5 (reproduction check): the boundary/ output is inspected to
    confirm that the expected caller→callee edge from the unsoundness report
    appears as a missed edge on the test case.

    Returns:
        (staticcg_path, dyncg_path, boundary_path)
    """

    test_case_path = state.reproducing_test_case_path
    staticcg_path = Path(test_case_path) / OUT_STATICCG_DIR / "reproducing" / state.tool_name / state.algorithm / "cg.json"
    dyncg_path = Path(test_case_path) / OUT_DYNCG_DIR / "reproducing" / "Dynamic/Dynamic" / "cg.json"
    boundary_path = Path(test_case_path) / OUT_BOUNDARY_DIR / state.tool_name / state.algorithm / "boundaries.json"

    log.info(f"{state.label()} Running analysis interface on test case...")
    log.info(f"{state.label()} Test case : {state.reproducing_test_case_path}")
    log.info(f"{state.label()} SA tool   : {state.sa_tool_path}")
    log.info(f"{state.label()} Algorithm : {state.algorithm}")

    
    import subprocess
    subprocess.run(
        [
            "../jcg/analysis_interface.py",
            '--framework', state.tool_name,
            '--algorithm', state.algorithm,
            '--project', 'reproducing',
            '--type', 'boundary',
            '--reproducing_test_case_path', state.reproducing_test_case_path,
        ],
    )
    

    log.info(f"{state.label()} Static CG  → {staticcg_path}")
    log.info(f"{state.label()} Dynamic CG → {dyncg_path}")
    log.info(f"{state.label()} Boundary   → {boundary_path}")
    return str(staticcg_path), str(dyncg_path), str(boundary_path)


def run_annotated_sa(state: PipelineState) -> str:
    """
    Run the annotated SA tool on the test case to produce the modified
    analysis output (SCG') and execution trace log.

    Used in stage 6 (trace check) to confirm the root cause via
    the execution trace written by the annotations.

    Returns:
        modified_analysis_path — path to the annotated SA tool output
    """
    out_path = state.out(OUT_MODIFIED_ANALYSIS)

    log.info(f"{state.label()} Running annotated SA tool...")
    log.info(f"{state.label()} Annotated tool : {state.annotated_sa_tool_path}")
    log.info(f"{state.label()} Test case      : {state.reproducing_test_case_path}")

    
    import subprocess
    subprocess.run(
        [
            '../jcg/annotation_interface.py',
            '--framework', state.tool_name,
            '--algorithm', state.algorithm,
            '--project', 'reproducing',
            '--reproducing_test_case_path', state.reproducing_test_case_path,
            '--annotated_sa_tool_path', state.annotated_sa_tool_path,
        ]
    )
    

    log.info(f"{state.label()} Annotated output → {out_path}")
    return out_path
