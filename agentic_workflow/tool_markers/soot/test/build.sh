# Sourced by tool_markers/run_test.sh. Builds the pinned Soot jar unless SOOT_CP is set.
DRIVER=SparkDriver
if [ -z "${SOOT_CP:-}" ]; then
  echo "building the pinned Soot jar (set SOOT_CP to reuse one) ..."
  cp -r "$TOOL_SRC" "$WORK/soot-build"; rm -rf "$WORK/soot-build/.git"
  (cd "$WORK/soot-build" && mvn -B -q -DskipTests -Dmaven.javadoc.skip=true compile assembly:single)
  SOOT_CP="$WORK/soot-build/target/sootclasses-trunk-jar-with-dependencies.jar"
fi
TOOL_CP="$SOOT_CP"

compile_overlay() {   # <tool copy> <out dir> <changed files relative to the tool root...>
  local tool="$1" out="$2"; shift 2
  (cd "$tool" && javac -nowarn -encoding utf8 -cp "$TOOL_CP" -d "$out" $(printf '%s\n' "$@" | grep '\.java$') 2>&1 | grep -v '^Note:' || true)
}
