import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

import soot.G;
import soot.PackManager;
import soot.Scene;
import soot.jimple.toolkits.callgraph.Edge;
import soot.options.Options;

/**
 * Runs SPARK the way JCG's SootJCGAdapter does (0-CFA-like SPARK: rta/vta off,
 * simulate-natives on) and writes the call graph as sorted "caller -> callee @line"
 * lines. JDK classes are loaded on demand from rt.jar instead of all being processed.
 *
 * Usage: SparkDriver <program dir or jar> <main class> <rt.jar> <output edges file>
 */
public class SparkDriver {
  public static void main(String[] a) throws Exception {
    G.reset();
    Options o = Options.v();
    o.set_whole_program(true);
    o.set_keep_line_number(true);
    o.set_allow_phantom_refs(true);
    o.set_process_dir(Collections.singletonList(a[0]));
    o.set_soot_classpath(a[0] + java.io.File.pathSeparator + a[2]);
    o.set_output_format(Options.output_format_none);
    // Soot creates its output directory even without output; keep it out of the working directory.
    o.set_output_dir(java.nio.file.Files.createTempDirectory("sootOutput").toString());
    o.setPhaseOption("jb", "use-original-names:true");
    o.setPhaseOption("cg", "safe-forname:false");
    o.setPhaseOption("cg", "safe-newinstance:false");
    o.set_main_class(a[1]);
    o.setPhaseOption("cg.spark", "enabled:true");
    o.setPhaseOption("cg.spark", "rta:false");
    o.setPhaseOption("cg.spark", "vta:false");
    o.setPhaseOption("cg.spark", "simulate-natives:true");
    Scene.v().loadNecessaryClasses();
    PackManager.v().runPacks();
    List<String> edges = new ArrayList<>();
    for (Iterator<Edge> it = Scene.v().getCallGraph().iterator(); it.hasNext(); ) {
      Edge e = it.next();
      int line = e.srcStmt() == null ? -1 : e.srcStmt().getJavaSourceStartLineNumber();
      edges.add(e.src().getSignature() + " -> " + e.tgt().getSignature() + " @" + line);
    }
    Collections.sort(edges);
    try (PrintWriter w = new PrintWriter(a[3])) {
      for (String e : edges) w.println(e);
    }
    System.err.println("[driver] " + edges.size() + " edges");
  }
}
