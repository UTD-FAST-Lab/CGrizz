#!/usr/bin/env bash
# End-to-end test of a tool's unsoundness-marker module on the real, pinned tool:
#   1. install the module + each case's example annotation into a copy of tools/<tool>
#   2. compile only the changed files on top of the pinned tool's jars
#   3. run the tool's call-graph algorithm (as JCG runs it) on test/cases/<case> with
#      and without them
#   4. the call graphs must be identical, and [UNSOUNDNESS-EDGE] warnings must appear
#      exactly at the expected lines
#
#   agentic_workflow/tool_markers/run_test.sh <soot|wala|opal> [case ...]
#
# Needs JDK 8 (rt.jar from $JAVA_HOME/jre/lib) and tools/<tool>
# (python scripts/setup/download_tools.py <tool>). The pinned tool is built by
# <tool>/test/build.sh unless <TOOL>_CP points to its prebuilt classpath.
set -euo pipefail
TOOLNAME="${1:?usage: run_test.sh <soot|wala|opal> [case ...]}"; shift
HERE="$(cd "$(dirname "$0")" && pwd)"
T="$HERE/$TOOLNAME/test"
AW="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$AW/.." && pwd)"
TOOL_SRC="$REPO/tools/$TOOLNAME"
RT="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}/jre/lib/rt.jar"
[ -d "$TOOL_SRC" ] || { echo "tools/$TOOLNAME missing: python scripts/setup/download_tools.py $TOOLNAME"; exit 1; }
[ -f "$RT" ] || { echo "rt.jar not found at $RT (JDK 8 needed)"; exit 1; }
WORK="${KEEP_WORK:-$(mktemp -d)}"; [ -n "${KEEP_WORK:-}" ] || trap 'rm -rf "$WORK"' EXIT

# shellcheck source=/dev/null
source "$T/build.sh"            # sets TOOL_CP, DRIVER, compile_overlay() [, compile_driver()]
mkdir -p "$WORK/driver"
if declare -F compile_driver >/dev/null; then compile_driver "$WORK/driver"
else javac -nowarn -cp "$TOOL_CP" -d "$WORK/driver" "$T/$DRIVER.java"; fi

fail=0
for dir in ${@:-$T/cases/*/}; do
  name="$(basename "$dir")"; case_dir="$T/cases/$name"; w="$WORK/$name"
  mkdir -p "$w/classes" "$w/overlay"
  javac -nowarn -XDignore.symbol.file -d "$w/classes" "$case_dir"/*.java 2>/dev/null
  mkdir -p "$w/tool" && cp -r "$TOOL_SRC"/. "$w/tool/" && rm -rf "$w/tool/.git"
  changed=$(cd "$AW" && python3 - "$TOOLNAME" "$w/tool" "$case_dir/annotation.json" <<'PY'
import json, sys
sys.path.insert(0, ".")
from pipeline.tool_markers import apply_edits, install
tool_name, tool, ann = sys.argv[1], sys.argv[2], json.load(open(sys.argv[3]))
files = set(install(tool_name, tool)) | set(apply_edits(ann["edits"], tool, "example annotation"))
print(" ".join(sorted(files)))
PY
)
  compile_overlay "$w/tool" "$w/overlay" $changed
  java -Xmx3g -cp "$WORK/driver:$TOOL_CP" "$DRIVER" "$w/classes" Main "$RT" "$w/original.txt" >"$w/original.log" 2>&1
  java -Xmx3g -cp "$WORK/driver:$w/overlay:$TOOL_CP" "$DRIVER" "$w/classes" Main "$RT" "$w/marked.txt" >"$w/marked.log" 2>&1
  if python3 - "$w" "$case_dir/annotation.json" <<'PY'
import json, re, sys
w, expect = sys.argv[1], json.load(open(sys.argv[2]))["expect"]
log = open(f"{w}/marked.log").read()
# only the test program's own calls (the tool may also warn inside the JDK, which is correct)
lines = sorted({int(m) for m in re.findall(r"\[UNSOUNDNESS-EDGE\][^\n]*\| caller=Main[.$][^\n]*\| line=(\d+)", log)})
jdk = len(re.findall(r"\[UNSOUNDNESS-EDGE\][^\n]*\| caller=(?!Main[.$])", log))
original, marked = open(f"{w}/original.txt").read(), open(f"{w}/marked.txt").read()
problems = []
if not original.strip(): problems.append("empty call graph")
if original != marked: problems.append("call graph changed")
if lines != sorted(expect["edge_warning_lines"]): problems.append(f"edge warnings at lines {lines}, expected {expect['edge_warning_lines']}")
if set(lines) & set(expect.get("no_warning_lines", [])): problems.append("warning where the tool resolves the call")
print("  " + ("; ".join(problems) if problems else f"identical call graphs ({len(original.splitlines())} edges), "
      f"edge warnings in Main at lines {lines} (+{jdk} inside the JDK)"))
sys.exit(1 if problems else 0)
PY
  then echo "[$TOOLNAME/$name] ok"; else echo "[$TOOLNAME/$name] FAIL"; grep "UNSOUNDNESS.*caller=Main" "$w/marked.log" || tail -20 "$w/marked.log"; fail=1; fi
done
exit $fail
