package com.example;

/**
 * Reproducing test case for implicit ClassLoader.loadClass() call.
 *
 * This test demonstrates that WALA misses the implicit ClassLoader.loadClass
 * call that occurs when the JVM executes a 'new' instruction during static
 * initialization.
 *
 * When App class is loaded, its static initializer <clinit>() executes.
 * The 'new Helper()' instruction triggers the JVM to implicitly call
 * ClassLoader.loadClass("com.example.Helper") before instantiation.
 *
 * Expected missed edge:
 *   com.example.App.<clinit>() -> java.lang.ClassLoader.loadClass(String)
 */
public class App {

    /**
     * Static field initialized with a new object allocation.
     * This triggers implicit class loading in <clinit>().
     */
    static Object helper = new Helper();

    public static void main(String[] args) {
        // Simply loading this class triggers the static initializer,
        // which in turn triggers implicit ClassLoader.loadClass for Helper
        System.out.println("App loaded, helper created: " + helper);
    }
}
