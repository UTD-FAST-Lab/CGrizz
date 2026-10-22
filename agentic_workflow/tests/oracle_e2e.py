"""
End-to-end check of the oracle stages against the real Java agent, with the LLM
stubbed out (the stub "agent" just writes a given spec).

Builds its own fixtures: a reproducing test case (com.example, Class.forName +
newInstance) and a "real program" with the same pattern (org.app). Needs java,
javac, jar and mvn on PATH, plus claude-agent-sdk installed.

    cd agentic_workflow && python tests/oracle_e2e.py
"""
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline.stages.oracle as SO
import pipeline.oracle as O
import pipeline.dedup as D
import pipeline.runner as R
from pipeline.state import PipelineState

TEST_CASE_APP = 'package com.example;\npublic class App {\n    public interface Log { boolean isDebugEnabled(); }\n    public static class SimpleLog implements Log { public boolean isDebugEnabled() { return true; } }\n    public static void main(String[] a) throws Exception {\n        Log l = (Log) Class.forName(System.getProperty("log", "com.example.App$SimpleLog")).newInstance();\n        System.out.println(l.isDebugEnabled());\n    }\n}\n'

PROGRAM_MAIN = 'package org.app;\nimport java.util.*;\npublic class Main {\n    public interface Plugin { String name(); void start(); }\n    public static class Alpha implements Plugin { public String name() { return "alpha"; } public void start() {} }\n    public static class Beta implements Plugin { public String name() { return "beta"; } public void start() {} }\n    static List<Plugin> load(String... names) throws Exception {\n        List<Plugin> out = new ArrayList<>();\n        for (String n : names) out.add((Plugin) Class.forName("org.app.Main$" + n).newInstance());\n        return out;\n    }\n    public static void main(String[] a) throws Exception {\n        for (Plugin p : load("Alpha", "Beta")) { System.out.println(p.name()); p.start(); }\n        System.out.println(new ArrayList<String>().size());\n    }\n}\n'

NEAR_MISS_APP = """package com.example;
public class App {
    public interface Log { boolean isDebugEnabled(); }
    public static class SimpleLog implements Log { public boolean isDebugEnabled() { return true; } }
    public static void main(String[] a) throws Exception {
        Log l = (Log) Class.forName("com.example.App$SimpleLog").newInstance();
        System.out.println(l.isDebugEnabled());
    }
}
"""

COND_SPEC = """{"rootCauses": [{"id": "forName", "at": "call", "owner": "java.lang.Class", "name": "forName", "tag": "return",
                 "when": [{"on": "arg0", "is": "nonConstant"}]}],
 "symptoms": {"match": ["receiverClass"]}}
"""

SPECS = {'good_spec.json': '{"pattern_class": "reflection_indirect_instantiation",\n "rootCauses": [{"id": "forName", "at": "call", "owner": "java.lang.Class", "name": "forName", "tag": "return"}],\n "symptoms": {"match": ["receiverClass"]}}\n', 'testy_spec.json': '{"rootCauses": [{"id": "x", "at": "call", "owner": "com.example.App", "name": "main", "tag": "return"}]}\n', 'bad_spec.json': '{"rootCauses": [{"id": "x", "at": "call", "owner": "java.lang.Class", "name": "forName", "tag": "returns"}], "extra": 1}\n'}


def build_fixtures(root: Path) -> None:
    tc = root / "tc"
    (tc / "src/main/java/com/example").mkdir(parents=True)
    (tc / "src/main/java/com/example/App.java").write_text(TEST_CASE_APP)
    (tc / "jcg.conf").write_text(json.dumps({"name": "reproducing", "target": "/repo/app.jar", "java": 8,
                                            "main": "com.example.App", "jvm_args": [],
                                            "cp": [{"path": "/repo/app.jar"}]}))
    (tc / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId>"
                                "<artifactId>app</artifactId><version>1</version></project>")
    classes = tc / "target/classes"
    classes.mkdir(parents=True)
    subprocess.run(["javac", "-nowarn", "-d", str(classes), str(tc / "src/main/java/com/example/App.java")], check=True)
    subprocess.run(["jar", "cf", str(tc / "target/app-1.jar"), "-C", str(classes), "."], check=True)
    prog = root / "prog"
    (prog / "src/org/app").mkdir(parents=True)
    (prog / "src/org/app/Main.java").write_text(PROGRAM_MAIN)
    (prog / "classes").mkdir()
    (prog / "jars").mkdir()
    subprocess.run(["javac", "-nowarn", "-d", str(prog / "classes"), str(prog / "src/org/app/Main.java")], check=True)
    subprocess.run(["jar", "cf", str(prog / "jars/prog.jar"), "-C", str(prog / "classes"), "."], check=True)
    for name, text in SPECS.items():
        (root / name).write_text(text)
    (root / "cond_spec.json").write_text(COND_SPEC)
    # the near miss: same program, constant class name (the tool resolves it)
    nr = root / "tcnr"
    (nr / "src/main/java/com/example").mkdir(parents=True)
    (nr / "src/main/java/com/example/App.java").write_text(NEAR_MISS_APP)
    (nr / "jcg.conf").write_text((tc / "jcg.conf").read_text())
    (nr / "target/classes").mkdir(parents=True)
    subprocess.run(["javac", "-nowarn", "-d", str(nr / "target/classes"), str(nr / "src/main/java/com/example/App.java")],
                   check=True)
    subprocess.run(["jar", "cf", str(nr / "target/app-1.jar"), "-C", str(nr / "target/classes"), "."], check=True)


T = Path(tempfile.mkdtemp(prefix="oracle_e2e_"))
build_fixtures(T)


def boundary(caller_cls, caller, callee_cls, callee):
    return {"caller": {"declaringClass": f"L{caller_cls.replace('.', '/')};", "name": caller},
            "missedCallee": {"declaringClass": f"L{callee_cls.replace('.', '/')};", "name": callee}}


def make_state(b, idx=0):
    out = Path(tempfile.mkdtemp())
    tc = out / "test_case" / "reproducing"
    shutil.copytree(T / "tc", tc)
    s = PipelineState(program_name="prog", tool_name="Soot", algorithm="SPARK", boundary_index=idx,
                      boundary_id=f"b{idx}", target_program_path=str(T / "prog"), main_class="org.app.Main",
                      sa_tool_path=".", boundary=b, output_dir=out)
    s.reproducing_test_case_path = str(tc)
    s.program_jar_path = str(T / "prog" / "jars")
    s.unsoundness_report = "report.json"
    return s


specs = []  # specs the stub "agent" writes, in order
prompts = []


async def fake_agent(prompt, options, stage_name):
    prompts.append(prompt)
    spec_line = next(l for l in prompt.splitlines() if l.startswith("Spec to write"))
    spec = Path(spec_line.split(":", 1)[1].strip())
    spec.write_text((T / specs.pop(0)).read_text())
    return '{"success": true}'

SO.run_agent = fake_agent
SO.build_agent_options = lambda state: None
results = {}


async def main():
    # 1. good spec → validated on the test case, boundary edge found on the real program
    specs[:] = ["good_spec.json"]
    s = make_state(boundary("org.app.Main", "main", "org.app.Main$Alpha", "name"))
    ok = await SO.stage_generate_oracle(s)
    v = await SO.stage_check_oracle_on_program(s)
    results["good spec: generated"] = ok
    results["good spec: verdict"] = v.kind
    tc_oracle = json.loads(Path(s.oracle_path).read_text())
    results["test-case oracle edges"] = sorted(f"{a}.{b}->{c}.{d}" for a, b, c, d in O.symptom_edges(tc_oracle))

    # 2. a boundary the pattern does not explain → pattern verdict
    s2 = make_state(boundary("org.app.Main", "main", "java.util.ArrayList", "size"), 1)
    s2.oracle_spec_path = s.oracle_spec_path
    v2 = await SO.stage_check_oracle_on_program(s2)
    results["unrelated edge: verdict"] = (v2.kind, v2.category)

    # 3. dedup prunes Beta.start (same root cause), keeps ArrayList.size
    others = [make_state(boundary("org.app.Main", "main", "org.app.Main$Beta", "start"), 2),
              make_state(boundary("org.app.Main", "main", "java.util.ArrayList", "size"), 3)]
    # a different program key → dedup runs the spec on the program instead of reusing oracle_target.json
    queues = {"other/Soot/SPARK": ({"program_name": "prog", "program_jar_path": str(T / "prog" / "jars"),
                                   "main_class": "org.app.Main"}, list(others))}
    await D.run_dedup(s, queues)
    results["dedup: kept"] = [x.boundary_id for x in queues["other/Soot/SPARK"][1]]
    rep = json.loads((s.output_dir / "pruning_report.json").read_text())
    results["dedup: pruned"] = [p["callee_class"] + "." + p["callee_method"] for p in rep["pruned_boundaries"]]
    results["dedup: run status"] = [r["status"] for r in rep["oracle_runs"]]

    # 4. test-case-specific spec is rejected by the lint, then fixed on the second attempt
    specs[:] = ["testy_spec.json", "good_spec.json"]
    prompts.clear()
    s3 = make_state(boundary("org.app.Main", "main", "org.app.Main$Alpha", "name"), 4)
    ok3 = await SO.stage_generate_oracle(s3)
    results["lint then fix: generated"] = ok3
    results["lint then fix: second prompt mentions test case"] = "a class of the test case" in prompts[1]

    # 5. invalid spec twice → instrumentation_failure, reason contains the agent's spec errors
    specs[:] = ["bad_spec.json", "bad_spec.json"]
    prompts.clear()
    s4 = make_state(boundary("org.app.Main", "main", "org.app.Main$Alpha", "name"), 5)
    ok4 = await SO.stage_generate_oracle(s4)
    results["invalid spec: generated"] = ok4
    results["invalid spec: kind"] = s4.oracle_failure_kind
    results["invalid spec: error mentions tag"] = "tag must be" in (s4.oracle_failure or "")

    # 6. timeout → infrastructure verdict; the runner must not send the report back
    slow = T / "prog" / "slow"
    slow.mkdir(exist_ok=True)
    (slow / "Slow.java").write_text("public class Slow { public static void main(String[] a) throws Exception { Thread.sleep(60000); } }")
    import subprocess
    subprocess.run(["javac", "-d", str(slow), str(slow / "Slow.java")], check=True)
    subprocess.run(["jar", "cf", str(slow / "slow.jar"), "-C", str(slow), "Slow.class"], check=True)
    run = await O.run_oracle(Path(s.oracle_spec_path), str(slow / "slow.jar"), "Slow", slow / "o.json", timeout=3)
    results["timeout: status"] = run.status
    results["timeout: verdict"] = O.classify(run, s.boundary).category

    # 7. negative check: an unconditional forName spec also fires on the near miss → rejected as too broad;
    #    the same spec with {"on": "arg0", "is": "nonConstant"} passes
    specs[:] = ["good_spec.json", "cond_spec.json"]
    prompts.clear()
    s6 = make_state(boundary("org.app.Main", "main", "org.app.Main$Alpha", "name"), 7)
    shutil.copytree(T / "tcnr", s6.output_dir / "test_case" / "non_reproducing")
    s6.non_reproducing_test_case_path = str(s6.output_dir / "test_case" / "non_reproducing")
    ok6 = await SO.stage_generate_oracle(s6)
    results["negative check: generated"] = ok6
    results["negative check: first spec rejected as too broad"] = (
        len(prompts) == 2 and "NON-reproducing" in prompts[1] and "com.example.App.main" in prompts[1])
    v6 = await SO.stage_check_oracle_on_program(s6)
    results["negative check: conditional spec still finds the real edge"] = v6.kind

    async def ok_(state, *a, **k):
        return True
    for n in ["stage_generate_report", "stage_generate_test_case", "stage_generate_oracle"]:
        setattr(R, n, ok_)

    async def infra(state):
        return O.Verdict("timeout", O.INFRA, "timed out")
    R.stage_check_oracle_on_program = infra
    calls = []

    async def repro(state):
        calls.append("repro")
        return True, ""
    R.stage_check_reproduction = repro
    s5 = make_state(boundary("a.B", "c", "d.E", "f"), 6)
    await R.run_pipeline(s5)
    res = json.loads((s5.output_dir / "pipeline_result.json").read_text())
    results["runner on infra failure"] = (res["success"], res["failure_kind"], res["loops_used"], calls)


asyncio.run(main())

EXPECTED = {
    "good spec: generated": True,
    "good spec: verdict": "found",
    "test-case oracle edges": ["com.example.App.main->com.example.App$SimpleLog.isDebugEnabled"],
    "unrelated edge: verdict": ("edge_missing", "pattern"),
    "dedup: kept": ["b3"],
    "dedup: pruned": ["org/app/Main$Beta.start"],
    "dedup: run status": ["ok"],
    "lint then fix: generated": True,
    "lint then fix: second prompt mentions test case": True,
    "invalid spec: generated": False,
    "invalid spec: kind": "instrumentation_failure",
    "invalid spec: error mentions tag": True,
    "timeout: status": "timeout",
    "timeout: verdict": "infrastructure",
    "runner on infra failure": (False, "instrumentation_failure", 0, []),
    "negative check: generated": True,
    "negative check: first spec rejected as too broad": True,
    "negative check: conditional spec still finds the real edge": "found",
}
failed = 0
for k, want in EXPECTED.items():
    got = results.get(k)
    ok = got == want
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {k}: {got}" + ("" if ok else f" (expected {want})"))
shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
