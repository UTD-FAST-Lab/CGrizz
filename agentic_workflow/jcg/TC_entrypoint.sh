#!/bin/sh

TIMEOUT=$1
MX=$2
shift 2

if [ "$SA_FRAMEWORK" = "WALA" ]; then
  echo "Building WALA..."
  cd /WALA || exit 1
  ./gradlew :com.ibm.wala.core:assemble
  ./gradlew :com.ibm.wala.util:assemble
  ./gradlew :com.ibm.wala.shrike:assemble

  ./gradlew :com.ibm.wala.core:publishToMavenLocal || true
  ./gradlew :com.ibm.wala.util:publishToMavenLocal || true
  ./gradlew :com.ibm.wala.shrike:publishToMavenLocal || true
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
  2>&1 | tee /log.txt