package edu.utd.fastlab.cgrizz.oracle;

import org.objectweb.asm.Opcodes;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The oracle spec (oracle_spec.json): a declarative description of a root-cause
 * pattern. See SPEC.md for the format. All validation problems are collected in
 * {@link #errors}; an invalid spec disables instrumentation instead of crashing
 * the target program.
 */
final class Spec {

    /** A '*' / '?' wildcard pattern over dotted names or JVM descriptors. */
    static final class Glob {
        final String text;
        private final Pattern regex;

        Glob(String text) {
            this.text = text;
            StringBuilder b = new StringBuilder();
            for (char c : text.toCharArray()) {
                if (c == '*') b.append(".*");
                else if (c == '?') b.append('.');
                else b.append(Pattern.quote(String.valueOf(c)));
            }
            this.regex = Pattern.compile(b.toString());
        }

        boolean matches(String s) {
            return text.equals("*") || regex.matcher(s).matches();
        }

        static boolean matches(Glob g, String s) {
            return g == null || g.matches(s);
        }
    }

    /**
     * A condition on one operand of a root-cause instruction. Static predicates
     * are decided when the class is loaded (data flow inside the method), runtime
     * predicates when the instruction executes (actual values). All conditions of
     * a root cause must hold.
     */
    static final class Condition {
        static final Set<String> STATIC = new HashSet<>(Arrays.asList(
                "constant", "nonConstant", "classLiteral", "fromParameter", "fromField", "fromCall", "fromNew",
                "staticType"));
        static final Set<String> RUNTIME = new HashSet<>(Arrays.asList(
                "isNull", "nonNull", "runtimeClass", "classValue", "customLoader", "notPublic",
                "noPublicNoArgConstructor", "declaredTypeAbstract", "taggedBy", "bridge"));

        String on;             // receiver | argN | return | value | target
        int arg = -1;          // N for argN
        String is;
        boolean not;
        Glob owner, name, type;      // fromField / fromCall (owner, name), fromNew / staticType / runtimeClass / classValue (type)
        final Set<String> ids = new HashSet<>();   // taggedBy

        boolean isStatic() {
            return STATIC.contains(is);
        }

        @Override
        public String toString() {
            return (not ? "not " : "") + on + " " + is;
        }
    }

    static final class RootCause {
        int index;
        String id;
        String at;                 // call | field | insn
        Glob owner, name, desc;    // call / field matchers (dotted owner)
        Glob inOwner, inName;      // optional: restrict the enclosing method
        String tag = "none";       // return | receiver | argN | value | none
        int tagArg = -1;           // N for argN
        boolean tagElements;       // trailing [] : tag the elements of an array
        boolean selfEdge;          // the matched call is itself the symptom
        boolean bridgeOnly;        // with selfEdge: only when the resolved target is a bridge
        String access = "get";     // field: get | put | any
        Set<Integer> opcodes = new HashSet<>();     // insn
        List<String> exceptions = new ArrayList<>(); // insn: implicit exceptions to link
        final List<Condition> staticConditions = new ArrayList<>();
        final List<Condition> runtimeConditions = new ArrayList<>();

        boolean matchesEnclosing(String ownerDotted, String methodName) {
            return Glob.matches(inOwner, ownerDotted) && Glob.matches(inName, methodName);
        }
    }

    static final class Propagation {
        String id;
        Glob owner, name, desc;
        int from = -1;             // -1 = receiver, N = argument N
        boolean toElements;        // return[]
    }

    String description = "";
    String unsupported;
    final List<RootCause> rootCauses = new ArrayList<>();
    final List<Propagation> propagations = new ArrayList<>();
    boolean matchReceiver = true;
    boolean matchReceiverClass = true;
    boolean matchCallerClass = false;
    final List<Glob> exclude = new ArrayList<>();
    final List<String> errors = new ArrayList<>();

    boolean hasInsnRootCauses() {
        for (RootCause rc : rootCauses) if (rc.at.equals("insn")) return true;
        return false;
    }

    boolean usable() {
        return errors.isEmpty() && unsupported == null && !rootCauses.isEmpty();
    }

    // ── Opcode names accepted by "insn" root causes ───────────────────────────

    static final Map<String, Integer> OPCODES = new HashMap<>();
    private static final Map<Integer, List<String>> DEFAULT_EXCEPTIONS = new HashMap<>();

    private static void op(String name, int code, String... exceptions) {
        OPCODES.put(name, code);
        DEFAULT_EXCEPTIONS.put(code, Arrays.asList(exceptions));
    }

    static {
        String npe = "java.lang.NullPointerException";
        String aioobe = "java.lang.ArrayIndexOutOfBoundsException";
        String linkage = "java.lang.LinkageError";
        op("CHECKCAST", Opcodes.CHECKCAST, "java.lang.ClassCastException");
        for (String n : new String[]{"IALOAD", "LALOAD", "FALOAD", "DALOAD", "AALOAD", "BALOAD", "CALOAD", "SALOAD",
                                     "IASTORE", "LASTORE", "FASTORE", "DASTORE", "BASTORE", "CASTORE", "SASTORE"}) {
            try {
                op(n, Opcodes.class.getField(n).getInt(null), aioobe, npe);
            } catch (ReflectiveOperationException e) {
                throw new IllegalStateException(e);
            }
        }
        op("AASTORE", Opcodes.AASTORE, aioobe, npe, "java.lang.ArrayStoreException");
        op("ARRAYLENGTH", Opcodes.ARRAYLENGTH, npe);
        op("ATHROW", Opcodes.ATHROW, npe);
        op("MONITORENTER", Opcodes.MONITORENTER, npe);
        op("MONITOREXIT", Opcodes.MONITOREXIT, npe, "java.lang.IllegalMonitorStateException");
        op("GETFIELD", Opcodes.GETFIELD, npe, linkage);
        op("PUTFIELD", Opcodes.PUTFIELD, npe, linkage);
        op("GETSTATIC", Opcodes.GETSTATIC, linkage);
        op("PUTSTATIC", Opcodes.PUTSTATIC, linkage);
        op("NEW", Opcodes.NEW, linkage);
        op("INVOKEVIRTUAL", Opcodes.INVOKEVIRTUAL, npe, linkage);
        op("INVOKEINTERFACE", Opcodes.INVOKEINTERFACE, npe, linkage);
        op("INVOKESPECIAL", Opcodes.INVOKESPECIAL, npe, linkage);
        op("INVOKESTATIC", Opcodes.INVOKESTATIC, linkage);
        op("NEWARRAY", Opcodes.NEWARRAY, "java.lang.NegativeArraySizeException");
        op("ANEWARRAY", Opcodes.ANEWARRAY, "java.lang.NegativeArraySizeException");
        op("MULTIANEWARRAY", Opcodes.MULTIANEWARRAY, "java.lang.NegativeArraySizeException");
        for (String n : new String[]{"IDIV", "LDIV", "IREM", "LREM"}) {
            try {
                op(n, Opcodes.class.getField(n).getInt(null), "java.lang.ArithmeticException");
            } catch (ReflectiveOperationException e) {
                throw new IllegalStateException(e);
            }
        }
    }

    // ── Parsing ───────────────────────────────────────────────────────────────

    static Spec load(String path) {
        Spec spec = new Spec();
        try {
            String text = new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
            spec.read(MiniJson.parse(text));
        } catch (Exception e) {
            spec.errors.add("cannot read spec " + path + ": " + e.getMessage());
        }
        return spec;
    }

    private static final Set<String> TOP_KEYS = new HashSet<>(Arrays.asList(
            "description", "unsupported", "rootCauses", "propagate", "symptoms", "exclude"));
    private static final Set<String> RC_KEYS = new HashSet<>(Arrays.asList(
            "id", "at", "owner", "name", "desc", "in", "tag", "selfEdge", "when", "access", "opcodes", "exceptions", "note"));
    private static final Set<String> COND_KEYS = new HashSet<>(Arrays.asList(
            "on", "is", "not", "owner", "name", "type", "ids", "note"));
    private static final Set<String> PROP_KEYS = new HashSet<>(Arrays.asList(
            "owner", "name", "desc", "from", "to", "note"));

    @SuppressWarnings("unchecked")
    private void read(Object json) {
        if (!(json instanceof Map)) {
            errors.add("spec must be a JSON object");
            return;
        }
        Map<String, Object> m = (Map<String, Object>) json;
        unknownKeys(m, TOP_KEYS, "spec");
        description = str(m, "description", "");
        unsupported = str(m, "unsupported", null);
        if (unsupported != null) return;

        List<Object> rcs = list(m, "rootCauses");
        if (rcs.isEmpty()) errors.add("rootCauses must be a non-empty array");
        Set<String> ids = new HashSet<>();
        for (int k = 0; k < rcs.size(); k++) {
            if (!(rcs.get(k) instanceof Map)) {
                errors.add("rootCauses[" + k + "] must be an object");
                continue;
            }
            RootCause rc = rootCause((Map<String, Object>) rcs.get(k), k);
            if (rc == null) continue;
            if (!ids.add(rc.id)) errors.add("duplicate root cause id '" + rc.id + "'");
            rc.index = rootCauses.size();
            rootCauses.add(rc);
        }

        List<Object> props = list(m, "propagate");
        for (int k = 0; k < props.size(); k++) {
            Object p = props.get(k);
            if ("reflection".equals(p)) {
                propagations.addAll(reflectionPreset());
            } else if (p instanceof Map) {
                Propagation pr = propagation((Map<String, Object>) p, "propagate[" + k + "]");
                if (pr != null) propagations.add(pr);
            } else {
                errors.add("propagate[" + k + "] must be \"reflection\" or an object");
            }
        }

        Object sym = m.get("symptoms");
        if (sym instanceof Map) {
            List<Object> match = list((Map<String, Object>) sym, "match");
            if (!match.isEmpty()) {
                matchReceiver = match.contains("receiver");
                matchReceiverClass = match.contains("receiverClass");
                matchCallerClass = match.contains("callerClass");
                for (Object o : match) {
                    if (!Arrays.asList("receiver", "receiverClass", "callerClass").contains(o)) {
                        errors.add("symptoms.match: unknown mode '" + o + "' (use receiver, receiverClass, callerClass)");
                    }
                }
            }
        } else if (sym != null) {
            errors.add("symptoms must be an object");
        }

        for (Object o : list(m, "exclude")) exclude.add(new Glob(String.valueOf(o)));
    }

    private RootCause rootCause(Map<String, Object> m, int k) {
        String where = "rootCauses[" + k + "]";
        unknownKeys(m, RC_KEYS, where);
        RootCause rc = new RootCause();
        rc.id = str(m, "id", "rc" + k);
        rc.at = str(m, "at", null);
        where = where + " (" + rc.id + ")";
        if (rc.at == null || !Arrays.asList("call", "field", "insn").contains(rc.at)) {
            errors.add(where + ": 'at' must be one of call, field, insn");
            return null;
        }
        rc.owner = glob(m, "owner");
        rc.name = glob(m, "name");
        rc.desc = glob(m, "desc");
        Object in = m.get("in");
        if (in instanceof Map) {
            @SuppressWarnings("unchecked") Map<String, Object> im = (Map<String, Object>) in;
            rc.inOwner = glob(im, "owner");
            rc.inName = glob(im, "name");
        }

        if (rc.at.equals("insn")) {
            for (Object o : list(m, "opcodes")) {
                Integer code = OPCODES.get(String.valueOf(o).toUpperCase());
                if (code == null) errors.add(where + ": unsupported opcode '" + o + "' (supported: " + sorted(OPCODES.keySet()) + ")");
                else rc.opcodes.add(code);
            }
            if (rc.opcodes.isEmpty()) errors.add(where + ": insn root causes need a non-empty 'opcodes' array");
            for (Object o : list(m, "exceptions")) rc.exceptions.add(String.valueOf(o));
            if (m.containsKey("when")) errors.add(where + ": insn root causes do not take 'when' (use 'exceptions')");
            if (rc.exceptions.isEmpty()) {
                Set<String> defaults = new LinkedHashSet<>();
                for (int code : rc.opcodes) defaults.addAll(DEFAULT_EXCEPTIONS.getOrDefault(code, Collections.<String>emptyList()));
                rc.exceptions.addAll(defaults);
            }
            return rc;
        }

        if (rc.owner == null || rc.name == null) {
            errors.add(where + ": '" + rc.at + "' root causes need 'owner' and 'name'");
        }
        String tag = str(m, "tag", rc.at.equals("field") ? "value" : "none");
        if (tag.endsWith("[]")) {
            rc.tagElements = true;
            tag = tag.substring(0, tag.length() - 2);
        }
        rc.tag = tag;
        if (rc.at.equals("field")) {
            rc.access = str(m, "access", "get");
            if (!Arrays.asList("get", "put", "any").contains(rc.access)) errors.add(where + ": 'access' must be get, put or any");
            if (!tag.equals("value")) errors.add(where + ": field root causes only support tag 'value'");
        } else {
            if (tag.startsWith("arg")) {
                try {
                    rc.tagArg = Integer.parseInt(tag.substring(3));
                } catch (NumberFormatException e) {
                    errors.add(where + ": bad tag '" + tag + "'");
                }
            } else if (!Arrays.asList("return", "receiver", "none").contains(tag)) {
                errors.add(where + ": tag must be return, receiver, argN (optionally with []), or none");
            }
            rc.selfEdge = bool(m, "selfEdge");
            if (tag.equals("none") && !rc.selfEdge) {
                errors.add(where + ": a call root cause must tag a value or set selfEdge: true");
            }
        }
        conditions(rc, m.get("when"), where);
        return rc;
    }

    private void conditions(RootCause rc, Object when, String where) {
        if (when == null) return;
        if ("bridge".equals(when)) {          // shorthand kept from the first spec version
            when = Collections.singletonList(new java.util.LinkedHashMap<String, Object>() {{
                put("on", "target");
                put("is", "bridge");
            }});
        }
        if (!(when instanceof List)) {
            errors.add(where + ": 'when' must be a list of conditions");
            return;
        }
        List<?> list = (List<?>) when;
        for (int k = 0; k < list.size(); k++) {
            String w = where + ".when[" + k + "]";
            if (!(list.get(k) instanceof Map)) {
                errors.add(w + ": a condition must be an object");
                continue;
            }
            @SuppressWarnings("unchecked") Map<String, Object> m = (Map<String, Object>) list.get(k);
            unknownKeys(m, COND_KEYS, w);
            Condition c = new Condition();
            c.on = str(m, "on", null);
            c.is = str(m, "is", null);
            c.not = bool(m, "not");
            c.owner = glob(m, "owner");
            c.name = glob(m, "name");
            c.type = glob(m, "type");
            for (Object id : list(m, "ids")) c.ids.add(String.valueOf(id));
            if (c.is == null || !(Condition.STATIC.contains(c.is) || Condition.RUNTIME.contains(c.is))) {
                errors.add(w + ": 'is' must be one of " + sorted(Condition.STATIC) + " (decided at load time) or "
                        + sorted(Condition.RUNTIME) + " (decided at run time)");
                continue;
            }
            if (c.on == null) {
                errors.add(w + ": 'on' is required (receiver, argN, return, value, target)");
                continue;
            }
            if (c.on.startsWith("arg")) {
                try {
                    c.arg = Integer.parseInt(c.on.substring(3));
                } catch (NumberFormatException e) {
                    errors.add(w + ": bad operand '" + c.on + "'");
                }
            } else if (!Arrays.asList("receiver", "return", "value", "target").contains(c.on)) {
                errors.add(w + ": 'on' must be receiver, argN, return, value or target");
            }
            boolean field = rc.at.equals("field");
            if (field != c.on.equals("value")) {
                errors.add(w + ": field root causes use 'on': \"value\"; call root causes use receiver/argN/return/target");
            }
            if (c.isStatic() && (c.on.equals("return") || c.on.equals("target"))) {
                errors.add(w + ": '" + c.is + "' is decided at load time and cannot look at '" + c.on + "'");
            }
            if (c.isStatic() && field && "get".equals(rc.access)) {
                errors.add(w + ": load-time conditions on a field read are not supported (the value is the field itself)");
            }
            if (c.is.equals("bridge")) {
                if (!c.on.equals("target") || !rc.selfEdge || c.not) {
                    errors.add(w + ": 'bridge' applies to 'target' of a selfEdge root cause");
                }
                rc.bridgeOnly = true;
                continue;
            }
            if (c.is.equals("taggedBy") && c.ids.isEmpty()) errors.add(w + ": 'taggedBy' needs 'ids' (root cause ids, or \"*\")");
            if ((c.is.equals("fromNew") || c.is.equals("staticType") || c.is.equals("runtimeClass")
                    || c.is.equals("classValue")) && c.type == null) {
                errors.add(w + ": '" + c.is + "' needs 'type'");
            }
            if (c.is.equals("declaredTypeAbstract") && !c.on.equals("return") && c.arg < 0) {
                errors.add(w + ": 'declaredTypeAbstract' applies to 'return' or an argument");
            }
            (c.isStatic() ? rc.staticConditions : rc.runtimeConditions).add(c);
        }
    }

    private Propagation propagation(Map<String, Object> m, String where) {
        unknownKeys(m, PROP_KEYS, where);
        Propagation p = new Propagation();
        p.owner = glob(m, "owner");
        p.name = glob(m, "name");
        p.desc = glob(m, "desc");
        p.id = where;
        if (p.owner == null || p.name == null) errors.add(where + ": needs 'owner' and 'name'");
        String from = str(m, "from", "receiver");
        if (from.equals("receiver")) {
            p.from = -1;
        } else if (from.startsWith("arg")) {
            try {
                p.from = Integer.parseInt(from.substring(3));
            } catch (NumberFormatException e) {
                errors.add(where + ": bad 'from' '" + from + "'");
            }
        } else {
            errors.add(where + ": 'from' must be receiver or argN");
        }
        String to = str(m, "to", "return");
        if (to.equals("return[]")) p.toElements = true;
        else if (!to.equals("return")) errors.add(where + ": 'to' must be return or return[]");
        return p;
    }

    /** Values derived from a tagged reflective object inherit its tag. */
    private static List<Propagation> reflectionPreset() {
        String[][] rules = {
            {"java.lang.Class", "newInstance", ""},
            {"java.lang.Class", "cast", ""},
            {"java.lang.Class", "getConstructor", ""},
            {"java.lang.Class", "getDeclaredConstructor", ""},
            {"java.lang.Class", "getMethod", ""},
            {"java.lang.Class", "getDeclaredMethod", ""},
            {"java.lang.Class", "getField", ""},
            {"java.lang.Class", "getDeclaredField", ""},
            {"java.lang.Class", "getConstructors", "[]"},
            {"java.lang.Class", "getDeclaredConstructors", "[]"},
            {"java.lang.Class", "getMethods", "[]"},
            {"java.lang.Class", "getDeclaredMethods", "[]"},
            {"java.lang.Class", "getFields", "[]"},
            {"java.lang.Class", "getDeclaredFields", "[]"},
            {"java.lang.Class", "getEnumConstants", "[]"},
            {"java.lang.reflect.Constructor", "newInstance", ""},
            {"java.lang.reflect.Method", "invoke", ""},
            {"java.lang.reflect.Field", "get", ""},
        };
        List<Propagation> out = new ArrayList<>();
        for (String[] r : rules) {
            Propagation p = new Propagation();
            p.owner = new Glob(r[0]);
            p.name = new Glob(r[1]);
            p.toElements = r[2].equals("[]");
            p.id = "reflection:" + r[0] + "." + r[1];
            out.add(p);
        }
        return out;
    }

    // ── Small helpers ─────────────────────────────────────────────────────────

    private void unknownKeys(Map<String, Object> m, Set<String> allowed, String where) {
        for (String k : m.keySet()) {
            if (!allowed.contains(k)) errors.add(where + ": unknown key '" + k + "' (allowed: " + sorted(allowed) + ")");
        }
    }

    private static List<String> sorted(Set<String> s) {
        List<String> l = new ArrayList<>(s);
        Collections.sort(l);
        return l;
    }

    private static String str(Map<String, Object> m, String k, String dflt) {
        Object v = m.get(k);
        return v == null ? dflt : String.valueOf(v);
    }

    private static boolean bool(Map<String, Object> m, String k) {
        return Boolean.TRUE.equals(m.get(k));
    }

    private static Glob glob(Map<String, Object> m, String k) {
        Object v = m.get(k);
        return v == null ? null : new Glob(String.valueOf(v));
    }

    @SuppressWarnings("unchecked")
    private List<Object> list(Map<String, Object> m, String k) {
        Object v = m.get(k);
        if (v == null) return Collections.emptyList();
        if (v instanceof List) return (List<Object>) v;
        errors.add("'" + k + "' must be an array");
        return Collections.emptyList();
    }
}
