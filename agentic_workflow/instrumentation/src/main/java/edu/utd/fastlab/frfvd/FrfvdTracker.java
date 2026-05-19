package edu.utd.fastlab.frfvd;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

/**
 * Runtime registry for the FinalReferenceFieldVirtualDispatch (FRFVD) pattern.
 *
 * Thread-local state links a GETSTATIC of a public-static-final reference field (RC)
 * to the virtual dispatch that uses its result as receiver (Sym).
 * Object identity is used to match the exact value across the def-use edge.
 */
public final class FrfvdTracker {

    private FrfvdTracker() {}

    private static final ThreadLocal<String> TL_RC_KEY = new ThreadLocal<String>();
    private static final ThreadLocal<Object> TL_RC_OBJ = new ThreadLocal<Object>();

    public static final ConcurrentHashMap<String, RootCauseEntry> RESULTS =
            new ConcurrentHashMap<String, RootCauseEntry>();

    /**
     * Called immediately after GETSTATIC at an RC site.
     * {@code value} is a DUP'd copy of the object that was loaded.
     */
    public static void onFinalFieldLoad(
            Object value,
            String method, int pc,
            String fieldOwner, String fieldName, String fieldDesc) {

        String key = "RC:" + method + "@" + pc;
        if (!RESULTS.containsKey(key)) {
            RESULTS.putIfAbsent(key,
                    new RootCauseEntry(method, pc,
                            fieldOwner + "." + fieldName + ":" + fieldDesc));
        }
        TL_RC_KEY.set(key);
        TL_RC_OBJ.set(value);
    }

    /**
     * Called just before a matched INVOKEVIRTUAL.
     * {@code receiver} is a DUP'd reference to the dispatch receiver.
     * Uses object identity to confirm it is the same value loaded at the RC site.
     */
    public static void onVirtualDispatch(
            Object receiver,
            String callerMethod, int pc,
            String calleeName, String calleeDesc) {

        String rcKey = TL_RC_KEY.get();
        if (rcKey == null) return;

        Object expected = TL_RC_OBJ.get();
        if (expected != receiver) return;

        String actualCallee = receiver.getClass().getName().replace('.', '/')
                + "." + calleeName + ":" + calleeDesc;

        RootCauseEntry rc = RESULTS.get(rcKey);
        if (rc != null) {
            rc.symptoms.add(new Symptom(callerMethod, pc, actualCallee));
        }

        TL_RC_OBJ.remove();
        TL_RC_KEY.remove();
    }

    // --- Shutdown output ---

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
            sb.append("    \"field\": ").append(jsonStr(rc.field)).append(",\n");
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
            System.err.println("[FRFVD] results written to " + outputPath);
        } catch (IOException ex) {
            System.err.println("[FRFVD] failed to write results: " + ex.getMessage());
        }
    }

    private static String jsonStr(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    // --- Data classes ---

    public static final class RootCauseEntry {
        public final String method;
        public final int pc;
        public final String field;
        public final List<Symptom> symptoms = new CopyOnWriteArrayList<Symptom>();

        RootCauseEntry(String method, int pc, String field) {
            this.method = method;
            this.pc = pc;
            this.field = field;
        }
    }

    public static final class Symptom {
        public final String caller;
        public final int pc;
        public final String callee;

        Symptom(String caller, int pc, String callee) {
            this.caller = caller;
            this.pc = pc;
            this.callee = callee;
        }
    }
}
