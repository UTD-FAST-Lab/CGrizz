#!/bin/sh

TIMEOUT=$1
MX=$2
shift 2

JCG_HOME="${JCG_HOME:-/home/JCG/JCG}"
CP_FILE="$JCG_HOME/.runtime-classpath"
LOG_FILE="${JCG_LOG:-/log.txt}"
JAVA_BIN="${JCG_JAVA_BIN:-/opt/jdk8u342-b07/bin}"

# Fast path (JCG_FAST_PATH=1, WALA and Soot): the annotation changes one or two
# Java files. Compile only those (CHANGED_SOURCES, relative to the tool root)
# against the prebuilt tool + JCG classpath exported at image build time and put
# them first on the classpath — instead of rebuilding and republishing the whole
# tool and running `sbt clean update` on JCG. Anything unusual (Scala/OPAL, a
# non-Java change, javac failure, no exported classpath) falls back to the full
# rebuild below. Assumes tools/<tool> is the commit built into this image.
if [ "$JCG_FAST_PATH" = "1" ] && [ "$1" = "runMain" ] && grep -q "jcg_evaluation" "$CP_FILE" 2>/dev/null \
   && { [ "$SA_FRAMEWORK" = "WALA" ] || [ "$SA_FRAMEWORK" = "SOOT" ]; }; then
  TOOL_DIR="${JCG_TOOL_ROOT:-}/$SA_FRAMEWORK"
  OVERLAY=$(mktemp -d)
  FILES=""
  NON_JAVA=0
  OLD_IFS=$IFS; IFS=:
  for f in $CHANGED_SOURCES; do
    case "$f" in
      *.java) FILES="$FILES
$TOOL_DIR/$f" ;;
      *) NON_JAVA=1 ;;
    esac
  done
  IFS=$OLD_IFS
  if [ "$NON_JAVA" = "1" ]; then
    echo "Fast path: non-Java tool change ($CHANGED_SOURCES), using the full rebuild"
  elif [ -z "$FILES" ] || echo "$FILES" | sed '/^$/d' | tr '\n' '\0' \
         | xargs -0 "$JAVA_BIN/javac" -nowarn -encoding utf8 -cp "$(cat "$CP_FILE")" -d "$OVERLAY"; then
    MAIN=$2
    shift 2
    echo "Fast path: overlay of [$CHANGED_SOURCES] on the prebuilt $SA_FRAMEWORK, running $MAIN"
    cd "$JCG_HOME" || exit 1
    timeout --foreground $TIMEOUT \
      "$JAVA_BIN/java" -Xmx${MX} -cp "$OVERLAY:$(cat "$CP_FILE")" "$MAIN" "$@" \
      2>&1 | tee "$LOG_FILE"
    exit
  else
    echo "Fast path: javac failed on [$CHANGED_SOURCES], using the full rebuild"
  fi
fi

if [ "$SA_FRAMEWORK" = "WALA" ]; then
  echo "Building WALA..."
  cd /WALA || exit 1
  ./gradlew :com.ibm.wala.core:publishLocalPublicationToMavenLocal -x javadoc -x testFixturesJavadoc
  ./gradlew :com.ibm.wala.util:publishLocalPublicationToMavenLocal -x javadoc -x testFixturesJavadoc
  ./gradlew :com.ibm.wala.shrike:publishLocalPublicationToMavenLocal -x javadoc -x testFixturesJavadoc

  echo "=== What version did WALA publish? ==="
  ls ~/.m2/repository/com/ibm/wala/com.ibm.wala.core/ 2>/dev/null || echo "Nothing in Maven local"

  echo "=== Is 1.5.7 specifically present? ==="
  ls ~/.m2/repository/com/ibm/wala/com.ibm.wala.core/1.5.7/ 2>/dev/null || echo "1.5.7 NOT FOUND"

  echo "=== Coursier cache for WALA ==="
  find ~/.cache/coursier -path "*wala*" -name "*.jar" 2>/dev/null | head -5 || echo "No Coursier cache"

  echo "=== Clearing caches ==="
  rm -rf ~/.ivy2/cache/com.ibm.wala
  rm -rf ~/.cache/coursier/v1/https/repo1.maven.org/maven2/com/ibm/wala
fi

if [ "$SA_FRAMEWORK" = "OPAL" ]; then
  echo "Building OPAL..."
  cd /OPAL || exit 1
  grep -r "ecj" --include="*.sbt" --include="*.scala" -l && \
  find . -name "*.sbt" -o -name "*.scala" | xargs grep -l "eclipse.jdt\|ecj" | \
  xargs sed -i \
      -e 's|"org.eclipse.jdt" % "ecj" % "[^"]*"|"org.eclipse.jdt" % "ecj" % "3.29.0"|g' \
      -e 's|https://repo.eclipse.org/content/repositories/eclipse-staging/|https://repo1.maven.org/maven2/|g' \
      -e 's|https://dl.bintray.com/sksamuel/sbt-plugins/|https://repo1.maven.org/maven2/|g' && \
  sbt publishLocal
fi

if [ "$SA_FRAMEWORK" = "SOOT" ]; then
  echo "Building SOOT..."
  cd /SOOT || exit 1
  mvn clean compile assembly:single
  mvn install:install-file \
  -Dfile=target/sootclasses-trunk-jar-with-dependencies.jar \
  -DgroupId=org.soot-oss \
  -DartifactId=soot \
  -Dversion=4.4.1 \
  -Dpackaging=jar \
  -DgeneratePom=true

  rm -rf ~/.ivy2/cache/org.soot-oss
  rm -rf ~/.cache/coursier/v1/https/repo1.maven.org/maven2/org/soot-oss
fi

echo "Building JCG..."
cd /home/JCG/JCG || exit 1
# sbt compile
sbt clean update

# exec /bin/bash

echo "Running analysis..."
timeout --foreground $TIMEOUT \
  sbt -java-home /opt/jdk8u342-b07/jre -J-Xmx${MX} \
    "; project jcg_evaluation; $*" \
  2>&1 | tee "$LOG_FILE"