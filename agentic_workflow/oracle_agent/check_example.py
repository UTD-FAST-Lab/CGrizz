"""Check an oracle.json produced by an example against its expect.json."""
import json
import sys


def main(oracle_path: str, expect_path: str) -> int:
    oracle = json.load(open(oracle_path))
    expect = json.load(open(expect_path))
    edges = [(s["caller"], s["callee"]) for rc in oracle.values() for s in rc["symptoms"]]
    rc_methods = [rc["method"] for rc in oracle.values()]

    def has(caller: str, callee: str) -> bool:
        return any(c.startswith(caller + "(") and callee in e for c, e in edges)

    problems = []
    for caller, callee in expect.get("present", []):
        if not has(caller, callee):
            problems.append(f"missing symptom {caller} -> {callee}")
    for caller, callee in expect.get("absent", []):
        if has(caller, callee):
            problems.append(f"unexpected symptom {caller} -> {callee}")
    for m in expect.get("rootCauseMethods", []):
        if not any(r.startswith(m + "(") for r in rc_methods):
            problems.append(f"missing root cause in {m}")
    if expect.get("stats"):
        stats_path = oracle_path[:-5] + ".stats.json"
        stats = json.load(open(stats_path))
        for key, want in expect["stats"].items():
            got = stats.get(key, {})
            for k, v in want.items():
                if got.get(k) != v:
                    problems.append(f"stats {key}[{k}] = {got.get(k)}, expected {v}")
    for p in problems:
        print("  FAIL:", p)
    if problems:
        print("  symptoms recorded:", *[f"{c} -> {e}" for c, e in edges], sep="\n    ")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
