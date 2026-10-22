package edu.utd.fastlab.cgrizz.oracle;

import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.FieldInsnNode;
import org.objectweb.asm.tree.InsnNode;
import org.objectweb.asm.tree.IntInsnNode;
import org.objectweb.asm.tree.InvokeDynamicInsnNode;
import org.objectweb.asm.tree.LdcInsnNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;
import org.objectweb.asm.tree.TypeInsnNode;
import org.objectweb.asm.tree.VarInsnNode;
import org.objectweb.asm.tree.analysis.Analyzer;
import org.objectweb.asm.tree.analysis.Frame;
import org.objectweb.asm.tree.analysis.SourceInterpreter;
import org.objectweb.asm.tree.analysis.SourceValue;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Where an operand of an instruction comes from, within its method: the
 * instructions that produced it, following loads/stores of locals, DUPs and
 * CHECKCASTs (ASM SourceInterpreter). This is how the SA tools decide things
 * like "the class name is a string constant", so load-time conditions of a
 * root cause are decided the same way.
 *
 * Must be built from the method before anything is inserted into it.
 */
final class Provenance {

    enum Kind { CONSTANT, CLASS_LITERAL, PARAMETER, FIELD, CALL, NEW, OTHER }

    static final class Origin {
        final Kind kind;
        final AbstractInsnNode insn;   // null for PARAMETER
        final String type;             // static type (dotted) where known, else null

        Origin(Kind kind, AbstractInsnNode insn, String type) {
            this.kind = kind;
            this.insn = insn;
            this.type = type;
        }
    }

    private final Map<AbstractInsnNode, Frame<SourceValue>> frames = new IdentityHashMap<>();
    private final Type[] localTypes;   // parameter types by local index (incl. this)

    private Provenance(String owner, MethodNode mn) throws Exception {
        Frame<SourceValue>[] fs = new Analyzer<>(new SourceInterpreter()).analyze(owner, mn);
        AbstractInsnNode[] insns = mn.instructions.toArray();
        for (int i = 0; i < insns.length; i++) {
            if (fs[i] != null) frames.put(insns[i], fs[i]);
        }
        localTypes = new Type[Math.max(mn.maxLocals, 1)];
        int slot = 0;
        if ((mn.access & Opcodes.ACC_STATIC) == 0) localTypes[slot++] = Type.getObjectType(owner);
        for (Type t : Type.getArgumentTypes(mn.desc)) {
            if (slot < localTypes.length) localTypes[slot] = t;
            slot += t.getSize();
        }
    }

    /** null if the method cannot be analysed (conditions are then treated as satisfied). */
    static Provenance of(String owner, MethodNode mn) {
        try {
            return new Provenance(owner, mn);
        } catch (Throwable t) {
            Tracker.log("provenance analysis failed for " + owner + "." + mn.name + ": " + t);
            return null;
        }
    }

    /**
     * Origins of the stack value at depth {@code depth} (0 = top) just before
     * {@code at}; null if unknown (unreachable code).
     */
    List<Origin> origins(AbstractInsnNode at, int depth) {
        Frame<SourceValue> f = frames.get(at);
        if (f == null || depth >= f.getStackSize()) return null;
        List<Origin> out = new ArrayList<>();
        resolve(f.getStack(f.getStackSize() - 1 - depth), out, new HashSet<>(), null);
        return out;
    }

    private void resolve(SourceValue v, List<Origin> out, Set<AbstractInsnNode> seen, String castType) {
        for (AbstractInsnNode insn : v.insns) {
            if (!seen.add(insn)) continue;
            int op = insn.getOpcode();
            Frame<SourceValue> f = frames.get(insn);
            if (insn instanceof VarInsnNode && op >= Opcodes.ILOAD && op <= Opcodes.ALOAD && f != null) {
                int var = ((VarInsnNode) insn).var;
                SourceValue local = f.getLocal(var);
                if (local.insns.isEmpty()) {
                    Type t = var < localTypes.length ? localTypes[var] : null;
                    out.add(new Origin(Kind.PARAMETER, null, castType != null ? castType : t == null ? null : t.getClassName()));
                } else {
                    resolve(local, out, seen, castType);
                }
            } else if (insn instanceof VarInsnNode && op >= Opcodes.ISTORE && op <= Opcodes.ASTORE && f != null) {
                resolve(f.getStack(f.getStackSize() - 1), out, seen, castType);
            } else if (op >= Opcodes.DUP && op <= Opcodes.SWAP && f != null) {
                // Frame.execute gives copies made by DUP*/SWAP the DUP as source: look through it.
                int n = (op == Opcodes.DUP || op == Opcodes.DUP_X1 || op == Opcodes.DUP_X2) ? 1 : 2;
                for (int k = 0; k < n && k < f.getStackSize(); k++) {
                    resolve(f.getStack(f.getStackSize() - 1 - k), out, seen, castType);
                }
            } else if (op == Opcodes.CHECKCAST && f != null) {
                String t = Type.getObjectType(((TypeInsnNode) insn).desc).getClassName();
                resolve(f.getStack(f.getStackSize() - 1), out, seen, castType != null ? castType : t);
            } else {
                out.add(classify(insn, castType));
            }
        }
    }

    private static Origin classify(AbstractInsnNode insn, String castType) {
        int op = insn.getOpcode();
        if (insn instanceof LdcInsnNode) {
            Object cst = ((LdcInsnNode) insn).cst;
            if (cst instanceof Type && (((Type) cst).getSort() == Type.OBJECT || ((Type) cst).getSort() == Type.ARRAY)) {
                return new Origin(Kind.CLASS_LITERAL, insn, "java.lang.Class");
            }
            String t = cst instanceof String ? "java.lang.String" : null;
            return new Origin(Kind.CONSTANT, insn, castType != null ? castType : t);
        }
        if ((insn instanceof InsnNode && op >= Opcodes.ACONST_NULL && op <= Opcodes.DCONST_1)
                || (insn instanceof IntInsnNode && (op == Opcodes.BIPUSH || op == Opcodes.SIPUSH))) {
            return new Origin(Kind.CONSTANT, insn, castType);
        }
        if (insn instanceof FieldInsnNode && (op == Opcodes.GETFIELD || op == Opcodes.GETSTATIC)) {
            String t = Type.getType(((FieldInsnNode) insn).desc).getClassName();
            return new Origin(Kind.FIELD, insn, castType != null ? castType : t);
        }
        if (insn instanceof MethodInsnNode) {
            String t = Type.getReturnType(((MethodInsnNode) insn).desc).getClassName();
            return new Origin(Kind.CALL, insn, castType != null ? castType : t);
        }
        if (insn instanceof InvokeDynamicInsnNode) {
            String t = Type.getReturnType(((InvokeDynamicInsnNode) insn).desc).getClassName();
            return new Origin(Kind.CALL, insn, castType != null ? castType : t);
        }
        if (insn instanceof TypeInsnNode && (op == Opcodes.NEW || op == Opcodes.ANEWARRAY)) {
            String t = op == Opcodes.NEW ? Type.getObjectType(((TypeInsnNode) insn).desc).getClassName() : null;
            return new Origin(Kind.NEW, insn, castType != null ? castType : t);
        }
        return new Origin(Kind.OTHER, insn, castType);
    }

    /** Decide a load-time condition; null origins (unknown) count as satisfied. */
    static boolean holds(Spec.Condition c, List<Origin> origins) {
        if (origins == null || origins.isEmpty()) return true;
        boolean r;
        switch (c.is) {
            case "constant":
                r = all(origins, o -> o.kind == Kind.CONSTANT || o.kind == Kind.CLASS_LITERAL);
                break;
            case "nonConstant":
                r = !all(origins, o -> o.kind == Kind.CONSTANT || o.kind == Kind.CLASS_LITERAL);
                break;
            case "classLiteral":
                r = all(origins, o -> o.kind == Kind.CLASS_LITERAL);
                break;
            case "fromParameter":
                r = any(origins, o -> o.kind == Kind.PARAMETER);
                break;
            case "fromField":
                r = any(origins, o -> o.kind == Kind.FIELD && memberMatches(c, ((FieldInsnNode) o.insn).owner, ((FieldInsnNode) o.insn).name));
                break;
            case "fromCall":
                r = any(origins, o -> o.kind == Kind.CALL && o.insn instanceof MethodInsnNode
                        && memberMatches(c, ((MethodInsnNode) o.insn).owner, ((MethodInsnNode) o.insn).name));
                break;
            case "fromNew":
                r = any(origins, o -> o.kind == Kind.NEW && o.type != null && c.type.matches(o.type));
                break;
            case "staticType":
                r = all(origins, o -> o.type != null && c.type.matches(o.type));
                break;
            default:
                r = true;
        }
        return c.not != r;
    }

    private static boolean memberMatches(Spec.Condition c, String internalOwner, String name) {
        return Spec.Glob.matches(c.owner, Type.getObjectType(internalOwner).getClassName()) && Spec.Glob.matches(c.name, name);
    }

    private interface P {
        boolean test(Origin o);
    }

    private static boolean all(List<Origin> os, P p) {
        for (Origin o : os) if (!p.test(o)) return false;
        return true;
    }

    private static boolean any(List<Origin> os, P p) {
        for (Origin o : os) if (p.test(o)) return true;
        return false;
    }
}
