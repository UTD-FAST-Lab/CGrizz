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
import subprocess
from pathlib import Path
import sys

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

    analysis_interface_path = Path(__file__).parent.parent / "jcg" / "analysis_interface.py"

    subprocess.run(
        [
            sys.executable,
            str(analysis_interface_path),
            '--framework', state.tool_name,
            '--algorithm', state.algorithm,
            '--project', 'reproducing',
            '--type', 'boundary',
            '--reproducing_test_case_path', str(Path(state.reproducing_test_case_path).resolve()),
            '--boundary_id', str(state.boundary_id),
        ]
    )
    

    log.info(f"{state.label()} Static CG  → {staticcg_path}")
    log.info(f"{state.label()} Dynamic CG → {dyncg_path}")
    log.info(f"{state.label()} Boundary   → {boundary_path}")
    return str(staticcg_path), str(dyncg_path), str(boundary_path)


def run_annotated_sa(state: PipelineState, project: str = "reproducing", test_case_path: str | None = None) -> str:
    """
    Run the annotated SA tool on a test case to produce the modified
    analysis output (SCG') and execution trace log
    (<test_case>/staticcg/log.txt).

    Used in stage 8 (trace check): on the reproducing test case to confirm the
    root cause, and on the non-reproducing one to check the warning is selective.

    Returns:
        modified_analysis_path — path to the annotated SA tool output
    """
    out_path = state.out(OUT_MODIFIED_ANALYSIS)
    test_case_path = test_case_path or state.reproducing_test_case_path

    log.info(f"{state.label()} Running annotated SA tool ({project})...")
    log.info(f"{state.label()} Annotated tool : {state.annotated_sa_tool_path}")
    log.info(f"{state.label()} Test case      : {test_case_path}")

    annotation_interface_path = Path(__file__).parent.parent / "jcg" / "annotation_interface.py"

    subprocess.run(
        [
            sys.executable,
            str(annotation_interface_path),
            '--framework', state.tool_name,
            '--algorithm', state.algorithm,
            '--project', project,
            '--reproducing_test_case_path', str(Path(test_case_path).resolve()),
            '--annotated_sa_tool_path', str(Path(state.annotated_sa_tool_path).resolve()),
            '--boundary_id', str(state.boundary_id),
        ]
    )
    

    log.info(f"{state.label()} Annotated output → {out_path}")
    return out_path
