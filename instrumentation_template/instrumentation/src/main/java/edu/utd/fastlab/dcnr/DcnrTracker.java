package edu.utd.fastlab.dcnr;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Runtime registry for the DynamicClassNameReflection (DCNR) pattern.
 *
 * Root Cause (RC): Class.forName(s) where s is not a compile-time constant —
 *   e.g., computed via string concatenation or Map.get().
 *
 * Symptom (Sym): INVOKEVIRTUAL / INVOKEINTERFACE whose receiver's runtime class
 *   is (or is an inner class of) a class loaded at an RC site.
 */
public final class DcnrTracker {

    private DcnrTracker() {}

    /**
     * Maps internal class name (e.g. "sun/nio/cs/UTF_8") → RC key for every
     * class loaded via a dynamic Class.forName call.
     */
    private static final ConcurrentHashMap<String, String> DYNAMIC_CLASS_TO_RC =
            new ConcurrentHashMap<String, String>();

    public static final ConcurrentHashMap<String, RootCauseEntry> RESULTS =
            new ConcurrentHashMap<String, RootCauseEntry>();

    // -------------------------------------------------------------------------
    // Called immediately after a dynamic Class.forName() returns.
    // loadedClass: the Class object returned by forName (may be null on exception path,
    //              but we only inject before the return so it should be non-null).
    // -------------------------------------------------------------------------
    public static void onDynamicClassLoad(
            Class<?> loadedClass,
            String callerMethod, int pc) {

        if (loadedClass == null) return;

        String key = "RC:" + callerMethod + "@" + pc;
        String internalName = loadedClass.getName().replace('.', '/');

        RESULTS.putIfAbsent(key, new RootCauseEntry(callerMethod, pc, internalName));
        DYNAMIC_CLASS_TO_RC.put(internalName, key);
    }

    // -------------------------------------------------------------------------
    // Called just before INVOKEVIRTUAL / INVOKEINTERFACE.
    // Checks whether the receiver's runtime class was (directly or as an inner
    // class) loaded via a dynamic Class.forName — if so, records the symptom.
    // -------------------------------------------------------------------------
    public static void onVirtualDispatch(
            Object receiver,
            String callerMethod, int pc,
            String calleeName, String calleeDesc) {

        if (receiver == null) return;

        String rcKey = findRcKey(receiver.getClass());
        if (rcKey == null) return;

        String actualCallee = receiver.getClass().getName().replace('.', '/')
                + "." + calleeName + ":" + calleeDesc;

        RootCauseEntry rc = RESULTS.get(rcKey);
        if (rc != null) {
            rc.symptoms.add(new Symptom(callerMethod, pc, actualCallee));
        }
    }

    /**
     * Walk the receiver's class hierarchy (supertypes + declaring classes) to
     * find a match with any class loaded at a dynamic forName site.
     *
     * This covers the common case where the SA-missed class (e.g. UTF_8) is the
     * *declaring* class of the actual runtime receiver (e.g. UTF_8$Encoder).
     */
    private static String findRcKey(Class<?> cls) {
        for (Class<?> c = cls; c != null; c = c.getSuperclass()) {
            String name = c.getName().replace('.', '/');
            String key = DYNAMIC_CLASS_TO_RC.get(name);
            if (key != null) return key;

            // Check declaring class — handles inner-class receivers like UTF_8$Encoder
            Class<?> declaring = c.getDeclaringClass();
            if (declaring != null) {
                String dname = declaring.getName().replace('.', '/');
                key = DYNAMIC_CLASS_TO_RC.get(dname);
                if (key != null) return key;
            }
        }
        // Also check directly implemented interfaces of the receiver
        for (Class<?> iface : cls.getInterfaces()) {
            String name = iface.getName().replace('.', '/');
            String key = DYNAMIC_CLASS_TO_RC.get(name);
            if (key != null) return key;
        }
        return null;
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
            sb.append("    \"loadedClass\": ").append(jsonStr(rc.loadedClass)).append(",\n");
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
            System.err.println("[DCNR] results written to " + outputPath);
        } catch (IOException ex) {
            System.err.println("[DCNR] failed to write results: " + ex.getMessage());
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
        public final String loadedClass;
        public final List<Symptom> symptoms = new CopyOnWriteArrayList<Symptom>();

        RootCauseEntry(String method, int pc, String loadedClass) {
            this.method      = method;
            this.pc          = pc;
            this.loadedClass = loadedClass;
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
