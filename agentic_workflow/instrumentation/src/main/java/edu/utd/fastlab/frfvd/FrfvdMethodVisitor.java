package edu.utd.fastlab.frfvd;

import org.objectweb.asm.*;
import org.objectweb.asm.commons.LocalVariablesSorter;

import java.io.*;
import java.util.*;

/**
 * Detects the FinalReferenceFieldVirtualDispatch (FRFVD) pattern and injects
 * tracking calls at matched sites.
 *
 * Strategy — collect-then-replay:
 *
 *   Pass 1 (visit* methods): buffer every bytecode instruction as a BufEntry
 *   lambda that closes over its instruction index; simultaneously run a simplified
 *   operand-stack simulation to identify matched (GETSTATIC_idx, INVOKEVIRTUAL_idx)
 *   pairs.
 *
 *   Pass 2 (visitEnd): replay the buffer to the real delegate. Each lambda already
 *   captured its instrIdx at collection time, so it knows whether to inject tracker
 *   calls inline — no second analysis pass needed.
 *
 *   Injection points:
 *     RC site  (post-GETSTATIC):      DUP value  → Tracker.onFinalFieldLoad(...)
 *     Sym site (pre-INVOKEVIRTUAL):   spill args → DUP receiver →
 *                                     Tracker.onVirtualDispatch(...) → restore args
 */
final class FrfvdMethodVisitor extends MethodVisitor {

    private static final String TRACKER = "edu/utd/fastlab/frfvd/FrfvdTracker";

    // Sentinel for "unknown / not a final-field GETSTATIC result".
    // Java 8's ArrayDeque forbids null elements, so we use -1 instead of null.
    private static final int UNKNOWN = -1;

    private final int access;
    private final String fqMethod;
    private final String methodDesc;
    private final ClassLoader loader;
    private final MethodVisitor realDelegate;

    /** Buffer entry — a lambda that emits one or more instructions to the delegate. */
    interface BufEntry {
        void emit(LocalVariablesSorter lvs);
    }

    private final List<BufEntry> buf = new ArrayList<BufEntry>();

    // Simplified operand-stack simulation.
    // Slot value: UNKNOWN (-1) = unknown value;
    //             N >= 0       = produced by GETSTATIC at instrIdx N.
    // NOTE: ArrayDeque in Java 8 does not allow null — always use UNKNOWN, never null.
    private final Deque<Integer> sim = new ArrayDeque<Integer>();

    // RC sites:  instrIdx of GETSTATIC  →  field metadata
    private final Map<Integer, FieldRef> rcAt = new HashMap<Integer, FieldRef>();
    // Sym sites: instrIdx of INVOKEVIRTUAL  →  instrIdx of the matching GETSTATIC
    private final Map<Integer, Integer> symAt = new HashMap<Integer, Integer>();

    // Counts only real bytecode instructions (not labels/frames/lineNumbers)
    private int instrIdx = 0;

    private int savedMaxStack, savedMaxLocals;

    // ===========================================================================
    // Constructor
    // ===========================================================================

    FrfvdMethodVisitor(MethodVisitor delegate, int access,
                       String owner, String name, String desc, ClassLoader loader) {
        super(Opcodes.ASM9, null);  // null → no automatic forwarding during collection
        this.realDelegate = delegate;
        this.access = access;
        this.fqMethod = "L" + owner + ";." + name + ":" + desc;
        this.methodDesc = desc;
        this.loader = loader;
    }

    // ===========================================================================
    // Non-bytecode metadata — forward immediately
    // ===========================================================================

    @Override public void visitCode() { realDelegate.visitCode(); }

    @Override
    public void visitTryCatchBlock(Label s, Label e, Label h, String type) {
        realDelegate.visitTryCatchBlock(s, e, h, type);
    }

    @Override
    public void visitLocalVariable(String nm, String d, String sig,
                                   Label start, Label end, int index) {
        realDelegate.visitLocalVariable(nm, d, sig, start, end, index);
    }

    @Override
    public AnnotationVisitor visitAnnotation(String d, boolean visible) {
        return realDelegate.visitAnnotation(d, visible);
    }

    @Override
    public AnnotationVisitor visitParameterAnnotation(int param, String d, boolean visible) {
        return realDelegate.visitParameterAnnotation(param, d, visible);
    }

    // ===========================================================================
    // Non-instruction bytecode metadata — buffer but don't increment instrIdx
    // ===========================================================================

    @Override
    public void visitLabel(final Label label) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitLabel(label);
        }});
        sim.clear();   // conservative: any label may be a branch target
    }

    @Override
    public void visitFrame(final int type, final int nl,
                           final Object[] local, final int ns, final Object[] stack) {
        final Object[] lClone = local == null ? null : local.clone();
        final Object[] sClone = stack == null ? null : stack.clone();
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitFrame(type, nl, lClone, ns, sClone);
        }});
        sim.clear();
    }

    @Override
    public void visitLineNumber(final int line, final Label start) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitLineNumber(line, start);
        }});
    }

    // ===========================================================================
    // Bytecode instructions — buffer + simulate stack + increment instrIdx
    // ===========================================================================

    @Override
    public void visitInsn(final int opcode) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitInsn(opcode);
        }});
        simInsn(opcode);
        instrIdx++;
    }

    @Override
    public void visitVarInsn(final int opcode, final int var) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitVarInsn(opcode, var);
        }});
        simVar(opcode);
        instrIdx++;
    }

    @Override
    public void visitTypeInsn(final int opcode, final String type) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitTypeInsn(opcode, type);
        }});
        simType(opcode);
        instrIdx++;
    }

    @Override
    public void visitFieldInsn(final int opcode,
                                final String owner, final String name, final String desc) {
        final int idx = instrIdx++;
        if (opcode == Opcodes.GETSTATIC && isFinalRefField(owner, name, desc)) {
            rcAt.put(idx, new FieldRef(owner, name, desc));
            simPush(idx);
            buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
                lvs.visitFieldInsn(opcode, owner, name, desc);
                injectPostLoad(lvs, rcAt.get(idx), idx);
            }});
        } else {
            simField(opcode, desc);
            buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
                lvs.visitFieldInsn(opcode, owner, name, desc);
            }});
        }
    }

    @Override
    public void visitMethodInsn(final int opcode,
                                 final String owner, final String name,
                                 final String desc, final boolean isInterface) {
        final int idx = instrIdx++;

        // Pattern detection BEFORE stack update so receiver is still on top
        if (opcode == Opcodes.INVOKEVIRTUAL || opcode == Opcodes.INVOKEINTERFACE) {
            int argSlots = argSlotCount(Type.getArgumentTypes(desc));
            List<Integer> snapshot = new ArrayList<Integer>(sim);
            int rcSlot = snapshot.size() - 1 - argSlots;
            if (rcSlot >= 0 && snapshot.get(rcSlot) != UNKNOWN) {
                symAt.put(idx, snapshot.get(rcSlot));
            }
        }
        simMethod(opcode, desc);

        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            if ((opcode == Opcodes.INVOKEVIRTUAL || opcode == Opcodes.INVOKEINTERFACE)
                    && symAt.containsKey(idx)) {
                injectPreDispatch(lvs, name, desc, idx);
            }
            lvs.visitMethodInsn(opcode, owner, name, desc, isInterface);
        }});
    }

    @Override
    public void visitLdcInsn(final Object cst) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitLdcInsn(cst);
        }});
        if (cst instanceof Long || cst instanceof Double) { simPush(UNKNOWN); simPush(UNKNOWN); }
        else simPush(UNKNOWN);
        instrIdx++;
    }

    @Override
    public void visitIntInsn(final int opcode, final int operand) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitIntInsn(opcode, operand);
        }});
        simPush(UNKNOWN);
        instrIdx++;
    }

    @Override
    public void visitIincInsn(final int var, final int incr) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitIincInsn(var, incr);
        }});
        instrIdx++;
    }

    @Override
    public void visitJumpInsn(final int opcode, final Label label) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitJumpInsn(opcode, label);
        }});
        switch (opcode) {
            case Opcodes.IFEQ: case Opcodes.IFNE: case Opcodes.IFLT:
            case Opcodes.IFGE: case Opcodes.IFGT: case Opcodes.IFLE:
            case Opcodes.IFNULL: case Opcodes.IFNONNULL:
                simPop(); break;
            case Opcodes.IF_ICMPEQ: case Opcodes.IF_ICMPNE: case Opcodes.IF_ICMPLT:
            case Opcodes.IF_ICMPGE: case Opcodes.IF_ICMPGT: case Opcodes.IF_ICMPLE:
            case Opcodes.IF_ACMPEQ: case Opcodes.IF_ACMPNE:
                simPop(); simPop(); break;
            default: break;
        }
        sim.clear();
        instrIdx++;
    }

    @Override
    public void visitMultiANewArrayInsn(final String desc, final int dims) {
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitMultiANewArrayInsn(desc, dims);
        }});
        for (int i = 0; i < dims; i++) simPop();
        simPush(UNKNOWN);
        instrIdx++;
    }

    @Override
    public void visitTableSwitchInsn(final int min, final int max,
                                      final Label dflt, final Label... labels) {
        final Label[] lClone = labels.clone();
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitTableSwitchInsn(min, max, dflt, lClone);
        }});
        simPop(); sim.clear();
        instrIdx++;
    }

    @Override
    public void visitLookupSwitchInsn(final Label dflt,
                                       final int[] keys, final Label[] labels) {
        final int[] kClone = keys.clone();
        final Label[] lClone = labels.clone();
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitLookupSwitchInsn(dflt, kClone, lClone);
        }});
        simPop(); sim.clear();
        instrIdx++;
    }

    @Override
    public void visitInvokeDynamicInsn(final String name, final String desc,
                                        final Handle bsm, final Object... bsmArgs) {
        final Object[] aClone = bsmArgs.clone();
        buf.add(new BufEntry() { public void emit(LocalVariablesSorter lvs) {
            lvs.visitInvokeDynamicInsn(name, desc, bsm, aClone);
        }});
        simMethod(Opcodes.INVOKESTATIC, desc);
        instrIdx++;
    }

    @Override
    public void visitMaxs(int maxStack, int maxLocals) {
        savedMaxStack = maxStack;
        savedMaxLocals = maxLocals;
    }

    // ===========================================================================
    // Pass 2 — replay
    // ===========================================================================

    @Override
    public void visitEnd() {
        LocalVariablesSorter lvs = new LocalVariablesSorter(access, methodDesc, realDelegate);
        for (BufEntry entry : buf) entry.emit(lvs);
        lvs.visitMaxs(savedMaxStack + 20, savedMaxLocals + 20);
        lvs.visitEnd();
    }

    // ===========================================================================
    // Injection helpers
    // ===========================================================================

    private void injectPostLoad(LocalVariablesSorter lvs, FieldRef fi, int rcIdx) {
        // Stack: ..., fieldValue
        lvs.visitInsn(Opcodes.DUP);
        lvs.visitLdcInsn(fqMethod);
        lvs.visitLdcInsn(rcIdx);
        lvs.visitLdcInsn(fi.owner);
        lvs.visitLdcInsn(fi.name);
        lvs.visitLdcInsn(fi.desc);
        lvs.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER, "onFinalFieldLoad",
                "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V",
                false);
        // Stack: ..., fieldValue  (dup consumed by tracker)
    }

    private void injectPreDispatch(LocalVariablesSorter lvs, String calleeName,
                                   String calleeDesc, int symIdx) {
        // Stack: ..., receiver, arg1, ..., argN
        Type[] argTypes = Type.getArgumentTypes(calleeDesc);
        int[] argLocals = new int[argTypes.length];
        for (int i = argTypes.length - 1; i >= 0; i--) {
            argLocals[i] = lvs.newLocal(argTypes[i]);
            lvs.visitVarInsn(argTypes[i].getOpcode(Opcodes.ISTORE), argLocals[i]);
        }
        // Stack: ..., receiver
        lvs.visitInsn(Opcodes.DUP);
        lvs.visitLdcInsn(fqMethod);
        lvs.visitLdcInsn(symIdx);
        lvs.visitLdcInsn(calleeName);
        lvs.visitLdcInsn(calleeDesc);
        lvs.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER, "onVirtualDispatch",
                "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)V",
                false);
        for (int i = 0; i < argTypes.length; i++) {
            lvs.visitVarInsn(argTypes[i].getOpcode(Opcodes.ILOAD), argLocals[i]);
        }
        // Stack: ..., receiver, arg1, ..., argN  — ready for INVOKEVIRTUAL
    }

    // ===========================================================================
    // Simplified operand-stack simulation
    // ===========================================================================

    private void simPush(int marker) { sim.addLast(marker); }
    private int simPop() { return sim.isEmpty() ? UNKNOWN : sim.removeLast(); }

    private void simInsn(int op) {
        switch (op) {
            // --- push one null slot ---
            case Opcodes.ACONST_NULL:
            case Opcodes.ICONST_M1: case Opcodes.ICONST_0: case Opcodes.ICONST_1:
            case Opcodes.ICONST_2:  case Opcodes.ICONST_3: case Opcodes.ICONST_4:
            case Opcodes.ICONST_5:
            case Opcodes.FCONST_0: case Opcodes.FCONST_1: case Opcodes.FCONST_2:
                simPush(UNKNOWN); break;

            // --- push two null slots (wide) ---
            case Opcodes.LCONST_0: case Opcodes.LCONST_1:
            case Opcodes.DCONST_0: case Opcodes.DCONST_1:
                simPush(UNKNOWN); simPush(UNKNOWN); break;

            // --- array load narrow: pop index + ref, push value ---
            case Opcodes.IALOAD: case Opcodes.FALOAD: case Opcodes.AALOAD:
            case Opcodes.BALOAD: case Opcodes.CALOAD: case Opcodes.SALOAD:
                simPop(); simPop(); simPush(UNKNOWN); break;

            // --- array load wide ---
            case Opcodes.LALOAD: case Opcodes.DALOAD:
                simPop(); simPop(); simPush(UNKNOWN); simPush(UNKNOWN); break;

            // --- array store narrow: pop value + index + ref ---
            case Opcodes.IASTORE: case Opcodes.FASTORE: case Opcodes.AASTORE:
            case Opcodes.BASTORE: case Opcodes.CASTORE: case Opcodes.SASTORE:
                simPop(); simPop(); simPop(); break;

            // --- array store wide ---
            case Opcodes.LASTORE: case Opcodes.DASTORE:
                simPop(); simPop(); simPop(); simPop(); break;

            case Opcodes.POP:  simPop(); break;
            case Opcodes.POP2: simPop(); simPop(); break;

            case Opcodes.DUP:
                simPush(sim.isEmpty() ? UNKNOWN : sim.peekLast()); break;

            case Opcodes.DUP_X1: {
                Integer a = simPop(), b = simPop();
                simPush(a); simPush(b); simPush(a); break;
            }
            case Opcodes.SWAP: {
                Integer a = simPop(), b = simPop();
                simPush(a); simPush(b); break;
            }

            // --- binary narrow: 2 → 1 ---
            case Opcodes.IADD: case Opcodes.ISUB: case Opcodes.IMUL: case Opcodes.IDIV:
            case Opcodes.IREM: case Opcodes.IAND: case Opcodes.IOR:  case Opcodes.IXOR:
            case Opcodes.ISHL: case Opcodes.ISHR: case Opcodes.IUSHR:
            case Opcodes.FADD: case Opcodes.FSUB: case Opcodes.FMUL: case Opcodes.FDIV:
            case Opcodes.FREM: case Opcodes.LCMP:
            case Opcodes.FCMPL: case Opcodes.FCMPG:
            case Opcodes.DCMPL: case Opcodes.DCMPG:
                simPop(); simPop(); simPush(UNKNOWN); break;

            // --- binary wide: 4 → 2 ---
            case Opcodes.LADD: case Opcodes.LSUB: case Opcodes.LMUL: case Opcodes.LDIV:
            case Opcodes.LREM: case Opcodes.LAND: case Opcodes.LOR:  case Opcodes.LXOR:
            case Opcodes.DADD: case Opcodes.DSUB: case Opcodes.DMUL: case Opcodes.DDIV:
            case Opcodes.DREM:
                simPop(); simPop(); simPop(); simPop(); simPush(UNKNOWN); simPush(UNKNOWN); break;

            // --- unary narrow ---
            case Opcodes.INEG: case Opcodes.FNEG:
                simPop(); simPush(UNKNOWN); break;

            // --- unary wide ---
            case Opcodes.LNEG: case Opcodes.DNEG:
                simPop(); simPop(); simPush(UNKNOWN); simPush(UNKNOWN); break;

            // --- widening conversions: 1 → 2 ---
            case Opcodes.I2L: case Opcodes.I2D: case Opcodes.F2L: case Opcodes.F2D:
                simPop(); simPush(UNKNOWN); simPush(UNKNOWN); break;

            // --- narrowing conversions: 2 → 1 ---
            case Opcodes.L2I: case Opcodes.L2F: case Opcodes.D2I: case Opcodes.D2F:
                simPop(); simPop(); simPush(UNKNOWN); break;

            // --- same-width conversions: 1 → 1 ---
            case Opcodes.I2B: case Opcodes.I2C: case Opcodes.I2S:
            case Opcodes.I2F: case Opcodes.F2I:
            case Opcodes.L2D: case Opcodes.D2L:
                simPop(); simPush(UNKNOWN); break;

            // --- returns ---
            case Opcodes.IRETURN: case Opcodes.FRETURN: case Opcodes.ARETURN:
                simPop(); break;
            case Opcodes.LRETURN: case Opcodes.DRETURN:
                simPop(); simPop(); break;
            case Opcodes.RETURN: break;

            case Opcodes.ATHROW:       simPop(); break;
            case Opcodes.ARRAYLENGTH:  simPop(); simPush(UNKNOWN); break;
            case Opcodes.MONITORENTER: case Opcodes.MONITOREXIT: simPop(); break;

            default: break;
        }
    }

    private void simVar(int op) {
        switch (op) {
            case Opcodes.ILOAD: case Opcodes.FLOAD: case Opcodes.ALOAD:
                simPush(UNKNOWN); break;
            case Opcodes.LLOAD: case Opcodes.DLOAD:
                simPush(UNKNOWN); simPush(UNKNOWN); break;
            case Opcodes.ISTORE: case Opcodes.FSTORE: case Opcodes.ASTORE:
                simPop(); break;
            case Opcodes.LSTORE: case Opcodes.DSTORE:
                simPop(); simPop(); break;
            default: break;
        }
    }

    private void simType(int op) {
        switch (op) {
            case Opcodes.NEW:        simPush(UNKNOWN); break;
            case Opcodes.ANEWARRAY:  simPop(); simPush(UNKNOWN); break;
            case Opcodes.INSTANCEOF: simPop(); simPush(UNKNOWN); break;
            case Opcodes.CHECKCAST:  break;   // stack unchanged
            default: break;
        }
    }

    private void simField(int op, String desc) {
        boolean wide = "J".equals(desc) || "D".equals(desc);
        switch (op) {
            case Opcodes.GETSTATIC:
                simPush(UNKNOWN); if (wide) simPush(UNKNOWN); break;
            case Opcodes.PUTSTATIC:
                simPop(); if (wide) simPop(); break;
            case Opcodes.GETFIELD:
                simPop(); simPush(UNKNOWN); if (wide) simPush(UNKNOWN); break;
            case Opcodes.PUTFIELD:
                simPop(); if (wide) simPop(); simPop(); break;
            default: break;
        }
    }

    private void simMethod(int op, String desc) {
        for (Type t : Type.getArgumentTypes(desc)) {
            simPop();
            if (t.getSort() == Type.LONG || t.getSort() == Type.DOUBLE) simPop();
        }
        if (op != Opcodes.INVOKESTATIC) simPop();  // receiver
        Type ret = Type.getReturnType(desc);
        if (ret.getSort() != Type.VOID) {
            simPush(UNKNOWN);
            if (ret.getSort() == Type.LONG || ret.getSort() == Type.DOUBLE) simPush(UNKNOWN);
        }
    }

    // ===========================================================================
    // Field predicate helpers
    // ===========================================================================

    private boolean isFinalRefField(String owner, String name, String desc) {
        if (!desc.startsWith("L") && !desc.startsWith("[")) return false;
        if (!isLibrary(owner)) return false;
        return checkStaticFinal(owner, name, desc);
    }

    private static boolean isLibrary(String owner) {
        return owner.startsWith("java/") || owner.startsWith("javax/")
                || owner.startsWith("sun/") || owner.startsWith("com/sun/")
                || owner.startsWith("jdk/");
    }

    private boolean checkStaticFinal(String owner, String name, String desc) {
        InputStream is = null;
        try {
            // Java 8: getSystemResourceAsStream searches bootstrap classpath (rt.jar)
            is = ClassLoader.getSystemResourceAsStream(owner + ".class");
            if (is == null && loader != null)
                is = loader.getResourceAsStream(owner + ".class");
            if (is == null) return true;   // unresolvable library class → assume final
            ClassReader cr = new ClassReader(is);
            final int[] flags = {0};
            cr.accept(new ClassVisitor(Opcodes.ASM9) {
                @Override
                public FieldVisitor visitField(int acc, String n, String d,
                                               String sig, Object val) {
                    if (n != null && n.equals(name) && d != null && d.equals(desc))
                        flags[0] = acc;
                    return null;
                }
            }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
            return (flags[0] & (Opcodes.ACC_STATIC | Opcodes.ACC_FINAL))
                    == (Opcodes.ACC_STATIC | Opcodes.ACC_FINAL);
        } catch (Exception e) {
            // Catch all (not just IOException): a malformed class file or unexpected
            // runtime error here must not escape and poison the transform() call.
            return false;
        } finally {
            if (is != null) { try { is.close(); } catch (IOException ignored) {} }
        }
    }

    private static int argSlotCount(Type[] args) {
        int n = 0;
        for (Type t : args) {
            n += (t.getSort() == Type.LONG || t.getSort() == Type.DOUBLE) ? 2 : 1;
        }
        return n;
    }

    // ===========================================================================
    // Data carrier (replaces Java 16 record)
    // ===========================================================================

    static final class FieldRef {
        final String owner, name, desc;
        FieldRef(String owner, String name, String desc) {
            this.owner = owner; this.name = name; this.desc = desc;
        }
    }
}
