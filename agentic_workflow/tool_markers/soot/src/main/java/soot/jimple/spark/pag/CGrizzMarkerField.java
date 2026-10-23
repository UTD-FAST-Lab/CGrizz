package soot.jimple.spark.pag;

import soot.jimple.spark.sets.P2SetVisitor;
import soot.jimple.spark.sets.PointsToSetInternal;

/**
 * CGrizz unsoundness markers: a field (or array element) of a marker object. It holds
 * nothing: stores into it are dropped and loads from it are empty.
 *
 * A marker has type NullType, so it reaches variables of every type, including the bases
 * of unrelated field accesses. If its fields held values, they would act as one shared
 * object between all those accesses and leak values between them: new call-graph edges.
 * The lost object's fields are unknown to SPARK anyway.
 */
public final class CGrizzMarkerField extends AllocDotField {

  private static final PointsToSetInternal NOTHING = new PointsToSetInternal(null) {
    @Override
    public boolean isEmpty() {
      return true;
    }

    @Override
    public boolean forall(P2SetVisitor v) {
      return false;
    }

    @Override
    public boolean add(Node n) {
      return false;
    }

    @Override
    public boolean addAll(PointsToSetInternal other, PointsToSetInternal exclude) {
      return false;
    }

    @Override
    public boolean contains(Node n) {
      return false;
    }
  };

  public CGrizzMarkerField(PAG pag, AllocNode base, SparkField field) {
    super(pag, base, field);
  }

  @Override
  public PointsToSetInternal getP2Set() {
    return NOTHING;
  }

  @Override
  public PointsToSetInternal makeP2Set() {
    return NOTHING;
  }
}
