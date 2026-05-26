package edu.utd.fastlab.dcnr;

import org.objectweb.asm.Label;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.commons.LocalVariablesSorter;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Instruments a single method to track the DynamicClassNameReflection (DCNR) pattern.
 *
 * Pass: single-pass, using LocalVariablesSorter for fresh local allocation.
 *
 * RC injection (after dynamic Class.forName):
 *   Detects Class.forName(s) call sites where the string arg was NOT produced
 *   by a compile-time LDC String instruction (tracked via operand-stack simulation).
 *   Injects DUP + DcnrTracker.onDynamicClassLoad(Class, String, int) after the call.
 *
 * Sym injection (before every INVOKEVIRTUAL / INVOKEINTERFACE):
 *   Spills args to fresh locals, DUPs the receiver, calls
 *   DcnrTracker.onVirtualDispatch(Object, String, int, String, String),
 *   then restores args before the original instruction.
 *
 * Stack simulation (simplified, all values = 1 word):
 *   TRUE  slot  = value was produced by LDC with a String literal
 *   FALSE slot  = anything else (method call, field load, concatenation, etc.)
 *
 *   For long/double we push two FALSE slots, which slightly mis-counts the stack
 *   depth for code that mixes long/double arithmetic. In practice, Class.forName
 *   chains never involve long/double, so accuracy is preserved for our target sites.
 */
final class DcnrMethodVisitor extends LocalVariablesSorter {

    private static final int    ASM9          = Opcodes.ASM9;
    private static final String TRACKER       = "edu/utd/fastlab/dcnr/DcnrTracker";
    private static final String ON_LOAD_DESC  =
            "(Ljava/lang/Class;Ljava/lang/String;I)V";
    private static final String ON_DISP_DESC  =
            "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)V";

    private final String currentMethod; // "Lowner;.name:desc" label for output

    // Operand-stack simulation: Boolean.TRUE = LDC String, Boolean.FALSE = other
    private final Deque<Boolean> sim = new ArrayDeque<Boolean>();

    // Instruction counter (used as PC label in output; matches what we inject as int constant)
    private int instrIdx = 0;

    DcnrMethodVisitor(int access, String desc, MethodVisitor delegate,
                      String owner, String name) {
        super(ASM9, access, desc, delegate);
        this.currentMethod = "L" + owner + ";." + name + ":" + desc;
    }

    // =========================================================================
    // Operand-stack simulation helpers
    // =========================================================================

    private void simPush(boolean isConstStr) {
        sim.addFirst(isConstStr ? Boolean.TRUE : Boolean.FALSE);
    }

    private boolean simPop() {
        return sim.isEmpty() ? false : Boolean.TRUE.equals(sim.removeFirst());
    }

    /** Peek at depth positions from the top (0 = top). Returns false if out of range. */
    private boolean simPeek(int depth) {
        int i = 0;
        for (Boolean b : sim) {
            if (i == depth) return Boolean.TRUE.equals(b);
            i++;
        }
        return false;
    }

    private void simPopN(int n) {
        for (int i = 0; i < n; i++) simPop();
    }

    // =========================================================================
    // visit* overrides — update simulation then call super (= emit to delegate)
    // =========================================================================

    @Override
    public void visitLdcInsn(Object value) {
        super.visitLdcInsn(value);
        if (value instanceof Long || value instanceof Double) {
            simPush(false); simPush(false); // 2-word constant
        } else {
            simPush(value instanceof String); // TRUE only for string literals
        }
        instrIdx++;
    }

    @Override
    public void visitInsn(int opcode) {
        super.visitInsn(opcode);
        simInsn(opcode);
        instrIdx++;
    }

    @Override
    public void visitVarInsn(int opcode, int var) {
        super.visitVarInsn(opcode, var);
        simVar(opcode);
        instrIdx++;
    }

    @Override
    public void visitIntInsn(int opcode, int operand) {
        super.visitIntInsn(opcode, operand);
        switch (opcode) {
            case Opcodes.BIPUSH:
            case Opcodes.SIPUSH:  simPush(false); break;
            case Opcodes.NEWARRAY: simPop(); simPush(false); break;
        }
        instrIdx++;
    }

    @Override
    public void visitTypeInsn(int opcode, String type) {
        super.visitTypeInsn(opcode, type);
        switch (opcode) {
            case Opcodes.NEW:       simPush(false); break;
            case Opcodes.ANEWARRAY: simPop(); simPush(false); break;
            case Opcodes.CHECKCAST: simPop(); simPush(false); break;
            case Opcodes.INSTANCEOF: simPop(); simPush(false); break;
        }
        instrIdx++;
    }

    @Override
    public void visitFieldInsn(int opcode, String owner, String name, String desc) {
        super.visitFieldInsn(opcode, owner, name, desc);
        simField(opcode, desc);
        instrIdx++;
    }

    @Override
    public void visitIincInsn(int var, int increment) {
        super.visitIincInsn(var, increment);
        instrIdx++;
    }

    @Override
    public void visitJumpInsn(int opcode, Label label) {
        super.visitJumpInsn(opcode, label);
        simJump(opcode);
        instrIdx++;
    }

    // =========================================================================
    // visitMethodInsn — core logic
    // =========================================================================

    @Override
    public void visitMethodInsn(int opcode, String owner, String name,
                                String descriptor, boolean isInterface) {

        boolean isForName = opcode == Opcodes.INVOKESTATIC
                && "java/lang/Class".equals(owner)
                && "forName".equals(name)
                && (descriptor.equals("(Ljava/lang/String;)Ljava/lang/Class;")
                        || descriptor.equals(
                                "(Ljava/lang/String;ZLjava/lang/ClassLoader;)Ljava/lang/Class;"));

        boolean isVirtual = (opcode == Opcodes.INVOKEVIRTUAL
                || opcode == Opcodes.INVOKEINTERFACE);

        if (isForName) {
            handleForName(opcode, owner, name, descriptor, isInterface);
            return;
        }

        if (isVirtual) {
            handleVirtualDispatch(opcode, owner, name, descriptor, isInterface);
            return;
        }

        // All other method calls — emit as-is, update sim
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);
        simMethod(opcode, descriptor, false);
        instrIdx++;
    }

    // =========================================================================
    // RC injection: dynamic Class.forName
    // =========================================================================

    private void handleForName(int opcode, String owner, String name,
                                String descriptor, boolean isInterface) {

        Type[] argTypes = Type.getArgumentTypes(descriptor);
        int argCount = argTypes.length; // 1 or 3

        // For 1-arg forName: string is at sim depth 0 (top)
        // For 3-arg forName: args pushed as [String, boolean, ClassLoader] → String is at depth 2
        int stringDepth = argCount - 1;
        boolean stringIsConstant = simPeek(stringDepth);

        // Emit the original Class.forName call
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);

        // Update sim: pop args, push the returned Class object (not a string)
        simPopN(argCount); // INVOKESTATIC has no implicit 'this'
        simPush(false);

        int capturedIdx = instrIdx;
        instrIdx++;

        // Only inject tracker when the string argument was NOT a compile-time constant
        if (!stringIsConstant) {
            // Stack: [..., Class]
            // DUP so tracker can consume one copy; original stays on stack
            super.visitInsn(Opcodes.DUP);
            super.visitLdcInsn(currentMethod);
            super.visitLdcInsn(capturedIdx);
            super.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER,
                    "onDynamicClassLoad", ON_LOAD_DESC, false);
            // Stack: [..., Class]  ← DUP consumed; original Class remains
        }
    }

    // =========================================================================
    // Sym injection: virtual / interface dispatch
    // =========================================================================

    private void handleVirtualDispatch(int opcode, String owner, String name,
                                       String descriptor, boolean isInterface) {

        Type[] argTypes = Type.getArgumentTypes(descriptor);
        int capturedIdx = instrIdx;

        // --- Spill all arguments to fresh locals (reverse order: last arg first) ---
        int[] argLocals = new int[argTypes.length];
        for (int i = argTypes.length - 1; i >= 0; i--) {
            argLocals[i] = newLocal(argTypes[i]);
            super.visitVarInsn(argTypes[i].getOpcode(Opcodes.ISTORE), argLocals[i]);
        }
        // Stack now: [..., receiver]

        // DUP the receiver so tracker consumes one copy
        super.visitInsn(Opcodes.DUP);

        // Push tracker arguments: (Object receiver, String caller, int pc, String callee, String desc)
        super.visitLdcInsn(currentMethod);
        super.visitLdcInsn(capturedIdx);
        super.visitLdcInsn(name);
        super.visitLdcInsn(descriptor);
        super.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER,
                "onVirtualDispatch", ON_DISP_DESC, false);
        // Stack: [..., receiver]

        // Restore arguments
        for (int i = 0; i < argTypes.length; i++) {
            super.visitVarInsn(argTypes[i].getOpcode(Opcodes.ILOAD), argLocals[i]);
        }
        // Stack: [..., receiver, arg0, arg1, ...]

        // Emit original dispatch
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);

        // Update sim: pop receiver + args, push return type
        simMethod(opcode, descriptor, false /* sim update only */);
        instrIdx++;
    }

    // =========================================================================
    // Stack simulation implementations
    // =========================================================================

    private void simInsn(int op) {
        switch (op) {
            case Opcodes.NOP: break;

            case Opcodes.ACONST_NULL:
            case Opcodes.ICONST_M1: case Opcodes.ICONST_0: case Opcodes.ICONST_1:
            case Opcodes.ICONST_2:  case Opcodes.ICONST_3: case Opcodes.ICONST_4:
            case Opcodes.ICONST_5:
            case Opcodes.FCONST_0:  case Opcodes.FCONST_1: case Opcodes.FCONST_2:
                simPush(false); break;

            case Opcodes.LCONST_0: case Opcodes.LCONST_1:
            case Opcodes.DCONST_0: case Opcodes.DCONST_1:
                simPush(false); simPush(false); break;

            // array loads: pop ref + index, push element
            case Opcodes.IALOAD: case Opcodes.FALOAD: case Opcodes.AALOAD:
            case Opcodes.BALOAD: case Opcodes.CALOAD: case Opcodes.SALOAD:
                simPopN(2); simPush(false); break;
            case Opcodes.LALOAD: case Opcodes.DALOAD:
                simPopN(2); simPush(false); simPush(false); break;

            // array stores: pop ref + index + value (simplified: treat all as 1-word value)
            case Opcodes.IASTORE: case Opcodes.FASTORE: case Opcodes.AASTORE:
            case Opcodes.BASTORE: case Opcodes.CASTORE: case Opcodes.SASTORE:
                simPopN(3); break;
            case Opcodes.LASTORE: case Opcodes.DASTORE:
                simPopN(4); break;

            case Opcodes.POP: simPop(); break;
            case Opcodes.POP2: simPopN(2); break;

            case Opcodes.DUP: { boolean v = simPop(); simPush(v); simPush(v); break; }
            case Opcodes.DUP_X1: {
                boolean v1 = simPop(), v2 = simPop();
                simPush(v1); simPush(v2); simPush(v1); break;
            }
            case Opcodes.DUP_X2: {
                boolean v1 = simPop(), v2 = simPop(), v3 = simPop();
                simPush(v1); simPush(v3); simPush(v2); simPush(v1); break;
            }
            case Opcodes.DUP2: {
                boolean v1 = simPop(), v2 = simPop();
                simPush(v2); simPush(v1); simPush(v2); simPush(v1); break;
            }
            case Opcodes.DUP2_X1: case Opcodes.DUP2_X2: sim.clear(); break;
            case Opcodes.SWAP: { boolean v1 = simPop(), v2 = simPop(); simPush(v1); simPush(v2); break; }

            // binary int/float ops: pop 2, push 1
            case Opcodes.IADD: case Opcodes.ISUB: case Opcodes.IMUL: case Opcodes.IDIV:
            case Opcodes.IREM: case Opcodes.IAND: case Opcodes.IOR:  case Opcodes.IXOR:
            case Opcodes.ISHL: case Opcodes.ISHR: case Opcodes.IUSHR:
            case Opcodes.FADD: case Opcodes.FSUB: case Opcodes.FMUL: case Opcodes.FDIV:
            case Opcodes.FREM: case Opcodes.FCMPL: case Opcodes.FCMPG:
                simPopN(2); simPush(false); break;

            // binary long/double ops: pop 4, push 2
            case Opcodes.LADD: case Opcodes.LSUB: case Opcodes.LMUL: case Opcodes.LDIV:
            case Opcodes.LREM: case Opcodes.LAND: case Opcodes.LOR: case Opcodes.LXOR:
            case Opcodes.DADD: case Opcodes.DSUB: case Opcodes.DMUL: case Opcodes.DDIV:
            case Opcodes.DREM:
                simPopN(4); simPush(false); simPush(false); break;

            // long shifts: long(2) + int(1) → long(2)
            case Opcodes.LSHL: case Opcodes.LSHR: case Opcodes.LUSHR:
                simPopN(3); simPush(false); simPush(false); break;

            // compare long/double → int
            case Opcodes.LCMP: case Opcodes.DCMPL: case Opcodes.DCMPG:
                simPopN(4); simPush(false); break;

            // unary
            case Opcodes.INEG: case Opcodes.FNEG: simPop(); simPush(false); break;
            case Opcodes.LNEG: case Opcodes.DNEG: simPopN(2); simPush(false); simPush(false); break;

            // conversions: int/float → ...
            case Opcodes.I2B: case Opcodes.I2C: case Opcodes.I2S:
            case Opcodes.I2F: case Opcodes.F2I:
                simPop(); simPush(false); break;
            case Opcodes.I2L: case Opcodes.I2D: case Opcodes.F2L: case Opcodes.F2D:
                simPop(); simPush(false); simPush(false); break;
            case Opcodes.L2I: case Opcodes.L2F: case Opcodes.D2I: case Opcodes.D2F:
                simPopN(2); simPush(false); break;
            case Opcodes.L2D: case Opcodes.D2L:
                // 2-word → 2-word: no net change
                break;

            // returns
            case Opcodes.IRETURN: case Opcodes.FRETURN: case Opcodes.ARETURN: simPop(); break;
            case Opcodes.LRETURN: case Opcodes.DRETURN: simPopN(2); break;
            case Opcodes.RETURN: break;

            case Opcodes.ARRAYLENGTH: simPop(); simPush(false); break;
            case Opcodes.ATHROW:      simPop(); break;
            case Opcodes.MONITORENTER: case Opcodes.MONITOREXIT: simPop(); break;
        }
    }

    private void simVar(int op) {
        switch (op) {
            case Opcodes.ILOAD: case Opcodes.FLOAD: case Opcodes.ALOAD:
                simPush(false); break;
            case Opcodes.LLOAD: case Opcodes.DLOAD:
                simPush(false); simPush(false); break;
            case Opcodes.ISTORE: case Opcodes.FSTORE: case Opcodes.ASTORE:
                simPop(); break;
            case Opcodes.LSTORE: case Opcodes.DSTORE:
                simPopN(2); break;
            // RET: no stack change
        }
    }

    private void simField(int op, String desc) {
        boolean isWide = desc.equals("J") || desc.equals("D");
        switch (op) {
            case Opcodes.GETSTATIC:
                if (isWide) { simPush(false); simPush(false); } else { simPush(false); }
                break;
            case Opcodes.GETFIELD:
                simPop(); // pop objectref
                if (isWide) { simPush(false); simPush(false); } else { simPush(false); }
                break;
            case Opcodes.PUTSTATIC:
                if (isWide) simPopN(2); else simPop();
                break;
            case Opcodes.PUTFIELD:
                if (isWide) simPopN(3); else simPopN(2); // pop value + objectref
                break;
        }
    }

    private void simJump(int op) {
        switch (op) {
            case Opcodes.GOTO: case 200 /* GOTO_W */: break;
            case Opcodes.JSR:  case 201 /* JSR_W  */: simPush(false); break;
            case Opcodes.IFEQ: case Opcodes.IFNE: case Opcodes.IFLT:
            case Opcodes.IFGE: case Opcodes.IFGT: case Opcodes.IFLE:
            case Opcodes.IFNULL: case Opcodes.IFNONNULL:
                simPop(); break;
            default: // IF_ACMP*, IF_ICMP*
                simPopN(2); break;
        }
    }

    /**
     * Update simulation for a method call (does not emit any bytecode).
     * opcode unused; isInterface flag unused — we only need the descriptor.
     */
    private void simMethod(int opcode, String descriptor, boolean ignored) {
        // Pop receiver for non-static calls
        if (opcode != Opcodes.INVOKESTATIC) {
            simPop();
        }
        // Pop all arguments (simplified: each arg = 1 slot)
        Type[] argTypes = Type.getArgumentTypes(descriptor);
        simPopN(argTypes.length);
        // Push return value
        Type retType = Type.getReturnType(descriptor);
        if (retType.getSort() != Type.VOID) {
            if (retType.getSort() == Type.LONG || retType.getSort() == Type.DOUBLE) {
                simPush(false); simPush(false);
            } else {
                simPush(false);
            }
        }
    }
}
