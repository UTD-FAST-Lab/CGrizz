# Sourced by tool_markers/run_test.sh. Builds the pinned OPAL (ThreeAddressCode and its
# dependencies) with sbt unless OPAL_CP is set; Scala files are compiled with the build's own
# Scala compiler (SCALAC_CP, found in the coursier cache unless set).
DRIVER=OpalDriver
if [ -z "${OPAL_CP:-}" ]; then
  echo "building the pinned OPAL (set OPAL_CP to reuse it) ..."
  cp -r "$TOOL_SRC" "$WORK/opal-build"; rm -rf "$WORK/opal-build/.git"
  # the same dependency fixes as jcg/TC.Dockerfile
  (cd "$WORK/opal-build" && find . -name "*.sbt" -o -name "*.scala" -path "./project/*" | xargs grep -l "eclipse.jdt\|ecj\|bintray" \
     | xargs -r sed -i -e 's|"org.eclipse.jdt" % "ecj" % "[^"]*"|"org.eclipse.jdt" % "ecj" % "3.29.0"|g' \
       -e 's|https://repo.eclipse.org/content/repositories/eclipse-staging/|https://repo1.maven.org/maven2/|g' \
       -e 's|https://dl.bintray.com/sksamuel/sbt-plugins/|https://repo1.maven.org/maven2/|g')
  OPAL_CP="$(cd "$WORK/opal-build" && sbt -batch -J-Xmx3500m -J-Xss8m "project ThreeAddressCode" compile "export Runtime/fullClasspath" | grep -v '^\[' | grep 'OPAL/tac/target' | tail -n 1)"
fi
TOOL_CP="$OPAL_CP"
if [ -z "${SCALAC_CP:-}" ]; then
  cache="${COURSIER_CACHE:-$HOME/.cache/coursier/v1}"
  SCALAC_CP="$(find "$cache" -name 'scala-compiler-2.13.11.jar' -o -name 'scala-reflect-2.13.11.jar' -o -name 'scala-library-2.13.11.jar' | tr '\n' ':')"
fi

scalac_() { java -Xss8m -cp "$SCALAC_CP" scala.tools.nsc.Main -nowarn -classpath "$1" -d "$2" "${@:3}"; }

compile_driver() { scalac_ "$TOOL_CP" "$1" "$T/$DRIVER.scala"; }

compile_overlay() {   # <tool copy> <out dir> <changed files relative to the tool root...>
  local tool="$1" out="$2"; shift 2
  (cd "$tool" && scalac_ "$TOOL_CP" "$out" $(printf '%s\n' "$@" | grep '\.scala$'))
}
