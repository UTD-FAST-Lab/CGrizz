# Reproducing Test Case: Implicit ClassLoader.loadClass() in Static Initializer

## Overview

This test case demonstrates the unsoundness where WALA misses the implicit edge from a class's `<clinit>` method to `ClassLoader.loadClass()` when a static field initialization contains a `new` instruction.

## Root Cause

When the JVM executes a `new` instruction (e.g., `new Helper()`) in a static initializer, it must first load the class if it hasn't been loaded yet. This triggers an implicit call to `ClassLoader.loadClass()`. WALA treats `new` instructions (SSANewInstruction) purely as object allocation operations without modeling this underlying class loading mechanism.

## Call Path

1. `App.main()` is invoked (entry point)
2. JVM triggers `App.<clinit>()` before executing main
3. `App.<clinit>()` executes static field initialization: `public static Helper instance = new Helper();`
4. The `new Helper()` instruction implicitly calls `ClassLoader.loadClass("com.example.Helper")` (MISSING EDGE)
5. After class is loaded, the Helper object is allocated and assigned to the static field

## Expected Behavior

**Dynamic Call Graph (Runtime):**
- Edge exists: `App.<clinit>()` -> `ClassLoader.loadClass()`

**WALA Call Graph (Static Analysis):**
- Edge MISSING: `App.<clinit>()` -> `ClassLoader.loadClass()`
- WALA only sees the allocation operation, not the implicit class loading

## Build and Run

```bash
mvn clean package
java -jar target/implicit-classloading-reproducing-1.0-SNAPSHOT.jar
```

## Difference from Non-Reproducing Test Case

The non-reproducing test case avoids placing the `new` instruction in a static initializer. Instead, it performs the allocation in an instance method or main method, where the class loading has already occurred during earlier program initialization, making the implicit call less relevant to the analysis.
