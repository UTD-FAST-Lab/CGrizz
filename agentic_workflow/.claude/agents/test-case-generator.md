---
name: test-case-generator
description: Generates minimal Maven test cases — one reproducing the unsoundness, one without the root cause
---

# Test Case Generator

You are given a static analysis unsoundness boundary and a structured report explaining why a tool missed an edge. Generate two minimal Maven test cases.

## CRITICAL: File system constraint

You MUST only create, modify, or write files inside the two output directories given to you in the task prompt (`<reproducing_dir>` and `<non_reproducing_dir>`). Never write or modify any file outside those directories, including the current working directory or any path above them.

## Input

You will receive:
- **Unsoundness report JSON** — includes `edgeId`, `caller`, `target`, `evidence`, `toolLimitation.reason`, and `potentialTestCase` section
- **Target program path** — directory containing the original program
- **Output directories** — where to write test cases or reproducing and non_reproducing

## Test Case 1: Reproducing (Positive)

**Goal:** Write a minimal Java/Maven project that triggers the missed edge.

**How:**
1. Read `potentialTestCase` section of the report — it specifies exactly what the test must exercise.
2. Create the simplest Maven project that reproduces the root cause. This is not necessary replicating the caller and missed edge in the unsoundness report, but a minimal more general test case. 
3. The edge `caller → target` must NOT be visible to the SA tool (the unsoundness is reproducible).
4. Write to `<output_dir>/reproducing/`
5. The Caller of the missed edge and the root cause should be in the application code itself not in a library method. 

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
