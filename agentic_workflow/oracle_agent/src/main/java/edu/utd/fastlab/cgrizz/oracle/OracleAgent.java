package edu.utd.fastlab.cgrizz.oracle;

import java.io.File;
import java.lang.instrument.Instrumentation;
import java.util.jar.JarFile;

/**
 * Java-agent entry point.
 *
 * Usage:
 *   java -javaagent:cgrizz-oracle-agent.jar=&lt;oracle_spec.json&gt;
 *        -Dcgrizz.oracle.out=&lt;oracle.json&gt; -XX:-OmitStackTraceInFastThrow ...
 *
 * The agent jar is appended to the bootstrap class path first, so the Tracker
 * is visible from every class loader (including loaders that do not delegate to
 * the system loader). This class therefore only touches JDK classes and hands
 * off to {@link AgentMain}, loaded from the bootstrap copy.
 */
public final class OracleAgent {

    private OracleAgent() {}

    public static void premain(String args, Instrumentation inst) throws Exception {
        File jar = new File(OracleAgent.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        inst.appendToBootstrapClassLoaderSearch(new JarFile(jar));
        Class<?> main = Class.forName("edu.utd.fastlab.cgrizz.oracle.AgentMain", true, null);
        main.getMethod("start", String.class, Instrumentation.class).invoke(null, args, inst);
    }
}
