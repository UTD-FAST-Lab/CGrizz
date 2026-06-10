package edu.utd.fastlab.dcnr;

import org.objectweb.asm.Label;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Type;
import org.objectweb.asm.commons.LocalVariablesSorter;

/**
 * Instruments a single method to track native method instantiation pattern.
 *
 * RC injection:
 *   Native method call sites (e.g., FileChannel.map()) - register the returned object
 *
 * Sym injection:
 *   Virtual/interface calls on objects that may have come from native methods
 */
final class DcnrMethodVisitor extends LocalVariablesSorter {

    private static final int    ASM9          = Opcodes.ASM9;
    private static final String TRACKER       = "edu/utd/fastlab/dcnr/DcnrTracker";
    private static final String ON_NATIVE_RETURN_DESC = "(Ljava/lang/Object;Ljava/lang/String;I)V";
    private static final String ON_VIRTUAL_CALL_DESC = "(Ljava/lang/Object;Ljava/lang/String;ILjava/lang/String;Ljava/lang/String;)V";

    private final String currentMethod; // "com.example.Class.method(desc)" label for output
    private final String ownerClass;

    // Instruction counter (used as PC label in output)
    private int instrIdx = 0;

    DcnrMethodVisitor(int access, String desc, MethodVisitor delegate,
                      String owner, String name) {
        super(ASM9, access, desc, delegate);
        this.ownerClass = owner.replace('/', '.');
        String methodSig = name + desc;
        this.currentMethod = this.ownerClass + "." + methodSig;
    }

    // =========================================================================
    // visit* overrides — update instruction counter
    // =========================================================================

    @Override
    public void visitLdcInsn(Object value) {
        super.visitLdcInsn(value);
        instrIdx++;
    }

    @Override
    public void visitInsn(int opcode) {
        super.visitInsn(opcode);
        instrIdx++;
    }

    @Override
    public void visitVarInsn(int opcode, int var) {
        super.visitVarInsn(opcode, var);
        instrIdx++;
    }

    @Override
    public void visitIntInsn(int opcode, int operand) {
        super.visitIntInsn(opcode, operand);
        instrIdx++;
    }

    @Override
    public void visitTypeInsn(int opcode, String type) {
        super.visitTypeInsn(opcode, type);
        instrIdx++;
    }

    @Override
    public void visitFieldInsn(int opcode, String owner, String name, String desc) {
        super.visitFieldInsn(opcode, owner, name, desc);
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
        instrIdx++;
    }


    // =========================================================================
    // visitMethodInsn — core logic
    // =========================================================================

    @Override
    public void visitMethodInsn(int opcode, String owner, String name,
                                String descriptor, boolean isInterface) {
        int capturedIdx = instrIdx;

        // RC: Track FileChannel.map() calls (native method that returns DirectByteBuffer)
        if (opcode == Opcodes.INVOKEVIRTUAL &&
            owner.equals("java/nio/channels/FileChannel") &&
            name.equals("map") &&
            descriptor.startsWith("(Ljava/nio/channels/FileChannel$MapMode;JJ)")) {
            handleNativeMethodReturn(opcode, owner, name, descriptor, isInterface, capturedIdx);
            return;
        }

        // Sym: Track virtual/interface calls that might be on native method results
        if ((opcode == Opcodes.INVOKEVIRTUAL || opcode == Opcodes.INVOKEINTERFACE)) {
            Type[] argTypes = Type.getArgumentTypes(descriptor);
            // Track all virtual calls and let the Tracker filter by object identity
            handleVirtualCall(opcode, owner, name, descriptor, isInterface, capturedIdx);
            return;
        }

        // All other method calls — emit as-is
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);
        instrIdx++;
    }

    // =========================================================================
    // RC injection: Native method returns (e.g., FileChannel.map())
    // =========================================================================

    private void handleNativeMethodReturn(int opcode, String owner, String name,
                                     String descriptor, boolean isInterface, int capturedIdx) {
        // Stack: [..., receiver, arg1, arg2, arg3]

        // Call the native method
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);
        // Stack: [..., result]

        // Duplicate result for tracker
        super.visitInsn(Opcodes.DUP);
        // Stack: [..., result, result]

        super.visitLdcInsn(currentMethod);
        super.visitLdcInsn(capturedIdx);
        super.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER,
                "onNativeMethodReturn", ON_NATIVE_RETURN_DESC, false);
        // Stack: [..., result]

        instrIdx++;
    }

    // =========================================================================
    // Sym injection: Virtual/interface calls
    // =========================================================================

    private void handleVirtualCall(int opcode, String owner, String name,
                                  String descriptor, boolean isInterface, int capturedIdx) {
        Type[] argTypes = Type.getArgumentTypes(descriptor);

        // Save all arguments to locals (in reverse order)
        int[] argLocals = new int[argTypes.length];
        for (int i = argTypes.length - 1; i >= 0; i--) {
            argLocals[i] = newLocal(argTypes[i]);
            super.visitVarInsn(argTypes[i].getOpcode(Opcodes.ISTORE), argLocals[i]);
        }
        // Stack: [..., receiver]

        // Duplicate receiver and call tracker
        super.visitInsn(Opcodes.DUP);
        // Stack: [..., receiver, receiver]

        super.visitLdcInsn(currentMethod);
        super.visitLdcInsn(capturedIdx);
        super.visitLdcInsn(name);
        super.visitLdcInsn(descriptor);
        super.visitMethodInsn(Opcodes.INVOKESTATIC, TRACKER,
                "onVirtualCall", ON_VIRTUAL_CALL_DESC, false);
        // Stack: [..., receiver]

        // Restore all arguments
        for (int i = 0; i < argTypes.length; i++) {
            super.visitVarInsn(argTypes[i].getOpcode(Opcodes.ILOAD), argLocals[i]);
        }
        // Stack: [..., receiver, arg1, arg2, ...]

        // Emit the original virtual call
        super.visitMethodInsn(opcode, owner, name, descriptor, isInterface);
        instrIdx++;
    }
}
