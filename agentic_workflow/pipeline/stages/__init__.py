"""
pipeline/stages
===============
One module per stage family. Each stage_*() function takes the PipelineState,
calls run_agent() and/or external tools, mutates the state, and returns bool
or (bool, feedback).
"""

from pipeline.stages.report import stage_generate_report, stage_refine_report
from pipeline.stages.test_case import stage_generate_test_case, stage_shrink_test_case
from pipeline.stages.oracle import stage_generate_oracle, stage_check_oracle_on_program
from pipeline.stages.reproduction import recheck_reproduction, stage_check_reproduction
from pipeline.stages.annotation import stage_root_cause_annotation, stage_check_trace

__all__ = [
    "stage_generate_report",
    "stage_refine_report",
    "stage_generate_test_case",
    "stage_shrink_test_case",
    "stage_generate_oracle",
    "stage_check_oracle_on_program",
    "stage_check_reproduction",
    "recheck_reproduction",
    "stage_root_cause_annotation",
    "stage_check_trace",
]
