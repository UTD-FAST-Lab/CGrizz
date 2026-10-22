package edu.utd.fastlab.cgrizz.oracle;

import java.lang.ref.Reference;
import java.lang.ref.ReferenceQueue;
import java.lang.ref.WeakReference;

/**
 * Weak identity map from objects to int tags. Uses only System.identityHashCode,
 * never the application's hashCode/equals, so lookups cannot run program code.
 * The first tag an object receives is kept.
 */
final class TagMap {

    private static final class Entry extends WeakReference<Object> {
        final int hash;
        final int value;
        Entry next;

        Entry(Object key, int hash, int value, Entry next, ReferenceQueue<Object> q) {
            super(key, q);
            this.hash = hash;
            this.value = value;
            this.next = next;
        }
    }

    private final ReferenceQueue<Object> queue = new ReferenceQueue<>();
    private Entry[] table = new Entry[1 << 10];
    private int size;
    private volatile boolean empty = true;

    boolean isEmpty() {
        return empty;
    }

    synchronized int size() {
        expunge();
        return size;
    }

    /** Returns true if the object was not tagged before. */
    synchronized boolean putIfAbsent(Object key, int value) {
        expunge();
        int h = System.identityHashCode(key);
        int idx = h & (table.length - 1);
        for (Entry e = table[idx]; e != null; e = e.next) {
            if (e.hash == h && e.get() == key) return false;
        }
        table[idx] = new Entry(key, h, value, table[idx], queue);
        size++;
        empty = false;
        if (size > table.length * 3 / 4) resize();
        return true;
    }

    /** Returns the tag, or -1. */
    int get(Object key) {
        if (empty || key == null) return -1;
        synchronized (this) {
            int h = System.identityHashCode(key);
            for (Entry e = table[h & (table.length - 1)]; e != null; e = e.next) {
                if (e.hash == h && e.get() == key) return e.value;
            }
            return -1;
        }
    }

    private void resize() {
        Entry[] old = table;
        Entry[] grown = new Entry[old.length * 2];
        for (Entry head : old) {
            for (Entry e = head; e != null; ) {
                Entry next = e.next;
                int idx = e.hash & (grown.length - 1);
                e.next = grown[idx];
                grown[idx] = e;
                e = next;
            }
        }
        table = grown;
    }

    private void expunge() {
        for (Reference<?> r; (r = queue.poll()) != null; ) {
            Entry dead = (Entry) r;
            int idx = dead.hash & (table.length - 1);
            Entry prev = null;
            for (Entry e = table[idx]; e != null; prev = e, e = e.next) {
                if (e == dead) {
                    if (prev == null) table[idx] = e.next;
                    else prev.next = e.next;
                    size--;
                    break;
                }
            }
        }
    }
}
