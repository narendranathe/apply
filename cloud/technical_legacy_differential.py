"""Compare synthetic legacy-only index outputs without authorizing a writer.

This helper accepts caller-supplied fixtures; it neither runs a builder nor
authenticates any history. It is not evidence of production compatibility.
"""
from .records import validate_record


def _fixture_cores(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("invalid_fixture")
    indexed = {}
    manifests = 0
    for row in rows:
        core = validate_record(row)
        if row != core or core["id"] in indexed:
            raise ValueError("invalid_fixture")
        indexed[core["id"]] = core
        manifests += core["kind"] == "index_manifests"
    if manifests != 1:
        raise ValueError("invalid_fixture")
    return indexed


def compare_legacy_fixtures(baseline, candidate):
    """Compare every immutable CORE, ignoring only output-list ordering.

    Inputs must represent the same legacy-only fixture, time and prior state.
    A matching result is not proof that either output is authentic or safe.
    """
    try:
        before, after = _fixture_cores(baseline), _fixture_cores(candidate)
        result = "unchanged" if before == after else "different"
    except (TypeError, ValueError, KeyError, RecursionError, OverflowError):
        result = "invalid_fixture"
    return {"result": result, "fixture_only": True, "production_verified": False}
