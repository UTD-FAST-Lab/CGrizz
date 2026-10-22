package edu.utd.fastlab.cgrizz.oracle;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassTooLargeException;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.MethodTooLargeException;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.FrameNode;
import org.objectweb.asm.tree.InsnList;
import org.objectweb.asm.tree.InsnNode;
import org.objectweb.asm.tree.IntInsnNode;
import org.objectweb.asm.tree.LabelNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.LineNumberNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TryCatchBlockNode;
import org.objectweb.asm.tree.TypeInsnNode;
import org.objectweb.asm.tree.VarInsnNode;

import java.lang.instrument.ClassFileTransformer;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Injects calls to {@link Tracker} at the instructions selected by the spec.
 *
 * Every injection is straight-line and stack-neutral (DUP / spill to fresh
 * locals above the original max_locals / INVOKESTATIC), so the class keeps its
 * original stack-map frames and is written with COMPUTE_MAXS only. No class
 * hierarchy is ever resolved at transform time.
 */
final class OracleTransformer implements ClassFileTransformer {

    private static final String TRACKER = "edu/utd/fastlab/cgrizz/oracle/Tracker";
    private static final String[] SKIP_PREFIXES = {
        "java/", "javax/", "sun/", "com/sun/", "jdk/", "edu/utd/fastlab/cgrizz/",
    };

    private final Spec spec;

    OracleTransformer(Spec spec) {
        this.spec = spec;
    }

    @Override
    public byte[] transform(ClassLoader loader, String className, Class<?> redefined,
                            ProtectionDomain pd, byte[] bytes) {
        if (loader == null || className == null || bytes == null) return null;
        for (String p : SKIP_PREFIXES) if (className.startsWith(p)) return null;
        String dotted = className.replace('/', '.');
        for (Spec.Glob g : spec.exclude) if (g.matches(dotted)) return null;
        try {
            return instrument(bytes, false);
        } catch (MethodTooLargeException | ClassTooLargeException tooLarge) {
            // Retry with root-cause and propagation hooks only (no generic symptom sites).
            try {
                return instrument(bytes, true);
            } catch (Throwable t) {
                Tracker.instrumentationError(dotted, t);
            }
        } catch (Throwable t) {
            Tracker.instrumentationError(dotted, t);
        }
        return null;
    }

    /** Per-class counters, committed to the statistics only if the class is written. */
    private static final class Counters {
        long symptomSites, propagationSites, handlerHooks;
        final Map<String, Long> rcSites = new HashMap<>();

        final Map<String, Long> filtered = new HashMap<>();

        void rc(String id) {
            rcSites.merge(id, 1L, Long::sum);
        }

        void filtered(String id) {
            filtered.merge(id, 1L, Long::sum);
        }

        void commit() {
            Tracker.SYMPTOM_SITES.addAndGet(symptomSites);
            Tracker.PROPAGATION_SITES.addAndGet(propagationSites);
            Tracker.HANDLER_HOOKS.addAndGet(handlerHooks);
            for (Map.Entry<String, Long> e : rcSites.entrySet()) {
                Tracker.RC_SITES_BY_RULE.computeIfAbsent(e.getKey(), k -> new AtomicLong()).addAndGet(e.getValue());
            }
            for (Map.Entry<String, Long> e : filtered.entrySet()) {
                Tracker.RC_SITES_FILTERED.computeIfAbsent(e.getKey(), k -> new AtomicLong()).addAndGet(e.getValue());
            }
        }
    }

    byte[] instrument(byte[] bytes, boolean lite) {
        ClassReader cr = new ClassReader(bytes);
        ClassNode cn = new ClassNode();
        cr.accept(cn, 0);
        Counters c = new Counters();
        boolean changed = false;
        for (MethodNode mn : cn.methods) {
            if (mn.instructions.size() > 0) changed |= method(cn, mn, lite, c);
        }
        if (!changed) {
            c.commit();
            return null;
        }
        ClassWriter cw = new ClassWriter(ClassWriter.COMPUTE_MAXS);
        cn.accept(cw);
        byte[] out = cw.toByteArray();
        c.commit();
        Tracker.CLASSES_INSTRUMENTED.incrementAndGet();
        return out;
    }

    private boolean method(ClassNode cn, MethodNode mn, boolean lite, Counters c) {
        String owner = cn.name.replace('/', '.');
        boolean changed = false;
        int line = -1;
        int ordinal = -1;
        // Load-time conditions need the data flow of the unmodified method.
        Provenance prov = needsProvenance(mn) ? Provenance.of(cn.name, mn) : null;
        for (AbstractInsnNode in : mn.instructions.toArray()) {
            if (in instanceof LineNumberNode) {
                line = ((LineNumberNode) in).line;
                continue;
            }
            int op = in.getOpcode();
            if (op < 0) continue;
            ordinal++;
            for (Spec.RootCause rc : spec.rootCauses) {
                if (rc.at.equals("insn") && rc.opcodes.contains(op) && rc.matchesEnclosing(owner, mn.name)) {
                    Sites.Site s = site(owner, mn, ordinal, line, op);
                    s.rootCause = rc;
                    Sites.add(s);
                    c.rc(rc.id);
                    Tracker.log("root cause site " + rc.id + " at " + s.rcKey());
                    break;
                }
            }
            if (in instanceof MethodInsnNode) {
                changed |= invoke(owner, mn, (MethodInsnNode) in, ordinal, line, lite, c, prov);
            } else if (in instanceof FieldInsnNode) {
                changed |= field(owner, mn, (FieldInsnNode) in, ordinal, line, c, prov);
            }
        }
        if (spec.hasInsnRootCauses()) changed |= handlerHooks(mn, c);
        return changed;
    }

    /** Does some root cause with load-time conditions name-match an instruction of this method? */
    private boolean needsProvenance(MethodNode mn) {
        for (Spec.RootCause rc : spec.rootCauses) {
            if (rc.staticConditions.isEmpty()) continue;
            for (AbstractInsnNode in : mn.instructions.toArray()) {
                if (in instanceof MethodInsnNode && rc.at.equals("call")
                        && rc.name.matches(((MethodInsnNode) in).name)
                        && rc.owner.matches(Type.getObjectType(((MethodInsnNode) in).owner).getClassName())) {
                    return true;
                }
                if (in instanceof FieldInsnNode && rc.at.equals("field") && rc.name.matches(((FieldInsnNode) in).name)) {
                    return true;
                }
            }
        }
        return false;
    }

    /**
     * Load-time conditions of a root cause at an instruction. depthOf maps an
     * operand to its stack depth just before the instruction (-1: no such operand).
     */
    private static boolean staticConditionsHold(Spec.RootCause rc, Provenance prov, AbstractInsnNode at,
                                                java.util.function.ToIntFunction<Spec.Condition> depthOf) {
        for (Spec.Condition cond : rc.staticConditions) {
            int depth = depthOf.applyAsInt(cond);
            if (depth < 0) return false;
            if (prov == null) {
                Tracker.STATIC_CONDITIONS_UNKNOWN.incrementAndGet();
                continue;                     // cannot tell: keep it a root cause
            }
            if (!Provenance.holds(cond, prov.origins(at, depth))) return false;
        }
        return true;
    }

    private static Sites.Site site(String owner, MethodNode mn, int ordinal, int line, int op) {
        Sites.Site s = new Sites.Site();
        s.callerOwner = owner;
        s.callerName = mn.name;
        s.callerDesc = mn.desc;
        s.index = ordinal;
        s.line = line;
        s.opcode = op;
        return s;
    }

    private static boolean isRef(Type t) {
        return t.getSort() == Type.OBJECT || t.getSort() == Type.ARRAY;
    }

    // ── Invoke instructions ───────────────────────────────────────────────────

    private boolean invoke(String owner, MethodNode mn, MethodInsnNode m, int ordinal, int line,
                           boolean lite, Counters c, Provenance prov) {
        int op = m.getOpcode();
        String calleeOwner = Type.getObjectType(m.owner).getClassName();
        boolean isCtor = m.name.equals("<init>");
        boolean hasRecv = op != Opcodes.INVOKESTATIC && !isCtor;
        Type[] args = Type.getArgumentTypes(m.desc);
        Type ret = Type.getReturnType(m.desc);
        boolean retRef = isRef(ret);

        List<Spec.RootCause> rcs = new ArrayList<>();          // unconditional at run time
        List<Spec.RootCause> conditional = new ArrayList<>();  // decided by Tracker.rc at run time
        int nArgs = args.length;
        for (Spec.RootCause rc : spec.rootCauses) {
            if (rc.at.equals("call") && rc.owner.matches(calleeOwner) && rc.name.matches(m.name)
                    && Spec.Glob.matches(rc.desc, m.desc) && rc.matchesEnclosing(owner, mn.name)) {
                boolean holds = staticConditionsHold(rc, prov, m, cond ->
                        cond.on.equals("receiver") ? (op == Opcodes.INVOKESTATIC ? -1 : nArgs)
                                : cond.arg >= 0 && cond.arg < nArgs ? nArgs - 1 - cond.arg : -1);
                if (!holds) {
                    c.filtered(rc.id);
                    continue;
                }
                (rc.runtimeConditions.isEmpty() ? rcs : conditional).add(rc);
            }
        }
        List<Spec.Propagation> props = new ArrayList<>();
        if (retRef) {
            for (Spec.Propagation p : spec.propagations) {
                boolean sourceOk = p.from < 0 ? hasRecv : p.from < args.length && isRef(args[p.from]);
                if (sourceOk && p.owner.matches(calleeOwner) && p.name.matches(m.name)
                        && Spec.Glob.matches(p.desc, m.desc)) {
                    props.add(p);
                }
            }
        }
        boolean symptom = !lite && (hasRecv
                ? spec.matchReceiver || spec.matchReceiverClass || spec.matchCallerClass
                : spec.matchCallerClass);
        if (rcs.isEmpty() && conditional.isEmpty() && props.isEmpty() && !symptom) return false;

        Sites.Site s = site(owner, mn, ordinal, line, op);
        s.calleeOwner = calleeOwner;
        s.calleeName = m.name;
        s.calleeDesc = m.desc;
        s.symptom = symptom;
        if (!rcs.isEmpty()) s.rootCause = rcs.get(0);
        else if (!conditional.isEmpty()) s.rootCause = conditional.get(0);
        s.conditionalRules.addAll(conditional);
        for (Spec.RootCause rc : rcs) {
            if (rc.selfEdge) {
                s.selfEdge = true;
                s.selfRule = rc.index;
                s.bridgeOnly = rc.bridgeOnly;
                break;
            }
        }
        int id = Sites.add(s);
        for (Spec.RootCause rc : rcs) {
            c.rc(rc.id);
            Tracker.log("root cause site " + rc.id + " at " + s.rcKey() + " (" + calleeOwner + "." + m.name + ")");
        }
        for (Spec.RootCause rc : conditional) {
            c.rc(rc.id);
            Tracker.log("root cause site " + rc.id + " at " + s.rcKey() + " (" + calleeOwner + "." + m.name
                    + ", runtime conditions " + rc.runtimeConditions + ")");
        }
        boolean needOps = !conditional.isEmpty();
        if (symptom) c.symptomSites++;
        c.propagationSites += props.size();

        boolean recvHook = hasRecv && (symptom || s.selfEdge);
        boolean noRecvHook = !hasRecv && (symptom || s.selfEdge);
        List<Spec.RootCause> tagRecv = new ArrayList<>();
        List<Spec.RootCause> tagArgs = new ArrayList<>();
        List<Spec.RootCause> tagRet = new ArrayList<>();
        for (Spec.RootCause rc : rcs) {
            if (rc.tag.equals("receiver") && hasRecv) tagRecv.add(rc);
            else if (rc.tagArg >= 0 && rc.tagArg < args.length && isRef(args[rc.tagArg])) tagArgs.add(rc);
            else if (rc.tag.equals("return") && retRef) tagRet.add(rc);
        }
        boolean recvAfter = false;
        boolean argSourced = false;
        for (Spec.Propagation p : props) {
            if (p.from < 0) recvAfter = true;
            else argSourced = true;
        }
        if (needOps && hasRecv) recvAfter = true;
        boolean spill = args.length > 0
                && (recvHook || !tagRecv.isEmpty() || !tagArgs.isEmpty() || recvAfter || argSourced || needOps);

        int next = mn.maxLocals;
        int[] argLocal = new int[args.length];
        if (spill) {
            for (int k = 0; k < args.length; k++) {
                argLocal[k] = next;
                next += args[k].getSize();
            }
        }
        int recvLocal = recvAfter ? next : -1;

        InsnList pre = new InsnList();
        if (spill) {
            for (int k = args.length - 1; k >= 0; k--) pre.add(new VarInsnNode(args[k].getOpcode(Opcodes.ISTORE), argLocal[k]));
        }
        if (recvAfter) {
            pre.add(new InsnNode(Opcodes.DUP));
            pre.add(new VarInsnNode(Opcodes.ASTORE, recvLocal));
        }
        if (recvHook) {
            pre.add(new InsnNode(Opcodes.DUP));
            pre.add(push(id));
            pre.add(hook("site", "(Ljava/lang/Object;I)V"));
        }
        if (noRecvHook) {
            pre.add(push(id));
            pre.add(hook("siteNoReceiver", "(I)V"));
        }
        for (Spec.RootCause rc : tagRecv) {
            pre.add(new InsnNode(Opcodes.DUP));
            pre.add(push(id));
            pre.add(tagHook(rc));
        }
        for (Spec.RootCause rc : tagArgs) {
            pre.add(new VarInsnNode(Opcodes.ALOAD, argLocal[rc.tagArg]));
            pre.add(push(id));
            pre.add(tagHook(rc));
        }
        if (spill) {
            for (int k = 0; k < args.length; k++) pre.add(new VarInsnNode(args[k].getOpcode(Opcodes.ILOAD), argLocal[k]));
        }

        InsnList post = new InsnList();
        for (Spec.RootCause rc : tagRet) {
            post.add(new InsnNode(Opcodes.DUP));
            post.add(push(id));
            post.add(tagHook(rc));
        }
        for (Spec.Propagation p : props) {
            post.add(new InsnNode(Opcodes.DUP));
            post.add(new VarInsnNode(Opcodes.ALOAD, p.from < 0 ? recvLocal : argLocal[p.from]));
            post.add(push(p.toElements ? 1 : 0));
            post.add(hook("propagate", "(Ljava/lang/Object;Ljava/lang/Object;I)V"));
        }
        if (needOps) {
            // Tracker.rc(result, {receiver, arg0, ...}, site) decides the runtime conditions and acts.
            post.add(new InsnNode(retRef ? Opcodes.DUP : Opcodes.ACONST_NULL));
            post.add(push(args.length + 1));
            post.add(new TypeInsnNode(Opcodes.ANEWARRAY, "java/lang/Object"));
            for (int k = -1; k < args.length; k++) {
                post.add(new InsnNode(Opcodes.DUP));
                post.add(push(k + 1));
                if (k < 0) post.add(hasRecv ? new VarInsnNode(Opcodes.ALOAD, recvLocal) : new InsnNode(Opcodes.ACONST_NULL));
                else post.add(isRef(args[k]) ? new VarInsnNode(Opcodes.ALOAD, argLocal[k]) : new InsnNode(Opcodes.ACONST_NULL));
                post.add(new InsnNode(Opcodes.AASTORE));
            }
            post.add(push(id));
            post.add(hook("rc", "(Ljava/lang/Object;[Ljava/lang/Object;I)V"));
        }

        if (pre.size() == 0 && post.size() == 0) return false;
        if (pre.size() > 0) mn.instructions.insertBefore(m, pre);
        if (post.size() > 0) mn.instructions.insert(m, post);
        return true;
    }

    // ── Field instructions ────────────────────────────────────────────────────

    private boolean field(String owner, MethodNode mn, FieldInsnNode f, int ordinal, int line, Counters c,
                          Provenance prov) {
        if (!isRef(Type.getType(f.desc))) return false;
        int op = f.getOpcode();
        boolean get = op == Opcodes.GETFIELD || op == Opcodes.GETSTATIC;
        String fieldOwner = Type.getObjectType(f.owner).getClassName();
        Spec.RootCause match = null;
        for (Spec.RootCause rc : spec.rootCauses) {
            if (!rc.at.equals("field")) continue;
            boolean accessOk = rc.access.equals("any") || rc.access.equals(get ? "get" : "put");
            if (accessOk && rc.owner.matches(fieldOwner) && rc.name.matches(f.name)
                    && Spec.Glob.matches(rc.desc, f.desc) && rc.matchesEnclosing(owner, mn.name)) {
                if (!staticConditionsHold(rc, prov, f, cond -> get ? -1 : 0)) {
                    c.filtered(rc.id);
                    continue;
                }
                match = rc;
                break;
            }
        }
        if (match == null) return false;
        Sites.Site s = site(owner, mn, ordinal, line, op);
        s.calleeOwner = fieldOwner;
        s.calleeName = f.name;
        s.calleeDesc = f.desc;
        s.rootCause = match;
        boolean runtime = !match.runtimeConditions.isEmpty();
        if (runtime) s.conditionalRules.add(match);
        int id = Sites.add(s);
        c.rc(match.id);
        Tracker.log("root cause site " + match.id + " at " + s.rcKey() + " (field " + fieldOwner + "." + f.name + ")");
        InsnList hook = new InsnList();
        hook.add(new InsnNode(Opcodes.DUP));
        if (runtime) {
            hook.add(new InsnNode(Opcodes.ACONST_NULL));
            hook.add(push(id));
            hook.add(hook("rc", "(Ljava/lang/Object;[Ljava/lang/Object;I)V"));
        } else {
            hook.add(push(id));
            hook.add(tagHook(match));
        }
        if (get) mn.instructions.insert(f, hook);
        else mn.instructions.insertBefore(f, hook);
        return true;
    }

    // ── Exception handlers (implicit-exception root causes) ───────────────────

    private boolean handlerHooks(MethodNode mn, Counters c) {
        Set<LabelNode> handlers = new LinkedHashSet<>();
        for (TryCatchBlockNode tcb : mn.tryCatchBlocks) handlers.add(tcb.handler);
        for (LabelNode h : handlers) {
            // Insert after the label and its frame, so the frame still describes the handler entry.
            AbstractInsnNode at = h;
            while (at.getNext() != null && (at.getNext() instanceof FrameNode
                    || at.getNext() instanceof LineNumberNode || at.getNext() instanceof LabelNode)) {
                at = at.getNext();
            }
            InsnList hook = new InsnList();
            hook.add(new InsnNode(Opcodes.DUP));
            hook.add(hook("caught", "(Ljava/lang/Throwable;)V"));
            mn.instructions.insert(at, hook);
            c.handlerHooks++;
        }
        return !handlers.isEmpty();
    }

    // ── Bytecode helpers ──────────────────────────────────────────────────────

    private static MethodInsnNode hook(String name, String desc) {
        return new MethodInsnNode(Opcodes.INVOKESTATIC, TRACKER, name, desc, false);
    }

    /** Expects (value, site id) on the stack; adds the rule index and calls tag/tagElements. */
    private static InsnList tagHook(Spec.RootCause rc) {
        InsnList l = new InsnList();
        l.add(push(rc.index));
        l.add(hook(rc.tagElements ? "tagElements" : "tag", "(Ljava/lang/Object;II)V"));
        return l;
    }

    private static AbstractInsnNode push(int v) {
        if (v >= -1 && v <= 5) return new InsnNode(Opcodes.ICONST_0 + v);
        if (v >= Byte.MIN_VALUE && v <= Byte.MAX_VALUE) return new IntInsnNode(Opcodes.BIPUSH, v);
        if (v >= Short.MIN_VALUE && v <= Short.MAX_VALUE) return new IntInsnNode(Opcodes.SIPUSH, v);
        return new LdcInsnNode(v);
    }
}
