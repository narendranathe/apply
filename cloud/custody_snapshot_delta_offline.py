"""Sanitized offline comparison; does not fetch, authenticate or authorize."""
from .activation import _time
from .startup_reconciliation import SnapshotState, core, source_metadata


class SnapshotComparisonUnavailable(ValueError):
    """Fixed, non-sensitive diagnostic when comparison cannot be performed."""


def _counts(before, after):
    first, second = set(before), set(after)
    return {'added': len(second - first), 'removed': len(first - second),
            'changed': sum(before[key] != after[key] for key in first & second)}


def compare_snapshots(before, after):
    """Compare complete supplied snapshots under the historical normalization.

    The caller is responsible for separately proving authenticity, freshness,
    serialization, and history. Counts disclose no record identifiers or data.
    """
    try:
        prior, current = SnapshotState(before), SnapshotState(after)
        if (prior.data_source_id != current.data_source_id
                or _time(current.snapshot['captured_at']) < _time(prior.snapshot['captured_at'])):
            raise ValueError('different_sources')
        pages = _counts(prior.pages, current.pages)
        records = _counts({key: core(value) for key, value in prior.rows.items()},
                          {key: core(value) for key, value in current.rows.items()})
        metadata_changed = (source_metadata(prior.snapshot['data_source'], data_source=True)
                            != source_metadata(current.snapshot['data_source'], data_source=True))
        equal = prior.fingerprint() == current.fingerprint()
    except Exception:
        raise SnapshotComparisonUnavailable('snapshot_comparison_unavailable') from None
    return {'pages': pages, 'records': records, 'source_metadata_changed': metadata_changed,
            'source_fingerprints_equal': equal, 'authenticity_verified': False,
            'reconciliation_authorized': False}
