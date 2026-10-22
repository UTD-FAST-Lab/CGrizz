package edu.utd.fastlab.cgrizz.oracle;

import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Runtime side of the oracle. Injected bytecode calls the public static hooks
 * below; they must never throw into the program, so every hook swallows
 * Throwable.
 *
 * A root-cause site tags the value it produces (object identity). A symptom is
 * a call site whose receiver is tagged (or whose receiver's class, or the
 * caller's own class, is a tagged Class object); it is linked to the root-cause
 * site that produced the tag.
 */
public final class Tracker {

    private Tracker() {}

    static final class Resolved {
        final String owner;
        final boolean bridge;

        Resolved(String owner, boolean bridge) {
            this.owner = owner;
            this.bridge = bridge;
        }
    }

    private static Spec spec;
    private static String outPath;
    private static boolean verbose;

    private static final TagMap TAGS = new TagMap();
    private static final TagMap SEEN_EXCEPTIONS = new TagMap();
    private static final Map<String, Integer> TAGGED_CLASS_NAMES = new ConcurrentHashMap<>();

    /** rc site id → symptom key → symptom entry */
    private static final Map<Integer, Map<String, Map<String, Object>>> SYMPTOMS = new ConcurrentHashMap<>();
    private static final Set<Integer> HIT_SITES = ConcurrentHashMap.newKeySet();
    private static final Map<Integer, AtomicLong> RULE_HITS = new ConcurrentHashMap<>();  // rule index → hits

    private static final ClassValue<Map<String, Resolved>> RESOLVED = new ClassValue<Map<String, Resolved>>() {
        @Override
        protected Map<String, Resolved> computeValue(Class<?> type) {
            return new ConcurrentHashMap<>();
        }
    };

    // Statistics reported in <out>.stats.json
    static final AtomicLong CLASSES_INSTRUMENTED = new AtomicLong();
    static final AtomicLong SYMPTOM_SITES = new AtomicLong();
    static final AtomicLong PROPAGATION_SITES = new AtomicLong();
    static final AtomicLong HANDLER_HOOKS = new AtomicLong();
    static final Map<String, AtomicLong> RC_SITES_BY_RULE = new ConcurrentHashMap<>();
    static final Map<String, AtomicLong> RC_SITES_FILTERED = new ConcurrentHashMap<>();   // load-time conditions false
    static final Map<String, AtomicLong> RC_RUNTIME_REJECTED = new ConcurrentHashMap<>(); // runtime conditions false
    static final AtomicLong STATIC_CONDITIONS_UNKNOWN = new AtomicLong();
    private static final AtomicLong EXCEPTIONS_SEEN = new AtomicLong();
    private static final AtomicLong EXCEPTIONS_LINKED = new AtomicLong();
    private static final List<String> INSTRUMENTATION_ERRORS = new ArrayList<>();
    private static volatile boolean dirty;

    static void init(Spec s, String out, boolean verboseMode) {
        spec = s;
        outPath = out;
        verbose = verboseMode;
        dirty = true;
    }

    static void log(String msg) {
        if (verbose) System.err.println("[cgrizz-oracle] " + msg);
    }

    static void instrumentationError(String className, Throwable t) {
        synchronized (INSTRUMENTATION_ERRORS) {
            if (INSTRUMENTATION_ERRORS.size() < 50) {
                INSTRUMENTATION_ERRORS.add(className + ": " + t);
            }
        }
        dirty = true;
        log("could not instrument " + className + ": " + t);
    }

    // ── Hooks called from injected bytecode ───────────────────────────────────

    /** {@code value} produced at root-cause site {@code siteId} by rule {@code rule}. */
    public static void tag(Object value, int siteId, int rule) {
        if (value == null) return;
        try {
            doTag(value, siteId, rule);
        } catch (Throwable ignored) {
        }
    }

    public static void tagElements(Object array, int siteId, int rule) {
        if (!(array instanceof Object[])) return;
        try {
            for (Object o : (Object[]) array) {
                if (o != null) doTag(o, siteId, rule);
            }
        } catch (Throwable ignored) {
        }
    }

    /** Tags {@code result} with the tag of {@code source}, if any. */
    public static void propagate(Object result, Object source, int toElements) {
        if (result == null || source == null || TAGS.isEmpty()) return;
        try {
            int rc = TAGS.get(source);
            if (rc < 0) return;
            if (toElements == 1) tagElements(result, rc, -1);   // -1: inherited, not a new hit
            else doTag(result, rc, -1);
        } catch (Throwable ignored) {
        }
    }

    /** Call site with a receiver (virtual, interface, or non-constructor special). */
    public static void site(Object receiver, int siteId) {
        try {
            Sites.Site s = Sites.get(siteId);
            if (s == null) return;
            Class<?> rc = receiver == null ? null : receiver.getClass();
            if (s.selfEdge) {
                if (s.seen(rc, s.id)) {
                    s.hits.incrementAndGet();
                } else {
                    Resolved r = resolve(rc, s);
                    if (!s.bridgeOnly || r.bridge) {
                        hit(s, s.selfRule);
                        record(s.id, s, r.owner, s.calleeName, s.calleeDesc);
                        s.remember(rc, s.id);
                    }
                }
            }
            if (!s.symptom || TAGS.isEmpty()) return;
            int tag = -1;
            if (receiver != null && spec.matchReceiver) tag = TAGS.get(receiver);
            if (tag < 0 && rc != null && spec.matchReceiverClass) tag = TAGS.get(rc);
            if (tag < 0 && spec.matchCallerClass) {
                Integer t = TAGGED_CLASS_NAMES.get(s.callerOwner);
                if (t != null) tag = t;
            }
            if (tag < 0) return;
            if (rc != null && s.seen(rc, tag) && !(receiver instanceof java.lang.reflect.Member)) return;
            s.remember(rc, tag);
            Resolved r = resolve(rc, s);
            record(tag, s, r.owner, s.calleeName, s.calleeDesc);
            recordReflectiveTarget(tag, s, receiver);
        } catch (Throwable ignored) {
        }
    }

    /** Call site without a usable receiver (static calls and constructor calls). */
    public static void siteNoReceiver(int siteId) {
        try {
            Sites.Site s = Sites.get(siteId);
            if (s == null) return;
            if (s.selfEdge) {
                hit(s, s.selfRule);
                if (!s.seen(null, s.id)) {
                    record(s.id, s, s.calleeOwner, s.calleeName, s.calleeDesc);
                    s.remember(null, s.id);
                }
            }
            if (!s.symptom) return;
            Integer t = TAGGED_CLASS_NAMES.get(s.callerOwner);
            if (t != null) record(t, s, s.calleeOwner, s.calleeName, s.calleeDesc);
        } catch (Throwable ignored) {
        }
    }

    /**
     * After a root-cause instruction with runtime conditions: {@code result} is the
     * call's return value (or the field value), {@code ops} the receiver and the
     * arguments (null for primitives). The first root cause whose conditions hold acts.
     */
    public static void rc(Object result, Object[] ops, int siteId) {
        try {
            Sites.Site s = Sites.get(siteId);
            if (s == null) return;
            for (Spec.RootCause rule : s.conditionalRules) {
                Spec.Condition failed = firstFailing(rule, result, ops, s);
                if (failed != null) {
                    RC_RUNTIME_REJECTED.computeIfAbsent(rule.id + ": " + failed, k -> new AtomicLong()).incrementAndGet();
                    continue;
                }
                act(rule, result, ops, s);
                return;
            }
        } catch (Throwable ignored) {
        }
    }

    private static void act(Spec.RootCause rule, Object result, Object[] ops, Sites.Site s) {
        Object receiver = ops == null ? null : ops[0];
        if (rule.selfEdge) {
            Resolved r = resolve(receiver == null ? null : receiver.getClass(), s);
            if (!rule.bridgeOnly || r.bridge) {
                hit(s, rule.index);
                record(s.id, s, r.owner, s.calleeName, s.calleeDesc);
            }
        }
        Object value;
        if (rule.at.equals("field") || rule.tag.equals("return")) value = result;
        else if (rule.tag.equals("receiver")) value = receiver;
        else if (rule.tagArg >= 0 && ops != null && rule.tagArg + 1 < ops.length) value = ops[rule.tagArg + 1];
        else return;
        if (rule.tagElements) tagElements(value, s.id, rule.index);
        else tag(value, s.id, rule.index);
    }

    private static Spec.Condition firstFailing(Spec.RootCause rule, Object result, Object[] ops, Sites.Site s) {
        for (Spec.Condition c : rule.runtimeConditions) {
            Object v;
            if (c.on.equals("receiver")) v = ops == null ? null : ops[0];
            else if (c.arg >= 0) v = ops != null && c.arg + 1 < ops.length ? ops[c.arg + 1] : null;
            else v = result;  // return / value
            if (holds(c, v, s) == c.not) return c;
        }
        return null;
    }

    private static final ClassLoader SYSTEM = ClassLoader.getSystemClassLoader();

    /** A runtime predicate on an operand value (before applying 'not'). */
    static boolean holds(Spec.Condition c, Object v, Sites.Site s) {
        switch (c.is) {
            case "isNull":
                return v == null;
            case "nonNull":
                return v != null;
            case "runtimeClass":
                return v != null && c.type.matches(v.getClass().getName());
            case "classValue":
                return v instanceof Class && c.type.matches(((Class<?>) v).getName());
            case "customLoader": {
                if (v == null) return false;
                Class<?> k = v instanceof Class ? (Class<?>) v : v.getClass();
                ClassLoader l = k.getClassLoader();
                return l != null && l != SYSTEM && l != SYSTEM.getParent();
            }
            case "notPublic":
                if (v instanceof Class) return !Modifier.isPublic(((Class<?>) v).getModifiers());
                if (v instanceof java.lang.reflect.Member) return !Modifier.isPublic(((java.lang.reflect.Member) v).getModifiers());
                return false;
            case "noPublicNoArgConstructor":
                if (!(v instanceof Class)) return false;
                try {
                    ((Class<?>) v).getConstructor();
                    return false;
                } catch (NoSuchMethodException e) {
                    return true;
                }
            case "declaredTypeAbstract":
                return declaredTypeAbstract(c, v, s);
            case "taggedBy": {
                int tag = TAGS.get(v);
                if (tag < 0) return false;
                if (c.ids.contains("*")) return true;
                Sites.Site from = Sites.get(tag);
                return from != null && from.rootCause != null && c.ids.contains(from.rootCause.id);
            }
            default:
                return true;
        }
    }

    private static final Map<String, Boolean> ABSTRACT_TYPES = new ConcurrentHashMap<>();

    /** Is the type in the instruction's descriptor (return or argument type) abstract or an interface? */
    private static boolean declaredTypeAbstract(Spec.Condition c, Object v, Sites.Site s) {
        Type t = c.arg >= 0 ? Type.getArgumentTypes(s.calleeDesc)[c.arg] : Type.getReturnType(s.calleeDesc);
        if (t.getSort() != Type.OBJECT) return false;
        String name = t.getClassName();
        return ABSTRACT_TYPES.computeIfAbsent(name, n -> {
            for (ClassLoader l : new ClassLoader[]{null, v == null ? null : v.getClass().getClassLoader(), SYSTEM}) {
                try {
                    Class<?> k = Class.forName(n, false, l);
                    return k.isInterface() || Modifier.isAbstract(k.getModifiers());
                } catch (Throwable ignored) {
                }
            }
            return false;
        });
    }

    /** Start of an exception handler in application code. */
    public static void caught(Throwable t) {
        if (t == null) return;
        try {
            onException(t);
        } catch (Throwable ignored) {
        }
    }

    // ── Internals ─────────────────────────────────────────────────────────────

    private static void doTag(Object value, int siteId, int rule) {
        if (rule >= 0) {
            // A hit = the root cause applied to a value, even if an earlier root cause tagged it first.
            Sites.Site s = Sites.get(siteId);
            if (s != null) hit(s, rule);
        }
        if (TAGS.putIfAbsent(value, siteId)) {
            if (value instanceof Class) {
                TAGGED_CLASS_NAMES.putIfAbsent(((Class<?>) value).getName(), siteId);
            }
        }
    }

    private static void hit(Sites.Site s, int rule) {
        if (s.hits.getAndIncrement() == 0) HIT_SITES.add(s.id);
        if (rule >= 0) {
            s.fired.add(rule);
            RULE_HITS.computeIfAbsent(rule, k -> new AtomicLong()).incrementAndGet();
        }
        dirty = true;
    }

    static void onException(Throwable t) {
        if (!Sites.hasInsnSites() || !SEEN_EXCEPTIONS.putIfAbsent(t, 0)) return;
        EXCEPTIONS_SEEN.incrementAndGet();
        StackTraceElement[] st = t.getStackTrace();
        if (st == null || st.length == 0) return;
        StackTraceElement top = st[0];
        Set<String> names = new HashSet<>();
        for (Class<?> c = t.getClass(); c != null; c = c.getSuperclass()) names.add(c.getName());
        for (Sites.Site s : Sites.insnSites(top.getClassName() + "." + top.getMethodName())) {
            if (s.line > 0 && top.getLineNumber() > 0 && s.line != top.getLineNumber()) continue;
            boolean compatible = false;
            for (String e : s.rootCause.exceptions) {
                if (names.contains(e)) {
                    compatible = true;
                    break;
                }
            }
            if (!compatible) continue;
            EXCEPTIONS_LINKED.incrementAndGet();
            hit(s, s.rootCause.index);
            record(s.id, s, t.getClass().getName(), "<init>", "()V");
            return;
        }
    }

    private static void record(int rcSiteId, Sites.Site at, String calleeOwner, String calleeName, String calleeDesc) {
        String callee = calleeOwner + "." + calleeName + calleeDesc;
        String key = at.id + "|" + callee;
        Map<String, Map<String, Object>> bucket = SYMPTOMS.computeIfAbsent(rcSiteId, k -> new ConcurrentHashMap<>());
        if (bucket.containsKey(key)) return;
        Map<String, Object> sym = new LinkedHashMap<>();
        sym.put("caller", at.callerMethod());
        sym.put("callee", callee);
        sym.put("pc", at.index);
        sym.put("line", at.line);
        if (bucket.putIfAbsent(key, sym) == null) {
            dirty = true;
            log("symptom " + at.callerMethod() + " -> " + callee);
        }
    }

    /** Method.invoke / Constructor.newInstance / Class.newInstance also reach the reflected member. */
    private static void recordReflectiveTarget(int tag, Sites.Site s, Object receiver) {
        if (receiver instanceof Method && s.calleeName.equals("invoke")) {
            Method m = (Method) receiver;
            record(tag, s, m.getDeclaringClass().getName(), m.getName(), Type.getMethodDescriptor(m));
        } else if (receiver instanceof Constructor && s.calleeName.equals("newInstance")) {
            Constructor<?> c = (Constructor<?>) receiver;
            record(tag, s, c.getDeclaringClass().getName(), "<init>", Type.getConstructorDescriptor(c));
        } else if (receiver instanceof Class && s.calleeName.equals("newInstance")) {
            record(tag, s, ((Class<?>) receiver).getName(), "<init>", "()V");
        }
    }

    /** The class that declares the method the JVM dispatches to (like a call-graph edge). */
    static Resolved resolve(Class<?> receiverClass, Sites.Site s) {
        if (receiverClass == null || s.opcode == Opcodes.INVOKESTATIC || s.opcode == Opcodes.INVOKESPECIAL) {
            return new Resolved(s.calleeOwner, false);
        }
        String key = s.calleeName + s.calleeDesc;
        Map<String, Resolved> cache = RESOLVED.get(receiverClass);
        Resolved r = cache.get(key);
        if (r == null) {
            r = lookup(receiverClass, s.calleeName, s.calleeDesc);
            cache.put(key, r);
        }
        return r;
    }

    private static Resolved lookup(Class<?> c, String name, String desc) {
        try {
            for (Class<?> k = c; k != null; k = k.getSuperclass()) {
                Method m = declared(k, name, desc);
                if (m != null && !Modifier.isAbstract(m.getModifiers())) return new Resolved(k.getName(), m.isBridge());
            }
            Deque<Class<?>> todo = new ArrayDeque<>();
            for (Class<?> k = c; k != null; k = k.getSuperclass()) {
                for (Class<?> i : k.getInterfaces()) todo.add(i);
            }
            Set<Class<?>> seen = new HashSet<>();
            while (!todo.isEmpty()) {
                Class<?> i = todo.poll();
                if (!seen.add(i)) continue;
                Method m = declared(i, name, desc);
                if (m != null && !Modifier.isAbstract(m.getModifiers())) return new Resolved(i.getName(), m.isBridge());
                for (Class<?> j : i.getInterfaces()) todo.add(j);
            }
        } catch (Throwable ignored) {
        }
        return new Resolved(c.getName(), false);
    }

    private static Method declared(Class<?> k, String name, String desc) {
        for (Method m : k.getDeclaredMethods()) {
            if (m.getName().equals(name) && Type.getMethodDescriptor(m).equals(desc)) return m;
        }
        return null;
    }

    // ── Output ────────────────────────────────────────────────────────────────

    static synchronized void dump(String reason) {
        if (outPath == null || (!dirty && !reason.equals("shutdown"))) return;
        dirty = false;
        try {
            Map<String, Object> oracle = new TreeMap<>();
            for (Map.Entry<Integer, Map<String, Map<String, Object>>> e : SYMPTOMS.entrySet()) {
                Sites.Site rc = Sites.get(e.getKey());
                if (rc == null || e.getValue().isEmpty()) continue;
                @SuppressWarnings("unchecked")
                Map<String, Object> entry = (Map<String, Object>) oracle.get(rc.rcKey());
                if (entry == null) {
                    entry = new LinkedHashMap<>();
                    entry.put("method", rc.callerMethod());
                    entry.put("pc", rc.index);
                    entry.put("line", rc.line);
                    List<String> fired = firedRules(rc);
                    entry.put("rootCause", fired.isEmpty() ? rc.rootCause.id : fired.get(0));
                    entry.put("rootCauses", fired);
                    entry.put("kind", rc.rootCause.at);
                    entry.put("hits", rc.hits.get());
                    entry.put("symptoms", new ArrayList<Object>());
                    oracle.put(rc.rcKey(), entry);
                }
                @SuppressWarnings("unchecked")
                List<Object> syms = (List<Object>) entry.get("symptoms");
                List<Map<String, Object>> sorted = new ArrayList<>(e.getValue().values());
                sorted.sort(Comparator.comparing((Map<String, Object> m) -> (String) m.get("caller"))
                        .thenComparing(m -> (Integer) m.get("pc"))
                        .thenComparing(m -> (String) m.get("callee")));
                syms.addAll(sorted);
            }
            write(outPath, MiniJson.write(oracle));
            write(statsPath(outPath), MiniJson.write(stats(reason, oracle)));
        } catch (Throwable t) {
            System.err.println("[cgrizz-oracle] failed to write oracle: " + t);
        }
    }

    private static List<String> firedRules(Sites.Site s) {
        List<Integer> idx = new ArrayList<>(s.fired);
        java.util.Collections.sort(idx);
        List<String> ids = new ArrayList<>();
        for (int i : idx) ids.add(spec.rootCauses.get(i).id);
        return ids;
    }

    static String statsPath(String out) {
        return out.endsWith(".json") ? out.substring(0, out.length() - 5) + ".stats.json" : out + ".stats.json";
    }

    private static Map<String, Object> stats(String reason, Map<String, Object> oracle) {
        Map<String, Object> st = new LinkedHashMap<>();
        st.put("written", reason);
        st.put("pattern_class", spec.patternClass);
        st.put("specErrors", spec.errors);
        st.put("unsupported", spec.unsupported);
        st.put("classesInstrumented", CLASSES_INSTRUMENTED.get());
        synchronized (INSTRUMENTATION_ERRORS) {
            st.put("instrumentationErrors", new ArrayList<>(INSTRUMENTATION_ERRORS));
        }
        Map<String, Object> sitesByRule = new TreeMap<>();
        Map<String, Object> hitsByRule = new TreeMap<>();
        for (Spec.RootCause rc : spec.rootCauses) {
            AtomicLong n = RC_SITES_BY_RULE.get(rc.id);
            sitesByRule.put(rc.id, n == null ? 0 : n.get());
            hitsByRule.put(rc.id, 0L);
        }
        for (Map.Entry<Integer, AtomicLong> e : RULE_HITS.entrySet()) {
            hitsByRule.put(spec.rootCauses.get(e.getKey()).id, e.getValue().get());
        }
        st.put("rootCauseSites", sitesByRule);
        st.put("rootCauseSitesExcludedByConditions", counts(RC_SITES_FILTERED));
        st.put("rootCauseRuntimeRejections", counts(RC_RUNTIME_REJECTED));
        st.put("conditionsUndecided", STATIC_CONDITIONS_UNKNOWN.get());
        st.put("rootCauseHits", hitsByRule);
        st.put("symptomSites", SYMPTOM_SITES.get());
        st.put("propagationSites", PROPAGATION_SITES.get());
        st.put("handlerHooks", HANDLER_HOOKS.get());
        st.put("taggedObjects", TAGS.size());
        st.put("exceptionsSeen", EXCEPTIONS_SEEN.get());
        st.put("exceptionsLinked", EXCEPTIONS_LINKED.get());
        int n = 0;
        for (Object v : oracle.values()) {
            @SuppressWarnings("unchecked") Map<String, Object> m = (Map<String, Object>) v;
            n += ((List<?>) m.get("symptoms")).size();
        }
        st.put("symptomsRecorded", n);
        return st;
    }

    private static Map<String, Object> counts(Map<String, AtomicLong> m) {
        Map<String, Object> out = new TreeMap<>();
        for (Map.Entry<String, AtomicLong> e : m.entrySet()) out.put(e.getKey(), e.getValue().get());
        return out;
    }

    private static void write(String path, String content) throws Exception {
        File target = new File(path).getAbsoluteFile();
        File parent = target.getParentFile();
        if (parent != null) parent.mkdirs();
        File tmp = new File(target.getPath() + ".tmp");
        Files.write(tmp.toPath(), content.getBytes(StandardCharsets.UTF_8));
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
}
