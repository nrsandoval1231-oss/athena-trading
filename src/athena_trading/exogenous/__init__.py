"""Point-in-time exogenous (non-price) evidence for AAT-V2.

Proposed in ``docs/AAT_V2_VISION.md``; sources specified in
``docs/EXOGENOUS_SOURCES_V1.md``.  This package holds no registered evidence and
supports no claim.  It must never import from ``orb_m`` or ``aat_v1``: the
deterministic strategy stays runnable with this directory deleted.
"""

from .analysts import (
    COMPRESSOR_VERSION,
    AnalystError,
    compress_all,
    compressor_identity,
    coverage,
    feature_keys,
)
from .buckets import ABSENT, bucket, ratio_bucket
from .contracts import (
    SOURCES,
    ExogenousContractError,
    ExogenousLeakageError,
    ExogenousRecord,
    assert_no_lookahead,
    parse_record,
    visible_records,
)
from .ingest import (
    IngestError,
    assert_stable_availability,
    load_record_store,
    normalize,
    passthrough_adapter,
    write_record_store,
)
from .snapshot import (
    SnapshotError,
    build_snapshot,
    compress_opportunity,
    coverage_rates,
    load_opportunity_index,
)

__all__ = [
    "ABSENT", "COMPRESSOR_VERSION", "SOURCES", "AnalystError", "ExogenousContractError",
    "ExogenousLeakageError", "ExogenousRecord", "IngestError", "SnapshotError",
    "assert_no_lookahead", "assert_stable_availability", "bucket", "build_snapshot",
    "compress_all", "compress_opportunity", "compressor_identity", "coverage",
    "coverage_rates", "feature_keys", "load_opportunity_index", "load_record_store",
    "normalize", "parse_record", "passthrough_adapter", "ratio_bucket", "visible_records",
    "write_record_store",
]
