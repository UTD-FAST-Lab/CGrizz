#!/usr/bin/env bash
# Builds the agent and runs every example under examples/ with bytecode
# verification forced on, then checks oracle.json against expect.json.
# Usage: ./test.sh [example ...]
set -u
cd "$(dirname "$0")"
mvn -q -B package || exit 1
JAR="$PWD/target/cgrizz-oracle-agent.jar"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail=0
for dir in ${@:-examples/*/}; do
  name="$(basename "$dir")"
  out="$WORK/$name"
  mkdir -p "$out/classes"
  pkg_main="ex.$name.Main"
  if ! javac -source 8 -target 8 -nowarn -XDignore.symbol.file -d "$out/classes" "examples/$name"/*.java 2>"$out/javac.log"; then
    echo "[$name] FAIL: javac"; cat "$out/javac.log"; fail=1; continue
  fi
  java -Xverify:all -XX:-OmitStackTraceInFastThrow \
       -javaagent:"$JAR=examples/$name/spec.json" -Dcgrizz.oracle.out="$out/oracle.json" \
       -cp "$out/classes" "$pkg_main" >"$out/run.log" 2>&1
  if grep -q "VerifyError\|ClassFormatError\|could not instrument\|spec error" "$out/run.log"; then
    echo "[$name] FAIL: agent/verifier problem"; cat "$out/run.log"; fail=1; continue
  fi
  if [ ! -f "$out/oracle.json" ]; then
    echo "[$name] FAIL: no oracle.json"; cat "$out/run.log"; fail=1; continue
  fi
  if python3 check_example.py "$out/oracle.json" "examples/$name/expect.json"; then
    echo "[$name] ok"
  else
    echo "[$name] FAIL"; fail=1
  fi
done
exit $fail
