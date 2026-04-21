package com.example;

/**
 * Minimal test case demonstrating implicit ClassLoader.loadClass() calls
 * during static initialization with 'new' instructions.
 *
 * When App's <clinit> is executed, the JVM implicitly calls ClassLoader.loadClass()
 * to load the Helper class before executing 'new Helper()'.
 *
 * WALA misses the edge: App.<clinit>() -> ClassLoader.loadClass()
 */
public class App {
    // Static field initialization triggers <clinit> and contains 'new' instruction
    public static Helper instance = new Helper();

    public static void main(String[] args) {
        System.out.println("Helper instance: " + instance);
    }
}
