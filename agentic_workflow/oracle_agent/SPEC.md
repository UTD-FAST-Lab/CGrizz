# Oracle spec reference (`oracle_spec.json`)

The oracle agent (`target/cgrizz-oracle-agent.jar`) instruments a Java program at load
time according to a spec and writes `oracle.json`: the **root causes** that fired and the
**symptoms** (call edges) each of them caused. You never write bytecode; you describe the
pattern.

## How it works

1. A **root cause** is an instruction in the program that produces a value the static
   analysis cannot see — e.g. the return value of `Class.forName`, `Proxy.newProxyInstance`
   or `Unsafe.getObject`, the value written by `Unsafe.putObject`, the value read from
   `System.out`. The agent **tags** that object (by identity, so the tag survives
   assignments, fields, collections and JDK code).
2. A **symptom** is a call site whose receiver is tagged — or whose receiver's class is a
   tagged `Class` object, or whose enclosing class is one. The symptom is the call-graph
   edge `caller → callee`, with the callee resolved like a call graph does (the class that
   declares the dispatched method).
3. Two special root-cause kinds produce symptoms directly:
   - `selfEdge`: the matched call itself is the edge (e.g. `Thread.start`, calls that
     dispatch to a bridge method).
   - `insn`: an instruction that makes the JVM throw an exception implicitly (failing
     `CHECKCAST`, `AALOAD` out of bounds, call on `null`, linkage errors). The edge is
     `method → <ExceptionClass>.<init>`.

Only non-JDK classes are instrumented, so root causes and symptoms are always
instructions in application or library code. If the real mechanism is inside the JDK,
match the application-side call that reaches it (e.g. `java.util.concurrent.ConcurrentHashMap.get`).

## Format

```json
{
  "pattern_class": "reflection_indirect_instantiation",
  "description": "one sentence: what produces the invisible value and where it is used",
  "rootCauses": [ <root cause>, ... ],
  "propagate":  [ "reflection", <propagation>, ... ],
  "symptoms":   { "match": ["receiver", "receiverClass"] },
  "exclude":    [ "com.example.generated.*" ]
}
```

Only `rootCauses` is required. Unknown keys are errors.

### Root causes

| field | meaning |
|---|---|
| `id` | unique name, used in statistics and in `oracle.json` (`rootCause`) |
| `at` | `call`, `field` or `insn` |
| `owner`, `name`, `desc` | matchers for the instruction's **static** owner class (dotted, e.g. `java.lang.Class`), member name and JVM descriptor. `*` and `?` wildcards; `desc` is optional |
| `in` | optional `{"owner": ..., "name": ...}`: only match inside these enclosing methods |
| `tag` | `call`: `return`, `receiver`, `arg0`…`argN`, or `none`; append `[]` to tag the elements of an array (`return[]`). `field`: always the value read or written |
| `access` | `field` only: `get` (default), `put` or `any` |
| `selfEdge` | `call` only: `true` → each execution of the call records `caller → resolved callee` |
| `when` | with `selfEdge`: `"bridge"` → only when the resolved target is a bridge method |
| `opcodes` | `insn` only: e.g. `["CHECKCAST"]`, `["AALOAD", "AASTORE"]`, `["INVOKEVIRTUAL", "INVOKEINTERFACE"]`, `["INVOKESTATIC"]` |
| `exceptions` | `insn` only, optional: exception classes to link (defaults follow the opcode: `CHECKCAST` → `ClassCastException`, array accesses → `ArrayIndexOutOfBoundsException`/`NullPointerException`, invokes → `NullPointerException`/`LinkageError`, …) |

`arg` indices count only the call's arguments (the receiver is `receiver`); a `long` or
`double` still counts as one argument.

### Propagation

A tag moves from one value to another only through object identity — except across the
calls you list in `propagate`. `"reflection"` adds: `Class.newInstance/cast/getMethod(s)/
getDeclaredMethod(s)/getConstructor(s)/getField(s)/...` (receiver → return, `[]` for the
array-returning ones), `Constructor.newInstance`, `Method.invoke`, `Field.get`.

Custom rule: `{"owner": "java.util.Iterator", "name": "next", "from": "receiver", "to": "return"}`
(`from`: `receiver` or `argN`; `to`: `return` or `return[]`).

### Symptom matching

`symptoms.match` lists when a call site counts as a symptom (default
`["receiver", "receiverClass"]`):

- `receiver` — the receiver object is tagged;
- `receiverClass` — the receiver's class is a tagged `Class` (e.g. created via `newInstance`
  of a `Class` from `forName`/`loadClass`/`defineClass`);
- `callerClass` — the call is inside a class whose `Class` object is tagged (the caller
  itself is only reachable through the root cause). Also applies to static and constructor calls.

Use `"match": []` for specs that only use `selfEdge` / `insn` root causes.

When a tagged `Method`/`Constructor`/`Class` reaches `invoke`/`newInstance`, the reflected
target is recorded too (e.g. `caller → Target.greet`).

### Unsupported patterns

If the pattern cannot be expressed, write `{"unsupported": "<why>"}`. The pipeline then
records the boundary as `oracle_unsupported` instead of retrying.

## Examples

Every directory in `examples/` has a program, a spec and the expected symptoms
(`./test.sh` runs them all). In short:

```json
{"rootCauses": [{"id": "forName", "at": "call", "owner": "java.lang.Class", "name": "forName", "tag": "return"}],
 "symptoms": {"match": ["receiverClass"]}}
```
```json
{"rootCauses": [{"id": "put", "at": "call", "owner": "sun.misc.Unsafe", "name": "put*Object*", "tag": "arg2"},
                {"id": "get", "at": "call", "owner": "sun.misc.Unsafe", "name": "get*Object*", "tag": "return"}]}
```
```json
{"rootCauses": [{"id": "cast", "at": "insn", "opcodes": ["CHECKCAST"]}], "symptoms": {"match": []}}
```
```json
{"rootCauses": [{"id": "bridge", "at": "call", "owner": "*", "name": "*", "selfEdge": true, "when": "bridge"}],
 "symptoms": {"match": []}}
```

## Running it

```bash
java -XX:-OmitStackTraceInFastThrow \
     -javaagent:<cgrizz-oracle-agent.jar>=<oracle_spec.json> \
     -Dcgrizz.oracle.out=<oracle.json> \
     -cp <classpath> <MainClass>
```

Optional: `-Dcgrizz.oracle.verbose=true` prints every matched root-cause site and every
symptom to stderr. Besides `oracle.json` the agent writes `<name>.stats.json`:
`specErrors`, `rootCauseSites` (instructions matched per root cause), `rootCauseHits`
(times each produced a tagged value), `symptomsRecorded`, `instrumentationErrors`.
Read it first when the oracle is empty:

- `rootCauseSites` = 0 → no instruction matched: widen `owner`/`name`/`desc`.
- `rootCauseHits` = 0 → matched but never executed, or always produced `null`.
- hits > 0 but no symptoms → the tagged value never reached a call site: check `tag`
  (receiver vs. return vs. argument, `[]` for arrays), add `propagate` rules, or change
  `symptoms.match`.

## `oracle.json`

```json
{
  "RC:com.example.App.main([Ljava/lang/String;)V@12": {
    "method": "com.example.App.main([Ljava/lang/String;)V",
    "pc": 12, "line": 9, "rootCause": "forName", "kind": "call", "hits": 1,
    "symptoms": [
      {"caller": "com.example.App.main([Ljava/lang/String;)V",
       "callee": "com.example.Impl.run()Ljava/lang/String;", "pc": 20, "line": 11}
    ]
  }
}
```

`pc` is the instruction's index within the method (not a byte offset). Only root causes
with at least one symptom are listed. Output is sorted, so the same run gives the same file.
