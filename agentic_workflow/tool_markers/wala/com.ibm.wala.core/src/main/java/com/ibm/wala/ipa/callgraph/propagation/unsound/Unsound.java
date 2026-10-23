package com.ibm.wala.ipa.callgraph.propagation.unsound;

import com.ibm.wala.classLoader.CallSiteReference;
import com.ibm.wala.classLoader.IClass;
import com.ibm.wala.classLoader.IMethod;
import com.ibm.wala.classLoader.NewSiteReference;
import com.ibm.wala.ipa.callgraph.CGNode;
import com.ibm.wala.ipa.callgraph.CallGraph;
import com.ibm.wala.ipa.callgraph.propagation.InstanceKey;
import com.ibm.wala.ipa.callgraph.propagation.PointerKey;
import com.ibm.wala.ipa.callgraph.propagation.PropagationCallGraphBuilder;
import com.ibm.wala.ipa.callgraph.propagation.PropagationSystem;
import com.ibm.wala.types.TypeReference;
import com.ibm.wala.util.collections.EmptyIterator;
import com.ibm.wala.util.collections.Pair;
import com.ibm.wala.util.intset.IntSet;
import com.ibm.wala.util.intset.IntSetUtil;
import com.ibm.wala.util.intset.MutableIntSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/**
 * CGrizz unsoundness markers for WALA (issue #14).
 *
 * <p>Where WALA gives up on a value, the annotation calls {@link #mark}: that adds a marker
 * instance key to the value's points-to set. The marker's concrete type is java.lang.Object and
 * it is registered with every class, so type filters (casts, declared types) let it through;
 * {@link #onDispatch} gives it no call target, so the call graph does not change. When it reaches
 * the receiver of a call, one warning per call site is printed:
 *
 * <pre>
 * [UNSOUNDNESS-EDGE] &lt;description&gt; | caller=pkg.Cls.m(params) | site=&lt;callee&gt; | line=&lt;n&gt; | pc=&lt;n&gt; | rc=&lt;id&gt;
 * </pre>
 *
 * <p>pc and line are computed like JCG's WALA adapter (site.getProgramCounter(),
 * method.getLineNumber(pc)), so warnings match the boundaries.
 */
public final class Unsound {

  private Unsound() {}

  /** One marker instance key per root cause. */
  public static final class Marker implements InstanceKey {
    public final String id;
    public final String description;
    private final IClass type;

    Marker(String id, String description, IClass type) {
      this.id = id;
      this.description = description;
      this.type = type;
    }

    @Override
    public IClass getConcreteType() {
      return type;
    }

    @Override
    public Iterator<Pair<CGNode, NewSiteReference>> getCreationSites(CallGraph cg) {
      return EmptyIterator.instance();
    }

    @Override
    public boolean equals(Object o) {
      return o instanceof Marker && ((Marker) o).id.equals(id);
    }

    @Override
    public int hashCode() {
      return id.hashCode() * 31 + 7;
    }

    @Override
    public String toString() {
      return "UnsoundMarker(" + id + ")";
    }
  }

  private static final Map<String, Marker> MARKERS = new HashMap<>();
  private static final MutableIntSet INDICES = IntSetUtil.getDefaultIntSetFactory().make();
  private static final Set<String> REPORTED = new HashSet<>();
  /** CGRIZZ_MARKER_SCOPE=application: warn only at calls outside the JDK. */
  private static final boolean APPLICATION_ONLY = "application".equals(System.getenv("CGRIZZ_MARKER_SCOPE"));
  private static final String[] JDK_PACKAGES = {"java.", "javax.", "jdk.", "sun.", "com.sun.", "com.oracle.", "org.ietf.jgss.", "org.omg.", "org.w3c.dom.", "org.xml.sax.", "org.jcp.xml."};

  /** className: dotted; WALA's lambda classes (wala.lambda$java$net$X$0) count as their enclosing class. */
  static boolean isJdk(String className) {
    String c = className.startsWith("wala.lambda$")
        ? className.substring("wala.lambda$".length()).replace('$', '.')
        : className;
    for (String p : JDK_PACKAGES) {
      if (c.startsWith(p)) {
        return true;
      }
    }
    return false;
  }

  /**
   * The value of local {@code valueNumber} in {@code node} is lost by WALA because of root cause
   * {@code id}. {@code description}: at most 60 characters, for the tool's user.
   */
  public static void mark(
      PropagationCallGraphBuilder builder,
      CGNode node,
      int valueNumber,
      String id,
      String description) {
    mark(builder, builder.getPointerKeyForLocal(node, valueNumber), id, description);
  }

  /** Same, for any pointer key (an instance field, a static field, an array contents key, ...). */
  public static void mark(
      PropagationCallGraphBuilder builder, PointerKey key, String id, String description) {
    PropagationSystem system = builder.getPropagationSystem();
    Marker m =
        MARKERS.computeIfAbsent(
            id, k -> new Marker(k, description, builder.getClassHierarchy().getRootClass()));
    system.newConstraint(key, m);
    int index = system.findOrCreateIndexForInstanceKey(m);
    if (INDICES.add(index)) {
      system.cgrizzRegisterMarker(index);
    }
  }

  public static boolean hasMarkers() {
    return !INDICES.isEmpty();
  }

  public static IntSet markerIndices() {
    return INDICES;
  }

  /**
   * Called first in PropagationCallGraphBuilder.getTargetForCall. A marker never gets a target;
   * a marker receiver also prints the warning. Returns true if the call must be skipped.
   */
  public static boolean onDispatch(CGNode caller, CallSiteReference site, InstanceKey[] keys) {
    if (keys == null || INDICES.isEmpty()) {
      return false;
    }
    boolean skip = false;
    for (int i = 0; i < keys.length; i++) {
      if (keys[i] instanceof Marker) {
        skip = true;
        if (i == 0) {
          report((Marker) keys[i], caller, site);
        }
      }
    }
    return skip;
  }

  private static void report(Marker m, CGNode caller, CallSiteReference site) {
    IMethod method = caller.getMethod();
    if (APPLICATION_ONLY && isJdk(method.getDeclaringClass().getName().toString().substring(1).replace('/', '.'))) {
      return;
    }
    int pc = site.getProgramCounter();
    String callerName = name(method);
    if (!REPORTED.add(m.id + "|" + callerName + "|" + pc)) {
      return;
    }
    int line;
    try {
      line = method.getLineNumber(pc);
    } catch (RuntimeException e) {
      line = -1;
    }
    System.out.println(
        "[UNSOUNDNESS-EDGE] "
            + m.description
            + " | caller="
            + callerName
            + " | site="
            + type(site.getDeclaredTarget().getDeclaringClass())
            + "."
            + site.getDeclaredTarget().getName()
            + " | line="
            + line
            + " | pc="
            + pc
            + " | rc="
            + m.id);
  }

  /** pkg.Class.method(param, types) — the format the CGrizz checks parse. */
  static String name(IMethod m) {
    StringBuilder b =
        new StringBuilder(type(m.getDeclaringClass().getReference()))
            .append('.')
            .append(m.getName())
            .append('(');
    int first = m.isStatic() ? 0 : 1;
    for (int i = first; i < m.getNumberOfParameters(); i++) {
      b.append(i == first ? "" : ",").append(type(m.getParameterType(i)));
    }
    return b.append(')').toString();
  }

  static String type(TypeReference t) {
    if (t.isArrayType()) {
      return type(t.getArrayElementType()) + "[]";
    }
    if (t.isPrimitiveType()) {
      return t.getName().toString();
    }
    String n = t.getName().toString();
    return (n.startsWith("L") ? n.substring(1) : n).replace('/', '.');
  }
}
