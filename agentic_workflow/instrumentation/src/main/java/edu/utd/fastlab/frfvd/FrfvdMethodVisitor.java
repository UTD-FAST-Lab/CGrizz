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
 *   Pass 1 (visit* methods): buffer every bytecode instruction as a lambda that
 *   closes over its instruction index; simultaneously run a simplified operand-stack
 *   simulation to identify matched (GETSTATIC_idx, INVOKEVIRTUAL_idx) pairs.
 *
 *   Pass 2 (visitEnd): replay the buffer to the real delegate. Because each lambda
 *   captured its own idx at collection time, it can look up rcAt/symAt and inject
 *   tracker calls inline — no second analysis pass needed.
 *
 *   Injection points:
 *     • Post-GETSTATIC (RC site):
 *         DUP fieldValue → Tracker.onFinalFieldLoad(value, method, pc, ...)
 *     • Pre-INVOKEVIRTUAL (Sym site):
 *         spill args to fresh locals → DUP receiver →
 *         Tracker.onVirtualDispatch(recv, method, pc, ...) → restore args
 */
final class FrfvdMethodVisitor extends MethodVisitor {

    private static final String TRACKER = "edu/utd/fastlab/frfvd/FrfvdTracker";

    private final int access;
    private final String fqMethod;
    private final String methodDesc;
    private final ClassLoader loader;
    private final MethodVisitor realDelegate;

    // Buffer — each entry is a lambda(LocalVariablesSorter) that emits one or more
    // bytecode instructions.  The lambda closes over the instruction index so it can
    // decide whether to inject tracker calls at replay time.
    @FunctionalInterface
    interface BufEntry { void emit(LocalVariablesSorter lvs); }
    private final List<BufEntry> buf = new ArrayList<>();

    // Simplified operand-stack simulation.
    // Slot value: null = unknown value; Integer N = "value produced by GETSTATIC at instrIdx N"
    private final Deque<Integer> sim = new ArrayDeque<>();

    // RC sites:   instrIdx of GETSTATIC  →  field metadata
    private final Map<Integer, FieldRef> rcAt = new HashMap<>();
    // Sym sites:  instrIdx of INVOKEVIRTUAL  →  instrIdx of the matching GETSTATIC
    private final Map<Integer, Integer> symAt = new HashMap<>();

    // Counts only real bytecode instructions (not labels / frames / line numbers)
    private int instrIdx = 0;

    // Captured from visitMaxs; used when replaying
    private int savedMaxStack, savedMaxLocals;

    // ===========================================================================
    // Constructor
    // ===========================================================================

    FrfvdMethodVisitor(MethodVisitor delegate, int access,
                       String owner, String name, String desc, ClassLoader loader) {
        super(Opcodes.ASM9, null);   // null → no automatic forwarding during collection
        this.realDelegate = delegate;
        this.access = access;
        this.fqMethod = "L" + owner + ";." + name + ":" + desc;
        this.methodDesc = desc;
        this.loader = loader;
    }

    // ===========================================================================
    // Non-bytecode metadata — forward immediately (no buffering needed)
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
    // Non-instruction bytecode metadata — buffer (no instrIdx increment)
    // ===========================================================================

    @Override
    public void visitLabel(Label label) {
        buf.add(lvs -> lvs.visitLabel(label));
        sim.clear();   // conservative: any label may be a branch target
    }

    @Override
    public void visitFrame(int type, int nl, Object[] local, int ns, Object[] stack) {
        Object[] lClone = local == null ? null : local.clone();
        Object[] sClone = stack == null ? null : stack.clone();
        buf.add(lvs -> lvs.visitFrame(type, nl, lClone, ns, sClone));
        sim.clear();
    }

    @Override
    public void visitLineNumber(int line, Label start) {
        buf.add(lvs -> lvs.visitLineNumber(line, start));
    }

    // ===========================================================================
    // Bytecode instructions — buffer (capturing idx) + simulate stack
    // ===========================================================================

    @Override
    public void visitInsn(int opcode) {
        buf.add(lvs -> lvs.visitInsn(opcode));
        simInsn(opcode);
        instrIdx++;
    }

    @Override
    public void visitVarInsn(int opcode, int var) {
        buf.add(lvs -> lvs.visitVarInsn(opcode, var));
        simVar(opcode);
        instrIdx++;
    }

    @Override
    public void visitTypeInsn(int opcode, String type) {
        buf.add(lvs -> lvs.visitTypeInsn(opcode, type));
        simType(opcode);
        instrIdx++;
    }

    @Override
    public void visitFieldInsn(int opcode, String owner, String name, String desc) {
        int idx = instrIdx++;
        if (opcode == Opcodes.GETSTATIC && isFinalRefField(owner, name, desc)) {
            rcAt.put(idx, new FieldRef(owner, name, desc));
            simPush(idx);   // slot is marked with this GETSTATIC's index
            buf.add(lvs -> {
                lvs.visitFieldInsn(opcode, owner, name, desc);
                // POST-injection: DUP + onFinalFieldLoad(...)
                injectPostLoad(lvs, rcAt.get(idx), idx);
            });
        } else {
            simField(opcode, desc);
            buf.add(lvs -> lvs.visitFieldInsn(opcode, owner, name, desc));
        }
    }

    @Override
    public void visitMethodInsn(int opcode, String owner, String name,
                                 String desc, boolean isInterface) {
        int idx = instrIdx++;

        // Pattern detection (before stack update so receiver is still visible)
        if (opcode == Opcodes.INVOKEVIRTUAL || opcode == Opcodes.INVOKEINTERFACE) {
            int argSlots = argSlotCount(Type.getArgumentTypes(desc));
            List<Integer> snapshot = new ArrayList<>(sim);
            int rcSlot = snapshot.size() - 1 - argSlots;
            if (rcSlot >= 0 && snapshot.get(rcSlot) != null) {
                symAt.put(idx, snapshot.get(rcSlot));
            }
        }

        simMethod(opcode, desc);

        buf.add(lvs -> {
            // PRE-injection: spill args → DUP receiver → onVirtualDispatch(...)
            if ((opcode == Opcodes.INVOKEVIRTUAL || opcode == Opcodes.INVOKEINTERFACE)
                    && symAt.containsKey(idx)) {
                injectPreDispatch(lvs, name, desc, idx);
            }
            lvs.visitMethodInsn(opcode, owner, name, desc, isInterface);
        });
    }

    @Override
    public void visitLdcInsn(Object cst) {
        buf.add(lvs -> lvs.visitLdcInsn(cst));
        if (cst instanceof Long || cst instanceof Double) { simPush(null); simPush(null); }
        else simPush(null);
        instrIdx++;
    }

    @Override
    public void visitIntInsn(int opcode, int operand) {
        buf.add(lvs -> lvs.visitIntInsn(opcode, operand));
        simPush(null);
        instrIdx++;
    }

    @Override
    public void visitIincInsn(int var, int incr) {
        buf.add(lvs -> lvs.visitIincInsn(var, incr));
        instrIdx++;
    }

    @Override
    public void visitJumpInsn(int opcode, Label label) {
        buf.add(lvs -> lvs.visitJumpInsn(opcode, label));
        switch (opcode) {
            case Opcodes.IFEQ, Opcodes.IFNE, Opcodes.IFLT, Opcodes.IFGE, Opcodes.IFGT,
                 Opcodes.IFLE, Opcodes.IFNULL, Opcodes.IFNONNULL -> simPop();
            case Opcodes.IF_ICMPEQ, Opcodes.IF_ICMPNE, Opcodes.IF_ICMPLT,
                 Opcodes.IF_ICMPGE, Opcodes.IF_ICMPGT, Opcodes.IF_ICMPLE,
                 Opcodes.IF_ACMPEQ, Opcodes.IF_ACMPNE -> { simPop(); simPop(); }
            default -> {}
        }
        sim.clear();   // conservative: assume any branch may reach a merge point
        instrIdx++;
    }

    @Override
    public void visitMultiANewArrayInsn(String desc, int dims) {
        buf.add(lvs -> lvs.visitMultiANewArrayInsn(desc, dims));
        for (int i = 0; i < dims; i++) simPop();
        simPush(null);
        instrIdx++;
    }

    @Override
    public void visitTableSwitchInsn(int min, int max, Label dflt, Label... labels) {
        Label[] lClone = labels.clone();
        buf.add(lvs -> lvs.visitTableSwitchInsn(min, max, dflt, lClone));
        simPop(); sim.clear();
        instrIdx++;
    }

    @Override
    public void visitLookupSwitchInsn(Label dflt, int[] keys, Label[] labels) {
        int[] kClone = keys.clone(); Label[] lClone = labels.clone();
        buf.add(lvs -> lvs.visitLookupSwitchInsn(dflt, kClone, lClone));
        simPop(); sim.clear();
        instrIdx++;
    }

    @Override
    public void visitInvokeDynamicInsn(String name, String desc, Handle bsm, Object... args) {
        Object[] aClone = args.clone();
        buf.add(lvs -> lvs.visitInvokeDynamicInsn(name, desc, bsm, aClone));
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
        // LVS wraps the real delegate; newLocal() allocates fresh locals for arg spilling
        LocalVariablesSorter lvs = new LocalVariablesSorter(access, methodDesc, realDelegate);
        for (BufEntry entry : buf) entry.emit(lvs);
        // COMPUTE_FRAMES recomputes these, but visitMaxs must still be called
        lvs.visitMaxs(savedMaxStack + 20, savedMaxLocals + 20);
        lvs.visitEnd();
    }

    // ===========================================================================
    // Injection helpers
    // ===========================================================================

    private void injectPostLoad(LocalVariablesSorter lvs, FieldRef fi, int rcIdx) {
        // Stack: ..., fieldValue
        lvs.visitInsn(Opcodes.DUP);               // ..., fieldValue, fieldValue
        lvs.visitLdcInsn(fqMethod);
        lvs.visitLdcInsn(rcIdx);
        lvs.visitLdcInsn(fi.owner);
        lvs.visitLdcInsn(fi.name);
        lvs.visitLdcInsn(fi.desc);
        lvs.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER, "onFinalFieldLoad",
                "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V",
                false);
        // Stack: ..., fieldValue  (dup was consumed by tracker)
    }

    private void injectPreDispatch(LocalVariablesSorter lvs, String calleeName,
                                   String calleeDesc, int symIdx) {
        // Stack: ..., receiver, arg1, ..., argN
        // Spill args so we can DUP the receiver that's now buried under them
        Type[] argTypes = Type.getArgumentTypes(calleeDesc);
        int[] argLocals = new int[argTypes.length];
        for (int i = argTypes.length - 1; i >= 0; i--) {
            argLocals[i] = lvs.newLocal(argTypes[i]);
            lvs.visitVarInsn(argTypes[i].getOpcode(Opcodes.ISTORE), argLocals[i]);
        }
        // Stack: ..., receiver
        lvs.visitInsn(Opcodes.DUP);               // ..., receiver, receiver
        lvs.visitLdcInsn(fqMethod);
        lvs.visitLdcInsn(symIdx);
        lvs.visitLdcInsn(calleeName);
        lvs.visitLdcInsn(calleeDesc);
        lvs.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER, "onVirtualDispatch",
                "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)V",
                false);
        // Restore args
        for (int i = 0; i < argTypes.length; i++) {
            lvs.visitVarInsn(argTypes[i].getOpcode(Opcodes.ILOAD), argLocals[i]);
        }
        // Stack: ..., receiver, arg1, ..., argN  — ready for the real INVOKEVIRTUAL
    }

    // ===========================================================================
    // Sim stack helpers
    // ===========================================================================

    private void simPush(Integer marker) { sim.addLast(marker); }
    private Integer simPop() { return sim.isEmpty() ? null : sim.removeLast(); }

    private void simInsn(int op) {
        switch (op) {
            case Opcodes.NOP -> {}
            case Opcodes.ACONST_NULL, Opcodes.ICONST_M1, Opcodes.ICONST_0,
                 Opcodes.ICONST_1, Opcodes.ICONST_2, Opcodes.ICONST_3,
                 Opcodes.ICONST_4, Opcodes.ICONST_5,
                 Opcodes.FCONST_0, Opcodes.FCONST_1, Opcodes.FCONST_2 -> simPush(null);
            case Opcodes.LCONST_0, Opcodes.LCONST_1,
                 Opcodes.DCONST_0, Opcodes.DCONST_1 -> { simPush(null); simPush(null); }
            case Opcodes.IALOAD, Opcodes.FALOAD, Opcodes.AALOAD,
                 Opcodes.BALOAD, Opcodes.CALOAD, Opcodes.SALOAD ->
                { simPop(); simPop(); simPush(null); }
            case Opcodes.LALOAD, Opcodes.DALOAD ->
                { simPop(); simPop(); simPush(null); simPush(null); }
            case Opcodes.IASTORE, Opcodes.FASTORE, Opcodes.AASTORE,
                 Opcodes.BASTORE, Opcodes.CASTORE, Opcodes.SASTORE ->
                { simPop(); simPop(); simPop(); }
            case Opcodes.LASTORE, Opcodes.DASTORE ->
                { simPop(); simPop(); simPop(); simPop(); }
            case Opcodes.POP  -> simPop();
            case Opcodes.POP2 -> { simPop(); simPop(); }
            case Opcodes.DUP  -> simPush(sim.isEmpty() ? null : sim.peekLast());
            case Opcodes.DUP_X1 -> {
                Integer a = simPop(), b = simPop();
                simPush(a); simPush(b); simPush(a);
            }
            case Opcodes.SWAP -> {
                Integer a = simPop(), b = simPop(); simPush(a); simPush(b);
            }
            case Opcodes.IADD, Opcodes.ISUB, Opcodes.IMUL, Opcodes.IDIV, Opcodes.IREM,
                 Opcodes.IAND, Opcodes.IOR,  Opcodes.IXOR, Opcodes.ISHL, Opcodes.ISHR,
                 Opcodes.IUSHR, Opcodes.FADD, Opcodes.FSUB, Opcodes.FMUL, Opcodes.FDIV,
                 Opcodes.FREM,  Opcodes.LCMP, Opcodes.FCMPL, Opcodes.FCMPG,
                 Opcodes.DCMPL, Opcodes.DCMPG ->
                { simPop(); simPop(); simPush(null); }
            case Opcodes.LADD, Opcodes.LSUB, Opcodes.LMUL, Opcodes.LDIV, Opcodes.LREM,
                 Opcodes.LAND, Opcodes.LOR,  Opcodes.LXOR,
                 Opcodes.DADD, Opcodes.DSUB, Opcodes.DMUL, Opcodes.DDIV, Opcodes.DREM ->
                { simPop(); simPop(); simPop(); simPop(); simPush(null); simPush(null); }
            case Opcodes.INEG, Opcodes.FNEG -> { simPop(); simPush(null); }
            case Opcodes.LNEG, Opcodes.DNEG -> { simPop(); simPop(); simPush(null); simPush(null); }
            case Opcodes.I2L, Opcodes.I2D, Opcodes.F2L, Opcodes.F2D ->
                { simPop(); simPush(null); simPush(null); }
            case Opcodes.L2I, Opcodes.L2F, Opcodes.D2I, Opcodes.D2F ->
                { simPop(); simPop(); simPush(null); }
            case Opcodes.I2B, Opcodes.I2C, Opcodes.I2S, Opcodes.I2F, Opcodes.F2I,
                 Opcodes.L2D, Opcodes.D2L ->
                { simPop(); simPush(null); }
            case Opcodes.IRETURN, Opcodes.FRETURN, Opcodes.ARETURN -> simPop();
            case Opcodes.LRETURN, Opcodes.DRETURN -> { simPop(); simPop(); }
            case Opcodes.RETURN  -> {}
            case Opcodes.ATHROW  -> simPop();
            case Opcodes.ARRAYLENGTH -> { simPop(); simPush(null); }
            case Opcodes.MONITORENTER, Opcodes.MONITOREXIT -> simPop();
            default -> {}
        }
    }

    private void simVar(int op) {
        switch (op) {
            case Opcodes.ILOAD, Opcodes.FLOAD, Opcodes.ALOAD  -> simPush(null);
            case Opcodes.LLOAD, Opcodes.DLOAD -> { simPush(null); simPush(null); }
            case Opcodes.ISTORE, Opcodes.FSTORE, Opcodes.ASTORE -> simPop();
            case Opcodes.LSTORE, Opcodes.DSTORE -> { simPop(); simPop(); }
        }
    }

    private void simType(int op) {
        switch (op) {
            case Opcodes.NEW       -> simPush(null);
            case Opcodes.ANEWARRAY -> { simPop(); simPush(null); }
            case Opcodes.CHECKCAST -> {}
            case Opcodes.INSTANCEOF -> { simPop(); simPush(null); }
        }
    }

    private void simField(int op, String desc) {
        boolean wide = desc.equals("J") || desc.equals("D");
        switch (op) {
            case Opcodes.GETSTATIC -> { simPush(null); if (wide) simPush(null); }
            case Opcodes.PUTSTATIC -> { simPop();      if (wide) simPop(); }
            case Opcodes.GETFIELD  -> { simPop(); simPush(null); if (wide) simPush(null); }
            case Opcodes.PUTFIELD  -> { simPop(); if (wide) simPop(); simPop(); }
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
            simPush(null);
            if (ret.getSort() == Type.LONG || ret.getSort() == Type.DOUBLE) simPush(null);
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
            ClassLoader bcl = ClassLoader.getPlatformClassLoader();
            is = bcl.getResourceAsStream(owner + ".class");
            if (is == null && loader != null)
                is = loader.getResourceAsStream(owner + ".class");
            if (is == null) return true;   // unresolvable library → assume final
            ClassReader cr = new ClassReader(is);
            int[] flags = {0};
            cr.accept(new ClassVisitor(Opcodes.ASM9) {
                @Override public FieldVisitor visitField(int acc, String n, String d,
                                                         String sig, Object val) {
                    if (n.equals(name) && d.equals(desc)) flags[0] = acc;
                    return null;
                }
            }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
            return (flags[0] & (Opcodes.ACC_STATIC | Opcodes.ACC_FINAL))
                    == (Opcodes.ACC_STATIC | Opcodes.ACC_FINAL);
        } catch (IOException e) {
            return false;
        } finally {
            if (is != null) try { is.close(); } catch (IOException ignored) {}
        }
    }

    private static int argSlotCount(Type[] args) {
        int n = 0;
        for (Type t : args) n += (t.getSort() == Type.LONG || t.getSort() == Type.DOUBLE) ? 2 : 1;
        return n;
    }

    record FieldRef(String owner, String name, String desc) {}
}
