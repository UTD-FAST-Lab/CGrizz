---
name: instrumentation
description: Write an ASM Java-agent to dynamically track a root cause pattern and its missed-edge symptoms, builds it, runs it against the test case, and writes oracle.json.
---

# Instrumentation Agent

You receive an unsoundness report and a reproducing test case. Your job is to instrument the java projects at runtime to observe the root cause pattern and the missed edges it causes, producing an `oracle.json` file.

## Inputs

You will receive:
- **Unsoundness report** — describes the root cause pattern and the missed edge
- **Test case path** — the reproducing Maven project directory; the built JAR is at `target/<artifactId>-<version>.jar`
- **Output path** — where to write `oracle.json`

## Step 1: Understand the root cause pattern from the report

Read **only the unsoundness report** — do NOT look at the SA tool source or the test case source yet.

Extract:
- `pattern_class` — the kind of missed edge (e.g. `reflection_indirect_instantiation`, `virtual_dispatch`, `dynamic_proxy`)
- `caller` and `target` — the missed edge
- `toolLimitation.reason` — why the tool missed it

Choose a **formal representation** for the root cause pattern that suits the mechanism:
- **Reflection** → dataflow from load site to dispatch site
- **Virtual / interface dispatch** → object's runtime type at a call site (receiver type at INVOKEVIRTUAL/INVOKEINTERFACE)
- **Dynamic proxy** → InvocationHandler.invoke call chain
- **Factory / callback** → dataflow from factory return value to method call
- **Other** → whichever representation captures when the pattern fires and what it dispatches to

Document which representation you chose and why (one sentence).

## Step 2: Determine what to track

Define:
- **Root Cause (RC)** — the location (method + bytecode PC) where the pattern fires. This is the point in the *test case's* bytecode that represents the root cause (e.g. a `Class.forName` call, a GETSTATIC of a final interface-typed field, an `Proxy.newProxyInstance` call, etc.).
- **Symptom (Sym)** — every call site that is *caused by* the RC. A symptom is linked to an RC when the receiver object at the call site was produced (directly or transitively) at the RC site or any other relationship that you can find between RC and Sym.

One RC can have multiple Syms; a Sym belongs to exactly one RC.

**Important — symptom edge validation:** When recording a symptom, resolve the actual declaring class of the dispatched method by walking the receiver's runtime class hierarchy upward until you find the class that actually declares the method. Do NOT use `receiver.getClass()` directly as the callee class — inherited methods must be attributed to their declaring superclass (or interface). This mirrors how call graphs represent edges.

**Important — root cause location:** the location of the root cause is an instruction's location. This is how static analysis tools parse and reason about different language features. So the root cause should be an instruction inside a method body that encloses the instruction. The symptom might happen somewhere else during the execution but the root cuase should be the location of the instruction in the enclosing method. You should be able to determine the way you should find root causes from the `toolLimitation` section in the unsoundness report. 

## Step 3: Write the ASM instrumentation

Write the ASM instrumentation to `<test_case_path>/instrumentation/` in a Maven form.

Write the four files to track your specific pattern:

**Agent.java**
- Accept `oracle.json` path from agent args (default: `oracle.json` in working dir)
- Set the class filter in the transformer: instrument only application classes (skip `java/`, `javax/`, `sun/`, `jdk/`, `com/sun/`, and your own agent package)

***Frame computation:*** Avoid recomputing stack-map frames whenever possible — recomputation forces the writer to resolve class hierarchies at transform time, which can trigger class loading and crash the JVM. So:
- **If your injection is branch-free** (only straight-line, stack-balanced sequences — push/dup args and `INVOKESTATIC` into the Tracker, leaving the operand stack unchanged), keep the original frames: build the writer with `ClassWriter.COMPUTE_MAXS` (not `COMPUTE_FRAMES`) and accept the reader with no `SKIP_FRAMES` flag. This never resolves classes, so it is safe. Prefer this — RC/Sym call-site tracking is naturally branch-free.
- **Only if your injection adds control flow** (new branches, jumps, try/catch, or new local types) the original frames become invalid and you must use `COMPUTE_FRAMES`. In that case override the writer's `getCommonSuperClass` to resolve super-types by reading `.class` bytes as a resource via `ClassReader`, never by loading classes (no `Class.forName`).

**ClassVisitor.java**
- Route every non-abstract, non-native method to your MethodVisitor
- Pass the class name (internal form) to the MethodVisitor

**MethodVisitor.java**
- Implement the pattern-specific injection logic:
  - **RC injection**
  - **Sym injection**
- Maintain a simplified operand-stack simulation (Boolean slots: TRUE = produced at RC site, FALSE = other) to connect RC and Sym sites when the receiver's origin is trackable statically within the method; for inter-method flows, rely on the Tracker's runtime registry instead

**Tracker.java**
- `onRootCause(...)` — record the RC: store a key `"RC:<callerMethod>@<pc>"` → `RootCauseEntry` in a `ConcurrentHashMap`; also register the produced object's identity (class name or object reference, depending on pattern) in a separate registry so symptoms can link back to it
- `onSymptom(receiver, callerMethod, pc, calleeName, calleeDesc)` — look up the receiver (or its class hierarchy / declaring class) in the RC registry; if found, resolve the actual declaring class for `calleeName`/`calleeDesc` by walking the hierarchy, then append a `Symptom` to the matching `RootCauseEntry`
- `dumpJson(outputPath)` — write `oracle.json` in the format shown below; use only standard Java 8 library (no external JSON libs)

**pom.xml**
- Keep ASM 9.x dependency + shade plugin (relocate `org.objectweb.asm` → `edu.utd.fastlab.shade.asm`)
- Set `<Premain-Class>` to your renamed Agent class
- Set `<Can-Retransform-Classes>true</Can-Retransform-Classes>`
- Java 8 source/target


**IMPORTANT**: The agent should NOT create the instrumentation specifically for the test case. It should be reusable for any real world or synthetic java project and should be able to track the root cause pattern and its symptoms in any project. So the instrumentation should be based on the root cause pattern, not the specific test case. You should be able to run the same agent on a different test case that has the same root cause pattern and it should work without any modification.

## Step 4: Build the agent

```bash
cd <test_case_path>/instrumentation
mvn clean package -q
```

The agent JAR is at `target/<artifactId>-1.0-SNAPSHOT.jar`.

If the build fails, read the compiler errors and fix them, then rebuild.

## Step 5: Build the test case (if not already built)

```bash
cd <test_case_path>
mvn clean package -q
```

Locate the test case JAR at `target/<artifactId>-<version>.jar` and the main class from `pom.xml`.

## Step 6: Run the agent against the test case

```bash
java -javaagent:<test_case_path>/instrumentation/target/<agent-jar>=<oracle_output_path> \
     -jar <test_case_path>/target/<test-jar>.jar
```

Capture stdout and stderr. If the JVM crashes or the agent throws errors:
1. Read the stack trace
2. Fix the instrumentation code
3. Rebuild and re-run (repeat until the run completes without fatal errors)


## Step 7: Verify oracle.json

Check that `oracle.json` was written and is valid JSON. If `dumpJson` was not called (e.g. JVM crash before shutdown hook), fix the root cause and re-run.


## Output format

`oracle.json`:

```json
{
  "RC:com.example.Test.test()V@0": {
    "method": "com.example.Test.test()V",
    "pc": 0,
    "symptoms": [
      {
        "caller": "com.example.Test.test()V",
        "callee": "java.io.PrintStream.println(Ljava/lang/String;)V",
        "pc": 2
      }
    ]
  },
  "RC:com.example.App.main([Ljava/lang/String;)V@4": {
    "method": "com.example.App.main([Ljava/lang/String;)V",
    "pc": 4,
    "symptoms": [
      {
        "caller": "com.example.App.main([Ljava/lang/String;)V",
        "callee": "java.io.PrintStream.println(Ljava/lang/String;)V",
        "pc": 6
      }
    ]
  }
}
```

Keys are `"RC:<method_descriptor>@<pc>"`. Method descriptors should follow the strict format: `package.classname.methodname(parameters)`. This format is later going to be parsed with a script, so make sure to keep the format exactly as this.
I use the following the commands to get the classname and method name of `rc.method`, `symptom.caller` and `symptom.callee`: 

```python
ex_classname = expected_method.split("(")[0].rsplit(".", 1)[0] if "." in expected_method else None
ex_methodname = expected_method.split("(")[0].rsplit(".", 1)[1] if "." in expected_method else None
```

For example, the expected output for the given oracle.json file for the root cause method is:

```
ex_classname = com.example.Test
ex_methodname = test
```