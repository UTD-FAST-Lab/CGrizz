package edu.utd.fastlab.cgrizz.oracle;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Registry of instrumented instructions. Every injected hook carries the id of
 * its Site, so the runtime knows the enclosing method, the instruction index
 * (reported as "pc") and the static callee without any reflection.
 */
final class Sites {

    static final class Site {
        int id;
        String callerOwner;    // dotted
        String callerName;
        String callerDesc;
        int index;             // instruction index inside the method
        int line = -1;
        int opcode;
        String calleeOwner;    // dotted; null for non-invoke instructions
        String calleeName;
        String calleeDesc;
        Spec.RootCause rootCause;  // non-null when this instruction is a root cause
        boolean symptom;           // generic symptom site (receiver / caller-class checks)
        boolean selfEdge;          // root cause whose own call is the symptom
        boolean bridgeOnly;
        int selfRule = -1;         // rule index of the selfEdge root cause
        /** Indices of the root-cause rules that fired at this instruction. */
        final java.util.Set<Integer> fired = java.util.concurrent.ConcurrentHashMap.newKeySet();
        /** Root causes whose runtime conditions Tracker.rc decides when the instruction executes. */
        final List<Spec.RootCause> conditionalRules = new ArrayList<>();
        final AtomicLong hits = new AtomicLong();
        /** Last (receiver class, tag) this site recorded; skips repeated work in loops. */
        private volatile Last last;

        boolean seen(Class<?> c, int tag) {
            Last l = last;
            return l != null && l.cls == c && l.tag == tag;
        }

        void remember(Class<?> c, int tag) {
            last = new Last(c, tag);
        }

        String callerMethod() {
            return callerOwner + "." + callerName + callerDesc;
        }

        String rcKey() {
            return "RC:" + callerMethod() + "@" + index;
        }
    }

    /** Immutable, so a racing reader never pairs one thread's class with another's tag. */
    private static final class Last {
        final Class<?> cls;
        final int tag;

        Last(Class<?> cls, int tag) {
            this.cls = cls;
            this.tag = tag;
        }
    }

    private static final Object LOCK = new Object();
    private static volatile Site[] sites = new Site[4096];
    private static int count;

    /** insn root-cause sites by "owner.method", used to link implicit exceptions. */
    private static final Map<String, List<Site>> INSN_SITES = new ConcurrentHashMap<>();

    static int add(Site s) {
        synchronized (LOCK) {
            Site[] cur = sites;
            if (count == cur.length) {
                Site[] grown = new Site[cur.length * 2];
                System.arraycopy(cur, 0, grown, 0, cur.length);
                cur = grown;
            }
            s.id = count;
            cur[count++] = s;
            sites = cur;
        }
        if (s.rootCause != null && s.rootCause.at.equals("insn")) {
            List<Site> l = INSN_SITES.computeIfAbsent(s.callerOwner + "." + s.callerName, k -> new ArrayList<>());
            synchronized (l) {
                l.add(s);
            }
        }
        return s.id;
    }

    static Site get(int id) {
        Site[] cur = sites;
        return id >= 0 && id < cur.length ? cur[id] : null;
    }

    static List<Site> insnSites(String ownerDotMethod) {
        List<Site> l = INSN_SITES.get(ownerDotMethod);
        if (l == null) return Collections.emptyList();
        synchronized (l) {
            return new ArrayList<>(l);
        }
    }

    static boolean hasInsnSites() {
        return !INSN_SITES.isEmpty();
    }
}
