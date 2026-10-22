"""
Tests for test-case size (issue #3): measurement, the budget round after
generation, and the shrink stage (kept only if every check still passes).

    cd agentic_workflow && python tests/test_case_size_test.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline.stages.test_case as TC
import pipeline.stages.oracle as SO
import pipeline.stages.reproduction as RP
from pipeline.state import PipelineState
from pipeline.test_case_size import code_lines, measure


def java(lines: int, extra_comment: bool = True) -> str:
    body = "\n".join(f"        int v{i} = {i};" for i in range(lines - 5))
    comment = "/* a block\n   comment */\n// a line comment\n\n" if extra_comment else ""
    return f"{comment}package com.example;\npublic class App {{\n    public static void main(String[] a) {{\n{body}\n    }}\n}}\n"


def test_case(root: Path, name: str, lines: int) -> Path:
    d = root / name
    (d / "src/main/java/com/example").mkdir(parents=True)
    (d / "src/main/java/com/example/App.java").write_text(java(lines))
    (d / "staticcg").mkdir()
    (d / "staticcg/log.txt").write_text("old analysis")
    return d


def state(lines: int, near_miss: bool = True) -> PipelineState:
    out = Path(tempfile.mkdtemp())
    s = PipelineState(program_name="p", tool_name="Soot", algorithm="SPARK", boundary_index=0, boundary_id="b0",
                      target_program_path=".", main_class="M", sa_tool_path=".", boundary={}, output_dir=out)
    s.reproducing_test_case_path = str(test_case(out / "test_case", "reproducing", lines))
    s.non_reproducing_test_case_path = str(test_case(out / "test_case", "non_reproducing", lines)) if near_miss else None
    s.unsoundness_report = "report.json"
    return s


prompts = []


def agent_writing(lines: int):
    async def agent(prompt, options, stage_name):
        prompts.append(prompt)
        for d in ("reproducing", "non_reproducing"):
            f = Path(prompt.split("Reproducing        : ")[1].split("/\n")[0]).parent / d / "src/main/java/com/example/App.java"
            if f.exists():
                f.write_text(java(lines))
        return '{"reproducing": true}'
    return agent


def test_measure():
    assert code_lines(java(10)) == 10
    s = state(10)
    assert measure(s.reproducing_test_case_path).as_dict() == {"files": 1, "lines": 10}
    assert measure(s.output_dir / "nowhere").as_dict() == {"files": 0, "lines": 0}


def test_budget():
    TC.build_agent_options = lambda st: None
    prompts.clear()
    TC.run_agent = agent_writing(40)
    s = state(400)
    asyncio.run(TC._enforce_budget(s))
    assert len(prompts) == 1 and "over the size budget" in prompts[0] and "400 lines of code in 1 Java files" in prompts[0]
    assert "Do not copy code from the target program" in prompts[0]
    assert s.test_case_size == {"files": 1, "lines": 40, "budget_simplified_from": 400}
    prompts.clear()
    s = state(40)
    asyncio.run(TC._enforce_budget(s))
    assert prompts == [] and s.test_case_size == {"files": 1, "lines": 40}


def run_shrink(s, agent_lines, oracle_problem="", reproduced=True):
    calls = []

    async def revalidate(st):
        calls.append("oracle")
        # the outputs were cleared, so the checks see freshly built test cases
        assert not (Path(st.reproducing_test_case_path) / "staticcg").exists()
        return oracle_problem

    async def reproduction(st):
        calls.append("reproduction")
        return reproduced, "" if reproduced else "[reproduction check] not missed"
    SO.revalidate_oracle, RP.stage_check_reproduction = revalidate, reproduction
    TC.run_agent = agent_writing(agent_lines)
    asyncio.run(TC.stage_shrink_test_case(s))
    return calls


def test_shrink_kept():
    s = state(120)
    calls = run_shrink(s, 30)
    assert calls == ["oracle", "reproduction"]
    assert s.test_case_size["lines"] == 30 and s.test_case_size["shrunk_from"] == 120
    assert measure(s.non_reproducing_test_case_path).lines == 30, "the near miss is shrunk with it"
    assert not list(Path(s.reproducing_test_case_path).parent.glob(".*before_shrink")), "backups removed"


def test_shrink_rejected_restores():
    for problem, reproduced, expected in (("[oracle] no symptoms", True, ["oracle"]),
                                          ("", False, ["oracle", "reproduction"])):
        s = state(120)
        calls = run_shrink(s, 30, oracle_problem=problem, reproduced=reproduced)
        assert calls == expected
        for d in (s.reproducing_test_case_path, s.non_reproducing_test_case_path):
            assert measure(d).lines == 120, "restored"
            assert (Path(d) / "staticcg/log.txt").read_text() == "old analysis", "outputs restored too"
        assert "shrunk_from" not in s.test_case_size


def test_shrink_skipped():
    s = state(40)                       # under TEST_CASE_SHRINK_ABOVE_LINES
    prompts.clear()
    assert run_shrink(s, 10) == [] and prompts == []
    s = state(120)
    assert run_shrink(s, 200) == [], "not smaller → no checks"
    assert measure(s.reproducing_test_case_path).lines == 120


if __name__ == "__main__":
    test_measure()
    test_budget()
    test_shrink_kept()
    test_shrink_rejected_restores()
    test_shrink_skipped()
    print("test case size: all tests passed")
