# Reproducing Test Case: Implicit ClassLoader.loadClass

## Overview

This test case demonstrates WALA's unsoundness when analyzing implicit class loading triggered by the JVM during execution of `new` bytecode instructions in static initializers.

## Root Cause

WALA models explicit calls to `ClassLoader.loadClass()` and `Class.forName()` when they appear in source code with string constants, but does NOT model the implicit `ClassLoader.loadClass` calls triggered internally by the JVM when executing `new` bytecode instructions.

## Call Path

1. JVM loads `App` class
2. `App.<clinit>()` executes (static initializer)
3. Static field `helper` is initialized with `new Helper()`
4. JVM **implicitly** calls `ClassLoader.loadClass("com.example.Helper")` before instantiation
5. `Helper` class is loaded and constructor is invoked

## Missed Edge

**Caller:** `com.example.App.<clinit>()`  
**Target:** `java.lang.ClassLoader.loadClass(Ljava/lang/String;)`

WALA will not include this edge in the static call graph because it treats JVM-internal class loading as outside the scope of the application call graph.

## Building and Running

```bash
mvn clean package
java -jar target/implicit-classloader-reproducing-1.0-SNAPSHOT.jar
```

## Expected Output

```
App loaded, helper created: Helper instance
```

## Difference from Non-Reproducing Test

The non-reproducing test eliminates the implicit class loading by removing the `new` instruction from the static initializer. Instead, it initializes the static field with `null` or uses a different approach that doesn't trigger class loading during static initialization.
