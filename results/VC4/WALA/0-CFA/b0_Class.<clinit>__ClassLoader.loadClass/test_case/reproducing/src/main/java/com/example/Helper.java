package com.example;

/**
 * Helper class that gets loaded implicitly by the JVM when 'new Helper()'
 * is executed in App's static initializer.
 *
 * The JVM will call ClassLoader.loadClass("com.example.Helper") internally
 * before this class can be instantiated.
 */
public class Helper {

    public Helper() {
        // Simple constructor
    }

    @Override
    public String toString() {
        return "Helper instance";
    }
}
