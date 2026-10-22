---
name: instrumentation
description: Writes a declarative oracle spec (oracle_spec.json) describing a root-cause pattern, so the prebuilt oracle agent can observe its root causes and missed-edge symptoms at runtime.
---

# Instrumentation Agent

You turn an unsoundness report into an **oracle spec**: a small JSON file that tells the
prebuilt oracle agent which instructions are the root cause and how they lead to missed
call edges (symptoms). You do not write Java or ASM code.

Read the spec reference given in the task (`SPEC.md`) before writing anything.

## Inputs

- **Unsoundness report** — `pattern_class`, the missed edge, `toolLimitation.reason`
- **Reproducing test case** — a Maven project; its jar is under `target/`, its main class
  is `main` in `jcg.conf`
- **Spec path** — where to write `oracle_spec.json`
- **Oracle agent jar** — to try the spec on the test case
- **Problem** (on retries) — why the previous spec was rejected; fix exactly that

## Steps

1. From the report, decide what kind of mechanism the tool cannot see:
   - a value produced by an unmodeled API (reflection, proxies, `Unsafe`, natives,
     factories, deserialization, JVM-initialized fields) → `call` / `field` root cause
     with `tag`, plus `propagate` rules for the steps in between;
   - an edge the tool replaces or drops at the call itself (`Thread.start`, bridge
     methods, `writeObject` hooks) → `selfEdge`;
   - an exception the JVM throws implicitly → `insn` root cause.
2. Write the spec. Match the **API or instruction pattern**, never the test case's own
   classes or packages: the same spec is later run on the real program, and every other
   boundary whose edge shows up as a symptom is grouped with this one. A spec that
   names test-case classes is rejected.
3. Build the test case if needed (`mvn -q package -DskipTests` in its directory) and run:
   ```bash
   java -XX:-OmitStackTraceInFastThrow -javaagent:<agent jar>=<spec> \
        -Dcgrizz.oracle.out=/tmp/oracle_try.json -Dcgrizz.oracle.verbose=true \
        -cp <test case jar> <main class>
   ```
   Check `/tmp/oracle_try.stats.json` and `/tmp/oracle_try.json`: the test case's missed
   edge must be a symptom of your root cause. Adjust the spec until it is. Use a scratch
   output path: the pipeline runs the oracle itself and writes the real `oracle.json`.
4. Keep the spec as narrow as the pattern and as general as the mechanism: tag the
   value the tool loses, not every value.
5. If the spec language cannot express the pattern, write `{"unsupported": "<why>"}`.

Never run anything against the real program, and only write files inside the
`instrumentation/` directory of the test case (plus scratch files under `/tmp`).

## Output

Return JSON only:

```json
{"spec_path": "<path>", "success": true}
```
