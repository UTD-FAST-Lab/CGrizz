#!/bin/sh

TIMEOUT=$1
MX=$2
shift 2

# Fast path (JCG_FAST_PATH=1): run the JCG main class with plain java on the
# runtime classpath exported at image build time, instead of starting sbt for
# every container. jcg_evaluation does not fork, so `sbt runMain` runs the same
# main in-process from the same working directory.
CP_FILE="${JCG_HOME:-/home/JCG/JCG}/.runtime-classpath"
LOG_FILE="${JCG_LOG:-/log.txt}"
JAVA="${JCG_JAVA:-/opt/jdk8u342-b07/jre/bin/java}"
if [ "$JCG_FAST_PATH" = "1" ] && [ "$1" = "runMain" ] && grep -q "jcg_evaluation" "$CP_FILE" 2>/dev/null; then
  MAIN=$2
  shift 2
  echo "Fast path: $JAVA $MAIN"
  timeout --foreground $TIMEOUT \
    "$JAVA" -Xmx${MX} -cp "$(cat "$CP_FILE")" "$MAIN" "$@" \
    | tee "$LOG_FILE"
  exit
fi

timeout --foreground $TIMEOUT \
  sbt -java-home /opt/jdk8u342-b07/jre -J-Xmx${MX} \
    "; project jcg_evaluation; $*" \
  | tee "$LOG_FILE"
