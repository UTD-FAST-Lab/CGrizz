#!/usr/bin/env bash
# End-to-end test of the Soot marker module on the real, pinned Soot:
#   1. install the module + the example annotation into a copy of tools/soot
#   2. compile the changed files on top of the pinned Soot jar
#   3. run SPARK (JCG's Soot options) on test/cases/<name> with and without them
#   4. the call graphs must be identical, and [UNSOUNDNESS-EDGE] warnings must appear
#      exactly at the expected lines
#
# Needs: JDK 8 (rt.jar from $JAVA_HOME/jre/lib), tools/soot (scripts/setup/download_tools.py soot).
# The pinned Soot jar is built with Maven unless SOOT_JAR points to one.
#   agentic_workflow/tool_markers/soot/test/run_test.sh [program ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AW="$(cd "$HERE/../../.." && pwd)"          # agentic_workflow/
REPO="$(cd "$AW/.." && pwd)"
TOOL="$REPO/tools/soot"
RT="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}/jre/lib/rt.jar"
[ -f "$TOOL/pom.xml" ] || { echo "tools/soot missing: python scripts/setup/download_tools.py soot"; exit 1; }
[ -f "$RT" ] || { echo "rt.jar not found at $RT (JDK 8 needed)"; exit 1; }
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

if [ -z "${SOOT_JAR:-}" ]; then
  echo "building the pinned Soot jar (set SOOT_JAR to reuse one) ..."
  cp -r "$TOOL" "$WORK/soot-build"; rm -rf "$WORK/soot-build/.git"
  (cd "$WORK/soot-build" && mvn -B -q -DskipTests -Dmaven.javadoc.skip=true compile assembly:single)
  SOOT_JAR="$WORK/soot-build/target/sootclasses-trunk-jar-with-dependencies.jar"
fi
mkdir -p "$WORK/driver" && javac -nowarn -cp "$SOOT_JAR" -d "$WORK/driver" "$HERE/SparkDriver.java"

fail=0
for dir in ${@:-$HERE/cases/*/}; do
  name="$(basename "$dir")"; prog="$HERE/cases/$name"; w="$WORK/$name"
  mkdir -p "$w/classes" "$w/overlay"
  javac -nowarn -XDignore.symbol.file -d "$w/classes" "$prog"/*.java 2>/dev/null
  mkdir -p "$w/tool" && cp -r "$TOOL/src" "$w/tool/"
  changed=$(cd "$AW" && python3 - "$w/tool" "$prog/annotation.json" <<'PY'
import json, sys
sys.path.insert(0, ".")
from pipeline.tool_markers import apply_edits, install
tool, ann = sys.argv[1], json.load(open(sys.argv[2]))
files = set(install("Soot", tool)) | set(apply_edits(ann["edits"], tool, "example annotation"))
print(" ".join(sorted(f for f in files if f.endswith(".java"))))
PY
)
  (cd "$w/tool" && javac -nowarn -encoding utf8 -cp "$SOOT_JAR" -d "$w/overlay" $changed 2>&1 | grep -v "^Note:" || true)
  java -Xmx3g -cp "$WORK/driver:$SOOT_JAR" SparkDriver "$w/classes" Main "$RT" "$w/original.txt" >/dev/null 2>&1
  java -Xmx3g -cp "$WORK/driver:$w/overlay:$SOOT_JAR" SparkDriver "$w/classes" Main "$RT" "$w/marked.txt" >"$w/marked.log" 2>&1
  if python3 - "$w" "$prog/annotation.json" <<'PY'
import json, re, sys
w, expect = sys.argv[1], json.load(open(sys.argv[2]))["expect"]
log = open(f"{w}/marked.log").read()
lines = sorted({int(m) for m in re.findall(r"\[UNSOUNDNESS-EDGE\][^\n]*\| line=(\d+)", log)})
same = open(f"{w}/original.txt").read() == open(f"{w}/marked.txt").read()
problems = []
if not same: problems.append("call graph changed")
if lines != sorted(expect["edge_warning_lines"]): problems.append(f"edge warnings at lines {lines}, expected {expect['edge_warning_lines']}")
if set(lines) & set(expect.get("no_warning_lines", [])): problems.append("warning where SPARK resolves the call")
print("  " + ("; ".join(problems) if problems else f"identical call graphs ({len(open(w + '/original.txt').readlines())} edges), edge warnings at lines {lines}"))
sys.exit(1 if problems else 0)
PY
  then echo "[$name] ok"; else echo "[$name] FAIL"; grep UNSOUNDNESS "$w/marked.log" || true; fail=1; fi
done
exit $fail
