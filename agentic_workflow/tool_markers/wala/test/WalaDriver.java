import com.ibm.wala.classLoader.BinaryDirectoryTreeModule;
import com.ibm.wala.classLoader.CallSiteReference;
import com.ibm.wala.classLoader.Language;
import com.ibm.wala.ipa.callgraph.AnalysisCacheImpl;
import com.ibm.wala.ipa.callgraph.AnalysisOptions;
import com.ibm.wala.ipa.callgraph.AnalysisScope;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.CallGraphBuilder;
import com.ibm.wala.ipa.callgraph.Entrypoint;
import com.ibm.wala.ipa.callgraph.impl.Util;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.cha.ClassHierarchyFactory;
import com.ibm.wala.ipa.cha.IClassHierarchy;
import com.ibm.wala.types.ClassLoaderReference;
import com.ibm.wala.util.config.FileOfClasses;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;
import java.util.jar.JarFile;

/**
 * Runs WALA 0-CFA the way JCG's WalaJCGAdapter does (makeZeroCFABuilder, ReflectionOptions.FULL,
 * main-class entrypoints) and writes the call graph as sorted "caller @pc -> callee" lines (pc
 * "synthetic" for WALA's fake root). Only the GUI packages of the JDK are excluded.
 *
 * <p>Usage: WalaDriver &lt;class dir&gt; &lt;main class&gt; &lt;rt.jar&gt; &lt;output edges file&gt;
 */
public class WalaDriver {
  public static void main(String[] a) throws Exception {
    AnalysisScope scope = AnalysisScope.createJavaAnalysisScope();
    scope.addToScope(ClassLoaderReference.Primordial, new JarFile(a[2]));
    scope.addToScope(ClassLoaderReference.Application, new BinaryDirectoryTreeModule(new File(a[0])));
    String exclusions = "java\\/awt\\/.*\njavax\\/swing\\/.*\nsun\\/awt\\/.*\nsun\\/swing\\/.*\ncom\\/sun\\/java\\/swing\\/.*\n";
    scope.setExclusions(new FileOfClasses(new ByteArrayInputStream(exclusions.getBytes(StandardCharsets.UTF_8))));
    IClassHierarchy cha = ClassHierarchyFactory.make(scope);
    Iterable<Entrypoint> entrypoints = Util.makeMainEntrypoints(scope, cha, "L" + a[1].replace('.', '/'));
    AnalysisOptions options = new AnalysisOptions(scope, entrypoints);
    options.setReflectionOptions(AnalysisOptions.ReflectionOptions.FULL);
    CallGraphBuilder<InstanceKey> builder =
        Util.makeZeroCFABuilder(Language.JAVA, options, new AnalysisCacheImpl(), cha, scope);
    CallGraph cg = builder.makeCallGraph(options, null);
    List<String> edges = new ArrayList<>();
    for (CGNode n : cg) {
      for (Iterator<CallSiteReference> sites = n.iterateCallSites(); sites.hasNext(); ) {
        CallSiteReference site = sites.next();
        // WALA's synthetic root numbers its call sites in discovery order: no bytecode position.
        String at = n.getMethod().getDeclaringClass().getName().toString().equals("Lcom/ibm/wala/FakeRootClass")
            ? "synthetic" : String.valueOf(site.getProgramCounter());
        for (CGNode t : cg.getPossibleTargets(n, site)) {
          edges.add(n.getMethod().getSignature() + " @" + at + " -> " + t.getMethod().getSignature());
        }
      }
    }
    Collections.sort(edges);
    try (PrintWriter w = new PrintWriter(a[3])) {
      for (String e : edges) w.println(e);
    }
    System.err.println("[driver] " + edges.size() + " edges");
  }
}
