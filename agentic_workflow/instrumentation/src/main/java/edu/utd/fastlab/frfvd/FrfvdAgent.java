package edu.utd.fastlab.frfvd;

import java.lang.instrument.*;
import java.security.ProtectionDomain;
import org.objectweb.asm.*;

/**
 * Java agent entry point.
 * Usage: java -javaagent:frfvd-agent.jar[=output.json] -cp ... MainClass
 * Default output path: frfvd_results.json in the working directory.
 */
public final class FrfvdAgent {

    public static void premain(String agentArgs, Instrumentation inst) {
        String outputPath = (agentArgs != null && !agentArgs.isEmpty())
                ? agentArgs : "frfvd_results.json";

        inst.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(
                    ClassLoader loader, String className,
                    Class<?> cls, ProtectionDomain pd, byte[] bytes) {

                if (className == null) return null;
                // Skip JDK internals and this agent itself to prevent infinite recursion
                if (className.startsWith("java/")
                        || className.startsWith("javax/")
                        || className.startsWith("sun/")
                        || className.startsWith("jdk/")
                        || className.startsWith("com/sun/")
                        || className.startsWith("edu/utd/fastlab/frfvd/")
                        || className.startsWith("org/objectweb/asm/")) {
                    return null;
                }

                try {
                    ClassReader cr = new ClassReader(bytes);
                    // COMPUTE_FRAMES required because we add locals and branches
                    ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_FRAMES);
                    cr.accept(new FrfvdClassVisitor(cw, loader), ClassReader.EXPAND_FRAMES);
                    return cw.toByteArray();
                } catch (Exception e) {
                    System.err.println("[FRFVD] skipping " + className + ": " + e);
                    return null;
                }
            }
        });

        Runtime.getRuntime().addShutdownHook(
                new Thread(() -> FrfvdTracker.dumpJson(outputPath), "frfvd-shutdown"));
    }
}
