package edu.utd.fastlab.frfvd;

import org.objectweb.asm.*;

/**
 * Routes each non-abstract, non-native method to {@link FrfvdMethodVisitor}.
 */
final class FrfvdClassVisitor extends ClassVisitor {

    private final ClassLoader loader;
    private String className;

    FrfvdClassVisitor(ClassVisitor cv, ClassLoader loader) {
        super(Opcodes.ASM9, cv);
        this.loader = loader;
    }

    @Override
    public void visit(int version, int access, String name, String signature,
                      String superName, String[] interfaces) {
        this.className = name;
        super.visit(version, access, name, signature, superName, interfaces);
    }

    @Override
    public MethodVisitor visitMethod(int access, String name, String descriptor,
                                     String signature, String[] exceptions) {
        MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
        // Skip abstract and native methods — they have no bytecode body
        if ((access & (Opcodes.ACC_ABSTRACT | Opcodes.ACC_NATIVE)) != 0) return mv;
        return new FrfvdMethodVisitor(mv, access, className, name, descriptor, loader);
    }
}
