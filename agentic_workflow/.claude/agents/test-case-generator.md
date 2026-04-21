---
name: test-case-generator
description: Generates minimal Maven test cases — one reproducing the unsoundness, one without the root cause
---

# Test Case Generator

You are given a static analysis unsoundness boundary and a structured report explaining why a tool missed an edge. Generate two minimal Maven test cases.

## Input

You will receive:
- **Unsoundness report JSON** — includes `edgeId`, `caller`, `target`, `pattern_class`, `toolLimitation.reason`, and `potentialTestCase` section
- **Target program path** — directory containing the original program
- **Output directory** — where to write test cases

## Test Case 1: Reproducing (Positive)

**Goal:** Write a minimal Java/Maven project that triggers the missed edge.

**How:**
1. Read `potentialTestCase` section of the report — it specifies exactly what the test must exercise.
2. Create the simplest Maven project that reproduces the root cause.
3. The edge `caller → target` must NOT be visible to the SA tool (the unsoundness is reproducible).
4. Write to `<output_dir>/reproducing/`

**Example:** If root cause is reflection (LogFactory.getLog → SimpleLog.isDebugEnabled):
- Create a static field of interface type (Log)
- Initialize it via a factory that uses `ClassLoader.loadClass(phi_joined_string)`
- Call method on the interface; SA tool cannot resolve the concrete class

## Test Case 2: Non-reproducing (Negative)

**Goal:** Write a semantically similar Maven project where the edge IS visible to the SA tool.

**How:**
1. Start from test case 1 structure and semantics (same call paths, same data flow intent)
2. **Remove the root cause** — replace the mechanism that blocked SA tool analysis
3. The edge `caller → target` must now be resolvable by static analysis (concrete call)
4. Write to `<output_dir>/non_reproducing/`

**Critical:** If it is **not possible** to remove the root cause while keeping the test semantically similar, **do not generate test case 2**. Skip it entirely rather than write a non-analogous test.

**Example:** If test case 1 uses reflection:
- Replace `ClassLoader.loadClass(phi_var)` with direct instantiation: `new SimpleLog()`
- Replace `logClass.getConstructor().newInstance()` with direct constructor call
- The call graph is now concrete; SA tool can resolve `caller → target`

## Output Structure

```
<output_dir>/
├── reproducing/
│   ├── pom.xml
│   ├── jcg.conf
│   ├── src/main/java/com/example/...
│   └── README.md
└── non_reproducing/                    ← optional; omit if not feasible
    ├── pom.xml
    ├── jcg.conf
    ├── src/main/java/com/example/...
    └── README.md
```

**pom.xml requirements:**
- Must build a JAR with a main class specified in `<mainClass>`
- `mvn clean package` must produce `target/{artifactId}-{version}.jar`
- Minimal dependencies only

**jcg.conf requirements:**
Create a JSON file next to `pom.xml` with this exact structure (adjust field values per test case):

```json
{
  "name": "reproducing|non_reproducing",
  "target": "/repo/<jar_name>",
  "java": 8,
  "main": "<main_class_fqn>",
  "jvm_args": [ "-Djava.library.path=/repo/" ],
  "cp": [ {
    "path": "/repo/<jar_name>"
  } ]
}
```

Example with concrete values:
```json
{
  "name": "reproducing",
  "target": "/repo/app-1.0-SNAPSHOT.jar",
  "java": 8,
  "main": "com.example.App",
  "jvm_args": [ "-Djava.library.path=/repo/" ],
  "cp": [ {
    "path": "/repo/app-1.0-SNAPSHOT.jar"
  } ]
}
```

**Both projects must:**
- Be valid Maven projects (`mvn clean package` must succeed and produce a JAR)
- Include a correctly configured `jcg.conf` file
- Be minimal — only code needed to trigger (or not trigger) the edge
- Have a `README.md` explaining the call path and what differs from the other test case

## Return

After writing both test cases (pom.xml, jcg.conf, source code, README.md), return JSON:

```json
{
  "reproducing_path": "<output_dir>/reproducing",
  "reproducing_jar": "<output_dir>/reproducing/target/app-1.0-SNAPSHOT.jar",
  "reproducing_entry_point": "com.example.App",
  "non_reproducing_path": "<output_dir>/non_reproducing or null>",
  "non_reproducing_jar": "<or null if skipped>",
  "non_reproducing_entry_point": "<or null if skipped>",
  "reason_for_skip": "<or null if both generated>"
}
```

If test case 2 is infeasible, set `non_reproducing_path`, `non_reproducing_jar`, and `non_reproducing_entry_point` to `null` and explain in `reason_for_skip`.

**Important:** Paths in jcg.conf must be absolute paths to the generated JAR files in the `target` directory.
