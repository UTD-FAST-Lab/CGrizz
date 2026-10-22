"""
Tests for jcg/analysis_interface.py and jcg/annotation_interface.py with fake
`docker` and `mvn` binaries (no Docker needed): image built once, static and
dynamic call graphs generated concurrently, fast-path environment passed to the
containers, and changed-source detection for the annotation fast path.

    cd agentic_workflow && python tests/jcg_interfaces_test.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JCG = ROOT / "jcg"

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Records each call; `docker run` fakes JCG's outputs after a delay."""
import json, os, sys, time
args = sys.argv[1:]
rec = {"cmd": args[0], "start": time.time(), "env": {}, "mounts": {}}
if args[0] == "run":
    i = 1
    while i < len(args):
        if args[i] == "-e":
            k, v = args[i + 1].split("=", 1); rec["env"][k] = v; i += 2
        elif args[i] == "--mount":
            parts = dict(p.split("=", 1) for p in args[i + 1].split(",") if "=" in p)
            rec["mounts"][parts["target"]] = parts["source"]; i += 2
        elif args[i] == "--name":
            rec["name"] = args[i + 1]; i += 2
        else:
            break
    rest = args[i + 1:]
    opt = {rest[j]: rest[j + 1] for j in range(len(rest) - 1) if rest[j].startswith("--")}
    time.sleep(1.0)
    if "/staticcg" in rec["mounts"] and "--adapter" in opt:
        out = os.path.join(rec["mounts"]["/staticcg"], opt["--project-prefix"], opt["--adapter"], opt["--algorithm-prefix"])
        os.makedirs(out, exist_ok=True); open(os.path.join(out, "cg.json"), "w").write("{}")
    if "/dyncg" in rec["mounts"]:
        out = os.path.join(rec["mounts"]["/dyncg"], opt["--project-prefix"], "Dynamic", "Dynamic")
        os.makedirs(out, exist_ok=True); open(os.path.join(out, "cg.json"), "w").write("{}")
    if "/output" in rec["mounts"]:
        open(os.path.join(rec["mounts"]["/output"], "boundaries.json"), "w").write('{"boundaries": []}')
rec["end"] = time.time()
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
'''


def setup(tmp: Path) -> dict:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "mvn").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    tc = tmp / "reproducing"
    tc.mkdir()
    (tc / "jcg.conf").write_text(json.dumps({"main": "com.example.App"}))
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", FAKE_DOCKER_LOG=str(tmp / "docker.log"))
    return {"tc": tc, "env": env, "log": tmp / "docker.log"}


def calls(log: Path) -> list[dict]:
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def run_analysis(ctx: dict, *extra: str) -> list[dict]:
    ctx["log"].unlink(missing_ok=True)
    subprocess.run([sys.executable, str(JCG / "analysis_interface.py"), "--framework", "Soot", "--algorithm", "SPARK",
                    "--project", "reproducing", "--type", "boundary", "--reproducing_test_case_path", str(ctx["tc"]),
                    "--boundary_id", "b3_x", *extra], env=ctx["env"], check=True, capture_output=True)
    return calls(ctx["log"])


def test_analysis_interface():
    ctx = setup(Path(tempfile.mkdtemp()))
    c = run_analysis(ctx, "--skip-image-build")
    runs = [x for x in c if x["cmd"] == "run"]
    assert not [x for x in c if x["cmd"] == "build"], "no image build with --skip-image-build"
    assert len(runs) == 3, [x.get("name") for x in runs]
    by_name = {x["name"].split("_container")[0]: x for x in runs}
    static, dyn, boundary = by_name["staticcg"], by_name["dyncg"], by_name["boundary"]
    assert static["start"] < dyn["end"] and dyn["start"] < static["end"], "static and dynamic CG must overlap"
    assert boundary["start"] >= max(static["end"], dyn["end"]), "boundary comparison runs after both"
    assert all(not x["env"] for x in runs), "no fast-path env by default"
    assert (ctx["tc"] / "boundary/Soot/SPARK/boundaries.json").exists()

    for d in ("staticcg", "dyncg", "boundary"):
        shutil.rmtree(ctx["tc"] / d, ignore_errors=True)
    c = run_analysis(ctx, "--skip-image-build", "--fast-path")
    assert all(x["env"].get("JCG_FAST_PATH") == "1" for x in c if x["cmd"] == "run")

    for d in ("staticcg", "dyncg", "boundary"):
        shutil.rmtree(ctx["tc"] / d, ignore_errors=True)
    c = run_analysis(ctx)
    assert len([x for x in c if x["cmd"] == "build"]) == 1, "standalone use still builds the image (once)"


def load(script: str):
    spec = importlib.util.spec_from_file_location(script[:-3], JCG / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changed_sources():
    ann = load("annotation_interface.py")
    tmp = Path(tempfile.mkdtemp())
    orig = tmp / "tools/wala"
    for rel, text in {"core/src/A.java": "class A {}", "core/src/B.java": "class B {}", "util/C.scala": "object C",
                      "README.md": "x"}.items():
        (orig / rel).parent.mkdir(parents=True, exist_ok=True)
        (orig / rel).write_text(text)
    annotated = tmp / "annotated"
    shutil.copytree(orig, annotated)                                   # keeps mtimes, like the pipeline
    (annotated / "core/src/A.java").write_text("class A { void f() { System.out.println(1); } }")
    time.sleep(0.01)
    (annotated / "core/src/B.java").write_text("class B {}")           # rewritten, same content
    (annotated / "core/target").mkdir()
    (annotated / "core/target/Gen.java").write_text("generated")      # build output is ignored
    (annotated / "util/D.java").write_text("class D {}")               # new file
    assert ann.changed_sources(annotated, orig) == ["core/src/A.java", "util/D.java"]

    ann.config["WALA_ORG_PATH"] = orig
    env = ann.container_env("WALA", str(annotated), fast_path=True)
    assert env == {"SA_FRAMEWORK": "WALA", "JCG_FAST_PATH": "1", "CHANGED_SOURCES": "core/src/A.java:util/D.java"}
    assert ann.container_env("WALA", str(annotated), fast_path=False) == {"SA_FRAMEWORK": "WALA"}


def test_pipeline_passes_flags():
    sys.path.insert(0, str(ROOT))
    import pipeline.analysis as A
    A.JCG_FAST_PATH = False
    assert A._fast_path_args() == ["--skip-image-build"]
    A.JCG_FAST_PATH = True
    assert A._fast_path_args() == ["--skip-image-build", "--fast-path"]


if __name__ == "__main__":
    test_analysis_interface()
    test_changed_sources()
    test_pipeline_passes_flags()
    print("jcg interfaces: all tests passed")
