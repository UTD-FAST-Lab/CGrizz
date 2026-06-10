package edu.utd.fastlab.dcnr;

import java.io.IOException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Runtime registry for native method instantiation pattern.
 *
 * Root Cause (RC):
 *   Native method call site (e.g., FileChannel.map()) that returns an object whose concrete type
 *   differs from the declared return type (e.g., DirectByteBuffer instead of MappedByteBuffer).
 *
 * Symptom (Sym):
 *   Virtual/interface calls on the object returned from the native method.
 */
public final class DcnrTracker {

    private DcnrTracker() {}

    public static final ConcurrentHashMap<String, RootCauseEntry> RESULTS =
            new ConcurrentHashMap<String, RootCauseEntry>();

    // Maps object identity hash codes to their RC keys
    private static final ConcurrentHashMap<Integer, String> OBJECT_TO_RC =
            new ConcurrentHashMap<Integer, String>();

    // -------------------------------------------------------------------------
    // Called after native method returns to register the returned object.
    // -------------------------------------------------------------------------
    public static void onNativeMethodReturn(
            Object returnedObject,
            String callerMethod, int pc) {

        if (returnedObject == null) return;

        // RC key: the native method call site
        String rcKey = "RC:" + callerMethod + "@" + pc;

        // Ensure the RC entry exists
        RESULTS.putIfAbsent(rcKey, new RootCauseEntry(callerMethod, pc));

        // Register the returned object's identity with this RC
        OBJECT_TO_RC.put(System.identityHashCode(returnedObject), rcKey);
    }

    // -------------------------------------------------------------------------
    // Called before virtual/interface calls to check if receiver came from ThreadLocal.get().
    // -------------------------------------------------------------------------
    public static void onVirtualCall(
            Object receiver,
            String callerMethod, int pc,
            String methodName, String methodDesc) {

        if (receiver == null) return;

        // Check if this object was returned from a ThreadLocal.get() we tracked
        String rcKey = OBJECT_TO_RC.get(System.identityHashCode(receiver));
        if (rcKey == null) return;

        // Resolve the actual declaring class of the method by walking the hierarchy
        String actualCallee = resolveDeclaringClass(receiver.getClass(), methodName, methodDesc);

        // Record the symptom: the actual dispatched method
        RootCauseEntry rc = RESULTS.get(rcKey);
        if (rc != null) {
            rc.symptoms.add(new Symptom(callerMethod, pc, actualCallee));
        }
    }

    /**
     * Walk the receiver's class hierarchy to find the class that actually declares the method.
     * This mirrors how call graphs represent edges — inherited methods are attributed to their
     * declaring class, not the receiver's class.
     */
    private static String resolveDeclaringClass(Class<?> receiverClass, String methodName, String methodDesc) {
        // Convert method descriptor to parameter types
        Class<?>[] paramTypes = parseDescriptor(methodDesc);

        // Walk up the hierarchy to find the declaring class
        for (Class<?> c = receiverClass; c != null; c = c.getSuperclass()) {
            try {
                Method m = c.getDeclaredMethod(methodName, paramTypes);
                // Found the declaring class
                return c.getName() + "." + methodName + methodDesc;
            } catch (NoSuchMethodException e) {
                // Not declared in this class, continue up
            }
        }

        // Check interfaces
        Class<?> declaringInterface = findInInterfaces(receiverClass, methodName, paramTypes);
        if (declaringInterface != null) {
            return declaringInterface.getName() + "." + methodName + methodDesc;
        }

        // Fallback: use receiver's class (shouldn't happen for valid method calls)
        return receiverClass.getName() + "." + methodName + methodDesc;
    }

    private static Class<?> findInInterfaces(Class<?> cls, String methodName, Class<?>[] paramTypes) {
        for (Class<?> iface : cls.getInterfaces()) {
            try {
                iface.getDeclaredMethod(methodName, paramTypes);
                return iface;
            } catch (NoSuchMethodException e) {
                // Try super-interfaces
                Class<?> found = findInInterfaces(iface, methodName, paramTypes);
                if (found != null) return found;
            }
        }
        return null;
    }

    /**
     * Parse method descriptor to extract parameter types.
     * Simplified parser for common types.
     */
    private static Class<?>[] parseDescriptor(String descriptor) {
        // Extract parameter part: (params)return -> params
        int endIdx = descriptor.indexOf(')');
        if (endIdx < 1) return new Class<?>[0];

        String params = descriptor.substring(1, endIdx);
        if (params.isEmpty()) return new Class<?>[0];

        java.util.ArrayList<Class<?>> types = new java.util.ArrayList<Class<?>>();
        int i = 0;
        while (i < params.length()) {
            char c = params.charAt(i);
            switch (c) {
                case 'I': types.add(int.class); i++; break;
                case 'J': types.add(long.class); i++; break;
                case 'F': types.add(float.class); i++; break;
                case 'D': types.add(double.class); i++; break;
                case 'Z': types.add(boolean.class); i++; break;
                case 'B': types.add(byte.class); i++; break;
                case 'C': types.add(char.class); i++; break;
                case 'S': types.add(short.class); i++; break;
                case 'L':
                    int semi = params.indexOf(';', i);
                    String className = params.substring(i + 1, semi).replace('/', '.');
                    try {
                        types.add(Class.forName(className));
                    } catch (ClassNotFoundException e) {
                        types.add(Object.class);
                    }
                    i = semi + 1;
                    break;
                case '[':
                    // Array: skip for now, treat as Object
                    types.add(Object.class);
                    i++;
                    while (i < params.length() && params.charAt(i) == '[') i++;
                    if (i < params.length()) {
                        if (params.charAt(i) == 'L') {
                            i = params.indexOf(';', i) + 1;
                        } else {
                            i++;
                        }
                    }
                    break;
                default:
                    i++;
            }
        }
        return types.toArray(new Class<?>[0]);
    }

    // -------------------------------------------------------------------------
    // JSON output on JVM shutdown
    // -------------------------------------------------------------------------
    public static void dumpJson(String outputPath) {
        StringBuilder sb = new StringBuilder("{\n");
        boolean firstRc = true;
        for (Map.Entry<String, RootCauseEntry> e : RESULTS.entrySet()) {
            if (!firstRc) sb.append(",\n");
            firstRc = false;
            RootCauseEntry rc = e.getValue();
            sb.append("  ").append(jsonStr(e.getKey())).append(": {\n");
            sb.append("    \"method\": ").append(jsonStr(rc.method)).append(",\n");
            sb.append("    \"pc\": ").append(rc.pc).append(",\n");
            sb.append("    \"symptoms\": [");
            boolean firstSym = true;
            for (Symptom s : rc.symptoms) {
                if (!firstSym) sb.append(", ");
                firstSym = false;
                sb.append("\n      {");
                sb.append("\"caller\": ").append(jsonStr(s.caller)).append(", ");
                sb.append("\"callee\": ").append(jsonStr(s.callee)).append(", ");
                sb.append("\"pc\": ").append(s.pc).append("}");
            }
            if (!rc.symptoms.isEmpty()) sb.append("\n    ");
            sb.append("]\n  }");
        }
        sb.append("\n}\n");
        try {
            Files.write(Paths.get(outputPath),
                    sb.toString().getBytes(StandardCharsets.UTF_8));
            System.err.println("[NativeMethod] results written to " + outputPath);
        } catch (IOException ex) {
            System.err.println("[NativeMethod] failed to write results: " + ex.getMessage());
        }
    }

    private static String jsonStr(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    // -------------------------------------------------------------------------
    // Data classes (Java 8 compatible — no records)
    // -------------------------------------------------------------------------

    public static final class RootCauseEntry {
        public final String method;
        public final int    pc;
        public final List<Symptom> symptoms = new CopyOnWriteArrayList<Symptom>();

        RootCauseEntry(String method, int pc) {
            this.method = method;
            this.pc     = pc;
        }
    }

    public static final class Symptom {
        public final String caller;
        public final int    pc;
        public final String callee;

        Symptom(String caller, int pc, String callee) {
            this.caller = caller;
            this.pc     = pc;
            this.callee = callee;
        }
    }
}
