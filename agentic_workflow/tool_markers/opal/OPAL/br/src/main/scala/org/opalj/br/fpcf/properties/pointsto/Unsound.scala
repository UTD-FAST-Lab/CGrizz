/* BSD 2-Clause License - see OPAL/LICENSE for details. */
package org.opalj
package br
package fpcf
package properties
package pointsto

import java.util.concurrent.ConcurrentHashMap

/**
 * CGrizz unsoundness markers for OPAL's type-based points-to analysis (issue #14).
 *
 * Where OPAL gives up on a value, the annotation adds `Unsound.markerType(id, description)` to
 * the value's points-to set (e.g. via `state.includeSharedPointsToSet(defSite,
 * createPointsToSet(pc, context, Unsound.markerType(...), isConstant = false), noFilter)`).
 * TypeBasedPointsToSet's filters and TypeIterator.isPossibleType let marker types through, so the
 * marker flows like the lost object; CallGraphAnalysis.handleCall never resolves a target for it
 * and prints one warning per call site instead, so the call graph does not change:
 *
 * {{{
 * [UNSOUNDNESS-EDGE] <description> | caller=pkg.Cls.m(params) | site=<callee> | line=<n> | pc=<n> | rc=<id>
 * }}}
 */
object Unsound {

    final val Prefix = "cgrizz/unsound/Marker_"

    private[this] val descriptions = new ConcurrentHashMap[String, String]()
    private[this] val reported = ConcurrentHashMap.newKeySet[String]()
    /**
     * CGRIZZ_MARKER_SCOPE=application: warn only at calls outside the JDK. (OPAL's project types do
     * not tell: JCG adds the JDK to the project when it analyses it.)
     */
    private[this] val applicationOnly = "application" == System.getenv("CGRIZZ_MARKER_SCOPE")
    private[this] val jdkPackages = Seq("java.", "javax.", "jdk.", "sun.", "com.sun.", "com.oracle.", "org.ietf.jgss.", "org.omg.", "org.w3c.dom.", "org.xml.sax.", "org.jcp.xml.")

    /** The marker type of root cause `id`; `description`: at most 60 characters, for the tool's user. */
    def markerType(id: String, description: String): ObjectType = {
        descriptions.putIfAbsent(id, description)
        ObjectType(Prefix + id)
    }

    def isMarker(t: ReferenceType): Boolean = t.isObjectType && t.asObjectType.fqn.startsWith(Prefix)

    /** caller: pkg.Cls.m(params). */
    def report(marker: ReferenceType, caller: String, site: String, line: Int, pc: Int): Unit = {
        val id = marker.asObjectType.fqn.substring(Prefix.length)
        if ((!applicationOnly || !jdkPackages.exists(caller.startsWith)) && reported.add(s"$id|$caller|$pc"))
            println(
                s"[UNSOUNDNESS-EDGE] ${descriptions.getOrDefault(id, id)} | caller=$caller | site=$site"+
                    s" | line=$line | pc=$pc | rc=$id"
            )
    }
}
