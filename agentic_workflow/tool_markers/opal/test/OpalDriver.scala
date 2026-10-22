import java.io.File
import java.io.PrintWriter
import java.net.URL
import scala.jdk.CollectionConverters._

import com.typesafe.config.ConfigFactory
import com.typesafe.config.ConfigValueFactory

import org.opalj.br.analyses.DeclaredMethodsKey
import org.opalj.br.analyses.Project
import org.opalj.br.analyses.Project.JavaClassFileReader
import org.opalj.br.fpcf.PropertyStoreKey
import org.opalj.br.fpcf.properties.cg.Callees
import org.opalj.br.fpcf.properties.cg.NoCalleesDueToNotReachableMethod
import org.opalj.fpcf.FinalEP
import org.opalj.fpcf.PropertyStore
import org.opalj.tac.cg.TypeBasedPointsToCallGraphKey
import org.opalj.tac.cg.TypeIteratorKey
import org.opalj.tac.fpcf.analyses.cg.TypeIterator

/**
 * Runs OPAL 0-CFA (TypeBasedPointsToCallGraphKey) the way JCG's OpalJCGAdatper does when the JDK
 * is not analysed (JDK = interface-only library) and writes the call graph as sorted
 * "caller @pc -> callee" lines.
 *
 * Usage: OpalDriver <class dir> <main class> <rt.jar> <output edges file>
 */
object OpalDriver {
    def main(a: Array[String]): Unit = {
        val Array(target, mainClass, rtJar, out) = a
        val base = ConfigFactory.load().withValue(
            "org.opalj.br.reader.ClassFileReader.Invokedynamic.rewrite", ConfigValueFactory.fromAnyRef(true)
        )
        implicit val config = base.withValue(
            "org.opalj.br.analyses.cg.InitialEntryPointsKey.analysis",
            ConfigValueFactory.fromAnyRef("org.opalj.br.analyses.cg.ConfigurationEntryPointsFinder")
        ).withValue(
            "org.opalj.br.analyses.cg.InitialEntryPointsKey.entryPoints",
            ConfigValueFactory.fromIterable(
                (base.getObjectList("org.opalj.br.analyses.cg.InitialEntryPointsKey.entryPoints").asScala :+
                    ConfigValueFactory.fromMap(Map("declaringClass" -> mainClass.replace('.', '/'), "name" -> "main").asJava)).asJava
            )
        ).withValue(
            "org.opalj.br.analyses.cg.InitialInstantiatedTypesKey.analysis",
            ConfigValueFactory.fromAnyRef("org.opalj.br.analyses.cg.ApplicationInstantiatedTypesFinder")
        )
        val reader = JavaClassFileReader(theConfig = config)
        val project: Project[URL] = Project(
            reader.ClassFiles(new File(target)),
            Project.JavaLibraryClassFileReader.AllClassFiles(Seq(new File(rtJar))),
            libraryClassFilesAreInterfacesOnly = true,
            Seq.empty
        )
        implicit val ps: PropertyStore = project.get(PropertyStoreKey)
        project.get(TypeBasedPointsToCallGraphKey)
        implicit val typeIterator: TypeIterator = project.get(TypeIteratorKey)
        val declaredMethods = project.get(DeclaredMethodsKey)
        val edges = scala.collection.mutable.SortedSet.empty[String]
        for (dm <- declaredMethods.declaredMethods) {
            val ownMethod = (!dm.hasSingleDefinedMethod && !dm.hasMultipleDefinedMethods) ||
                (dm.hasSingleDefinedMethod && dm.definedMethod.classFile.thisType == dm.declaringClassType)
            if (ownMethod) ps(dm, Callees.key) match {
                case FinalEP(_, callees: Callees) if callees ne NoCalleesDueToNotReachableMethod =>
                    for (ctx <- callees.callerContexts; (pc, targets) <- callees.callSites(ctx); tgt <- targets)
                        edges += s"${dm.toJava} @$pc -> ${tgt.method.toJava}"
                case _ =>
            }
        }
        val w = new PrintWriter(out)
        edges.foreach(w.println)
        w.close()
        ps.shutdown()
        System.err.println(s"[driver] ${edges.size} edges")
    }
}
