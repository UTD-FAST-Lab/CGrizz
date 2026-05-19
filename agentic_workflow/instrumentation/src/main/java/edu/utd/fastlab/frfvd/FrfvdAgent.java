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
        final String outputPath = (agentArgs != null && agentArgs.length() > 0)
                ? agentArgs : "frfvd_results.json";

        inst.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(
                    final ClassLoader loader, String className,
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

                    // Override getCommonSuperClass to use the TRANSFORM'S class loader.
                    // The default implementation uses the ClassWriter's own loader (the
                    // agent loader), which cannot find application classes and may NPE
                    // when walking an unresolvable superclass chain.
                    ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_FRAMES) {
                        @Override
                        protected String getCommonSuperClass(String type1, String type2) {
                            // Pick the best available loader: transform loader → context → agent
                            ClassLoader cl = loader;
                            if (cl == null) cl = Thread.currentThread().getContextClassLoader();
                            if (cl == null) cl = FrfvdAgent.class.getClassLoader();
                            try {
                                Class<?> c1 = Class.forName(
                                        type1.replace('/', '.'), false, cl);
                                Class<?> c2 = Class.forName(
                                        type2.replace('/', '.'), false, cl);
                                if (c1.isAssignableFrom(c2)) return type1;
                                if (c2.isAssignableFrom(c1)) return type2;
                                if (c1.isInterface() || c2.isInterface())
                                    return "java/lang/Object";
                                do {
                                    c1 = c1.getSuperclass();
                                    if (c1 == null) return "java/lang/Object";
                                } while (!c1.isAssignableFrom(c2));
                                return c1.getName().replace('.', '/');
                            } catch (Exception e) {
                                // Fall back to Object — conservative but safe
                                return "java/lang/Object";
                            }
                        }
                    };

                    cr.accept(new FrfvdClassVisitor(cw, loader), ClassReader.EXPAND_FRAMES);
                    return cw.toByteArray();
                } catch (Exception e) {
                    System.err.println("[FRFVD] skipping " + className + ": " + e);
                    e.printStackTrace(System.err);
                    return null;
                }
            }
        });

        Runtime.getRuntime().addShutdownHook(
                new Thread(new Runnable() {
                    public void run() { FrfvdTracker.dumpJson(outputPath); }
                }, "frfvd-shutdown"));
    }
}
