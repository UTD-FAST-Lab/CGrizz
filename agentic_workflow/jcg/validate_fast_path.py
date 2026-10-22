"""
Check that JCG_FAST_PATH gives the same results as the default (sbt + full
tool rebuild) path before enabling it in config.py.

Takes an existing boundary output directory (with test_case/reproducing and
annotated_sa_tool/), runs each interface twice on fresh copies of the test
case — once normally, once with --fast-path — and compares:

  boundary    static cg.json, dynamic cg.json and boundaries.json
  annotation  static cg.json and the [UNSOUNDNESS] lines of the trace

Usage (from agentic_workflow/):
  python jcg/validate_fast_path.py --framework WALA --algorithm 0-CFA \\
      --boundary-dir ../results/<program>/WALA/0-CFA/<boundary>
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, args: list[str]) -> float:
    start = time.monotonic()
    subprocess.run([sys.executable, str(HERE / script), *args], check=False)
    return time.monotonic() - start


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as e:
        return f"<unreadable: {e}>"


def warnings(path: Path) -> list[str]:
    try:
        return sorted({l.strip() for l in path.read_text().splitlines() if "[UNSOUNDNESS]" in l})
    except Exception as e:
        return [f"<unreadable: {e}>"]


def compare(name: str, a, b) -> bool:
    same = a == b
    print(f"  {'same     ' if same else 'DIFFERENT'} {name}")
    if not same and isinstance(a, list) and isinstance(b, list):
        print(f"    only default: {sorted(set(map(str, a)) - set(map(str, b)))[:5]}")
        print(f"    only fast   : {sorted(set(map(str, b)) - set(map(str, a)))[:5]}")
    return same


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--framework", required=True, help="WALA, Soot or OPAL")
    p.add_argument("--algorithm", required=True)
    p.add_argument("--boundary-dir", required=True, type=Path)
    a = p.parse_args()

    tc = a.boundary_dir / "test_case" / "reproducing"
    tool = a.boundary_dir / "annotated_sa_tool"
    work = Path(tempfile.mkdtemp(prefix="fastpath_"))
    ok = True

    copies = {}
    for mode in ("default", "fast"):
        d = work / mode / "reproducing"
        shutil.copytree(tc, d, ignore=shutil.ignore_patterns("staticcg", "dyncg", "boundary"))
        copies[mode] = d

    print("Boundary check (analysis_interface.py)")
    times = {}
    for mode, d in copies.items():
        times[mode] = run("analysis_interface.py", [
            "--framework", a.framework, "--algorithm", a.algorithm, "--project", "reproducing", "--type", "boundary",
            "--reproducing_test_case_path", str(d), "--boundary_id", f"validate{mode}",
            *(["--fast-path"] if mode == "fast" else [])])
    for rel in (f"staticcg/reproducing/{a.framework}/{a.algorithm}/cg.json",
                "dyncg/reproducing/Dynamic/Dynamic/cg.json",
                f"boundary/{a.framework}/{a.algorithm}/boundaries.json"):
        ok &= compare(rel, load(copies["default"] / rel), load(copies["fast"] / rel))
    print(f"  time: default {times['default']:.0f}s, fast {times['fast']:.0f}s")

    if tool.exists():
        print("Trace check (annotation_interface.py)")
        for mode, d in copies.items():
            shutil.rmtree(d / "staticcg", ignore_errors=True)
            times[mode] = run("annotation_interface.py", [
                "--framework", a.framework, "--algorithm", a.algorithm, "--project", "reproducing",
                "--reproducing_test_case_path", str(d), "--annotated_sa_tool_path", str(tool.resolve()),
                "--boundary_id", f"validate{mode}", *(["--fast-path"] if mode == "fast" else [])])
        ok &= compare("[UNSOUNDNESS] lines", warnings(copies["default"] / "staticcg/log.txt"),
                      warnings(copies["fast"] / "staticcg/log.txt"))
        rel = f"staticcg/reproducing/{a.framework}/{a.algorithm}/cg.json"
        ok &= compare(rel, load(copies["default"] / rel), load(copies["fast"] / rel))
        print(f"  time: default {times['default']:.0f}s, fast {times['fast']:.0f}s")

    print(f"\n{'OK — fast path matches' if ok else 'MISMATCH — keep JCG_FAST_PATH = False'} (outputs in {work})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
