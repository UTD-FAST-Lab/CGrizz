# Reproducing Test Case: Implicit Class Loading via NEW in Static Initializer

## Description

This test case reproduces the unsoundness where WALA misses the implicit `ClassLoader.loadClass()` call triggered by a `NEW` instruction in a static initializer.

## Call Path

1. JVM loads `Main` class
2. `Main.<clinit>()` executes to initialize static field `helper`
3. Static field initialization contains `new HelperClass()`
4. JVM implicitly calls `ClassLoader.loadClass("com.example.HelperClass")` to load `HelperClass`
5. `HelperClass.<clinit>()` executes
6. `HelperClass` instance is created and assigned to `helper`

## Expected Behavior

**Runtime (JVM):** The edge `Main.<clinit>() -> ClassLoader.loadClass(String)` exists because the JVM must load `HelperClass` before instantiating it.

**WALA 0-CFA:** WALA abstracts away the class loading mechanism. It directly models `Main.<clinit>() -> HelperClass.<clinit>()` without modeling the intermediate `ClassLoader.loadClass()` call. This is a design decision where WALA treats class loading as implicit.

## Root Cause

WALA's `SSAPropagationCallGraphBuilder.visitNew()` method calls `processClassInitializer()` which directly adds the `<clinit>` invocation without modeling the `ClassLoader.loadClass()` call that precedes it in the actual JVM execution.

## Build and Run

```bash
mvn clean package
java -jar target/class-loading-reproducing-1.0-SNAPSHOT.jar
```

## Difference from Non-Reproducing Test Case

The non-reproducing test case uses explicit class loading (e.g., `Class.forName()`) in the main method rather than implicit loading via `NEW` in a static initializer. This makes the `ClassLoader.loadClass()` call explicit in the source code, though WALA may still abstract it depending on implementation.
