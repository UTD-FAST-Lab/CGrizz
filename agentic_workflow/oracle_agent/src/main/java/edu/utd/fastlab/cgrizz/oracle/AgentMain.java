package edu.utd.fastlab.cgrizz.oracle;

import java.lang.instrument.Instrumentation;

/** Loaded from the bootstrap class path by {@link OracleAgent}. */
public final class AgentMain {

    private AgentMain() {}

    public static void start(String args, Instrumentation inst) {
        String specPath = args != null && !args.trim().isEmpty() ? args.trim() : System.getProperty("cgrizz.oracle.spec");
        String out = System.getProperty("cgrizz.oracle.out", "oracle.json");
        boolean verbose = Boolean.getBoolean("cgrizz.oracle.verbose");

        Spec spec = specPath == null ? new Spec() : Spec.load(specPath);
        if (specPath == null) spec.errors.add("no spec given: use -javaagent:<jar>=<oracle_spec.json>");
        Tracker.init(spec, out, verbose);

        if (spec.usable()) {
            inst.addTransformer(new OracleTransformer(spec), false);
            if (spec.hasInsnRootCauses()) installUncaughtHandler();
        } else if (spec.unsupported != null) {
            System.err.println("[cgrizz-oracle] spec marked unsupported: " + spec.unsupported);
        } else {
            for (String e : spec.errors) System.err.println("[cgrizz-oracle] spec error: " + e);
            System.err.println("[cgrizz-oracle] instrumentation disabled because the spec is invalid");
        }

        Runtime.getRuntime().addShutdownHook(new Thread(() -> Tracker.dump("shutdown"), "cgrizz-oracle-dump"));

        long flushSeconds = Long.getLong("cgrizz.oracle.flushSeconds", 15L);
        if (flushSeconds > 0) {
            Thread flusher = new Thread(() -> {
                while (true) {
                    try {
                        Thread.sleep(flushSeconds * 1000);
                    } catch (InterruptedException e) {
                        return;
                    }
                    Tracker.dump("periodic");
                }
            }, "cgrizz-oracle-flush");
            flusher.setDaemon(true);
            flusher.start();
        }
    }

    /** Exceptions that escape every application handler still reach the oracle. */
    private static void installUncaughtHandler() {
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, t) -> {
            Tracker.caught(t);
            if (previous != null) {
                previous.uncaughtException(thread, t);
            } else {
                System.err.print("Exception in thread \"" + thread.getName() + "\" ");
                t.printStackTrace(System.err);
            }
        });
    }
}
