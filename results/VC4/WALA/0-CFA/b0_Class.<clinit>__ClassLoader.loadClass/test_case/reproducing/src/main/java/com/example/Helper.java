package com.example;

/**
 * Helper class that gets loaded implicitly via ClassLoader.loadClass()
 * when App's static initializer executes 'new Helper()'.
 */
public class Helper {
    @Override
    public String toString() {
        return "Helper instance";
    }
}
