# Sourced by tool_markers/run_test.sh. Builds the pinned WALA (util, shrike, core) unless WALA_CP is set.
DRIVER=WalaDriver
if [ -z "${WALA_CP:-}" ]; then
  echo "building the pinned WALA jars (set WALA_CP to reuse them) ..."
  cp -r "$TOOL_SRC" "$WORK/wala-build"; rm -rf "$WORK/wala-build/.git"
  # Only the three modules the analysis needs (the Eclipse/ECJ subprojects use a Gradle plugin
  # that fails on some platforms, e.g. aarch64, and are not needed here).
  python3 - "$WORK/wala-build/settings.gradle" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
start = s.index("include("); end = s.index(")", start) + 1
open(p, "w").write(s[:start] + "include('com.ibm.wala.core', 'com.ibm.wala.shrike', 'com.ibm.wala.util')" + s[end:])
PY
  (cd "$WORK/wala-build" && ./gradlew --no-daemon -q :com.ibm.wala.util:jar :com.ibm.wala.shrike:jar :com.ibm.wala.core:jar -x javadoc)
  WALA_CP="$(ls "$WORK"/wala-build/com.ibm.wala.{core,shrike,util}/build/libs/*.jar | tr '\n' ':')"
fi
TOOL_CP="$WALA_CP"

compile_overlay() {   # <tool copy> <out dir> <changed files relative to the tool root...>
  local tool="$1" out="$2"; shift 2
  (cd "$tool" && javac -nowarn -encoding utf8 -cp "$TOOL_CP" -d "$out" $(printf '%s\n' "$@" | grep '\.java$') 2>&1 | grep -v '^Note:' || true)
  # resources (e.g. natives.xml) come from the prebuilt jars
}
