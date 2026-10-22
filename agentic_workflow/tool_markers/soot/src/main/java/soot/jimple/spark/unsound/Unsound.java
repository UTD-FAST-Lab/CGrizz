package soot.jimple.spark.unsound;

import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import soot.Local;
import soot.NullType;
import soot.SootMethod;
import soot.Type;
import soot.jimple.Stmt;
import soot.jimple.spark.pag.AllocNode;
import soot.jimple.spark.pag.FieldRefNode;
import soot.jimple.spark.pag.Node;
import soot.jimple.spark.pag.PAG;
import soot.jimple.spark.pag.VarNode;
import soot.jimple.toolkits.callgraph.OnFlyCallGraphBuilder;
import soot.jimple.toolkits.callgraph.VirtualCallSite;
import soot.tagkit.BytecodeOffsetTag;

/**
 * CGrizz unsoundness markers for SPARK (issue #14).
 *
 * At the point where SPARK gives up on a value (an unmodelled native, a reflective
 * allocation it cannot resolve, ...) the annotation calls {@link #mark}. That adds a
 * marker object to the value's points-to set. The marker has type {@link NullType}, so
 * SPARK's type filters let it through every cast and declared type, it flows wherever
 * the lost object would have flowed, and call resolution gives it no targets — the call
 * graph does not change. When it reaches the receiver of a call, {@link #onReceiver}
 * prints one warning per call site:
 *
 * <pre>
 * [UNSOUNDNESS-EDGE] &lt;description&gt; | caller=pkg.Cls.m(params) | site=&lt;callee&gt; | line=&lt;n&gt; | pc=&lt;n&gt; | rc=&lt;id&gt;
 * </pre>
 */
public final class Unsound {

  private Unsound() {
  }

  /** Allocation key of a marker: one marker node per root cause. */
  public static final class Marker {
    public final String id;
    public final String description;

    Marker(String id, String description) {
      this.id = id;
      this.description = description;
    }

    @Override
    public boolean equals(Object o) {
      return o instanceof Marker && ((Marker) o).id.equals(id);
    }

    @Override
    public int hashCode() {
      return Objects.hash("CGrizzUnsoundMarker", id);
    }

    @Override
    public String toString() {
      return "UnsoundMarker(" + id + ")";
    }
  }

  private static final Set<String> REPORTED = ConcurrentHashMap.newKeySet();
  private static final Map<String, Marker> MARKERS = new ConcurrentHashMap<>();

  /**
   * The value of {@code var} (a SPARK VarNode or FieldRefNode, e.g. a native method's
   * return variable) is lost by SPARK because of root cause {@code id}.
   *
   * @param var         a ReferenceVariable / Node of the PAG
   * @param container   the method the value belongs to (may be null)
   * @param id          a stable root-cause id
   * @param description at most 60 characters, for the tool's user
   */
  public static void mark(Object var, SootMethod container, String id, String description) {
    if (!(var instanceof Node)) {
      return;
    }
    Node target = (Node) var;
    PAG pag = target.getPag();
    Marker key = MARKERS.computeIfAbsent(id, k -> new Marker(k, description));
    AllocNode marker = pag.makeAllocNode(key, NullType.v(), container);
    if (target instanceof VarNode) {
      pag.addEdge(marker, target);
    } else if (target instanceof FieldRefNode) {
      VarNode holder = pag.makeGlobalVarNode(marker, NullType.v());
      pag.addEdge(marker, holder);
      pag.addEdge(holder, target);
    }
  }

  public static boolean isMarker(Node n) {
    return n instanceof AllocNode && ((AllocNode) n).getNewExpr() instanceof Marker;
  }

  /** Called by OnFlyCallGraph for every object that reaches a receiver variable. */
  public static void onReceiver(OnFlyCallGraphBuilder ofcgb, Local receiver, AllocNode object) {
    if (!(object.getNewExpr() instanceof Marker)) {
      return;
    }
    Marker m = (Marker) object.getNewExpr();
    List<VirtualCallSite> sites = ofcgb.cgrizzSitesOf(receiver);
    if (sites == null) {
      return;
    }
    for (VirtualCallSite site : sites) {
      Stmt s = site.stmt();
      if (!REPORTED.add(m.id + "|" + System.identityHashCode(s))) {
        continue;
      }
      System.out.println("[UNSOUNDNESS-EDGE] " + m.description + " | caller=" + method(site.container()) + " | site="
          + site.iie().getMethodRef().getDeclaringClass().getName() + "." + site.iie().getMethodRef().getName()
          + " | line=" + s.getJavaSourceStartLineNumber() + " | pc=" + pc(s) + " | rc=" + m.id);
    }
  }

  private static int pc(Stmt s) {
    BytecodeOffsetTag t = (BytecodeOffsetTag) s.getTag(BytecodeOffsetTag.NAME);
    return t == null ? -1 : t.getBytecodeOffset();
  }

  /** pkg.Class.method(param, types) — the format the CGrizz checks parse. */
  static String method(SootMethod m) {
    StringBuilder b = new StringBuilder(m.getDeclaringClass().getName()).append('.').append(m.getName()).append('(');
    List<Type> params = m.getParameterTypes();
    for (int i = 0; i < params.size(); i++) {
      b.append(i == 0 ? "" : ",").append(params.get(i));
    }
    return b.append(')').toString();
  }
}
