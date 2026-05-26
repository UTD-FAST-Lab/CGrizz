package edu.utd.fastlab.dcnr;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;

/**
 * Java agent entry point for the DynamicClassNameReflection (DCNR) pattern.
 *
 * Usage:
 *   java -javaagent:dcnr-agent.jar[=output.json] -cp ... MainClass
 *
 * Default output: dcnr_results.json in the working directory.
 */
public final class DcnrAgent {

    public static void premain(String agentArgs, Instrumentation inst) {
        final String outputPath = (agentArgs != null && !agentArgs.isEmpty())
                ? agentArgs : "dcnr_results.json";

        inst.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(ClassLoader loader, String className,
                                    Class<?> cls, ProtectionDomain pd, byte[] bytes) {
                if (className == null) return null;
                // Skip JDK internals and this agent to prevent infinite recursion
                if (className.startsWith("java/")
                        || className.startsWith("javax/")
                        || className.startsWith("sun/")
                        || className.startsWith("jdk/")
                        || className.startsWith("com/sun/")
                        || className.startsWith("edu/utd/fastlab/dcnr/")
                        || className.startsWith("org/objectweb/asm/")) {
                    return null;
                }
                try {
                    ClassReader cr = new ClassReader(bytes);

                    // Override getCommonSuperClass to use the transform's loader,
                    // not the agent loader, so application classes resolve correctly.
                    ClassWriter cw = new ClassWriter(cr, ClassWriter.COMPUTE_FRAMES) {
                        @Override
                        protected String getCommonSuperClass(String type1, String type2) {
                            ClassLoader cl = loader;
                            if (cl == null) cl = Thread.currentThread().getContextClassLoader();
                            if (cl == null) cl = DcnrAgent.class.getClassLoader();
                            try {
                                Class<?> c1 = Class.forName(type1.replace('/', '.'), false, cl);
                                Class<?> c2 = Class.forName(type2.replace('/', '.'), false, cl);
                                if (c1.isAssignableFrom(c2)) return type1;
                                if (c2.isAssignableFrom(c1)) return type2;
                                if (c1.isInterface() || c2.isInterface()) return "java/lang/Object";
                                do {
                                    c1 = c1.getSuperclass();
                                    if (c1 == null) return "java/lang/Object";
                                } while (!c1.isAssignableFrom(c2));
                                return c1.getName().replace('.', '/');
                            } catch (Exception e) {
                                return "java/lang/Object";
                            }
                        }
                    };

                    cr.accept(new DcnrClassVisitor(cw), ClassReader.EXPAND_FRAMES);
                    return cw.toByteArray();
                } catch (Exception e) {
                    System.err.println("[DCNR] skipping " + className + ": " + e);
                    e.printStackTrace(System.err);
                    return null;
                }
            }
        });

        Runtime.getRuntime().addShutdownHook(
                new Thread(new Runnable() {
                    public void run() { DcnrTracker.dumpJson(outputPath); }
                }, "dcnr-shutdown"));
    }
}
