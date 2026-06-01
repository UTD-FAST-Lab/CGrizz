#!/bin/sh

TIMEOUT=$1
MX=$2
shift 2

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
  2>&1 | tee /log.txt