"""Fixture-only guard for hypothetical legacy-only index builders."""
from copy import deepcopy
import unittest

from cloud.records import make_record
from cloud.technical_legacy_differential import compare_legacy_fixtures


def outputs():
    return [
        make_record("entity_projections", {"suppressed": True, "note": "SYNTHETIC PRIVATE"}, "projection:contact"),
        make_record("entity_projections", {"application_status": "submission_unknown"}, "projection:application"),
        make_record("index_manifests", {"complete": True, "history": ["projection:contact", "projection:application"]},
                    "manifest:fixture", "published"),
    ]


class LegacyFixtureDifferentialTests(unittest.TestCase):
    def test_identical_reordered_legacy_output_is_fixture_only_evidence(self):
        result = compare_legacy_fixtures(outputs(), list(reversed(deepcopy(outputs()))))
        self.assertEqual(result, {"result": "unchanged", "fixture_only": True, "production_verified": False})

    def test_changed_suppression_and_missing_history_are_detected_without_leaking_values(self):
        baseline = outputs()
        changed = deepcopy(baseline)
        changed[0] = make_record("entity_projections", {"suppressed": False, "note": "SYNTHETIC PRIVATE"},
                                 "projection:contact")
        missing = [baseline[0], baseline[2]]
        for candidate in (changed, missing):
            result = compare_legacy_fixtures(baseline, candidate)
            self.assertEqual(result["result"], "different")
            self.assertFalse(result["production_verified"])
            self.assertNotIn("SYNTHETIC PRIVATE", str(result))
            self.assertNotIn("projection:contact", str(result))

    def test_altered_manifest_semantics_or_corrupted_hash_never_pass(self):
        baseline = outputs()
        changed_manifest = deepcopy(baseline)
        changed_manifest[-1] = make_record("index_manifests", {"complete": False, "history": []},
                                           "manifest:fixture", "published")
        self.assertEqual(compare_legacy_fixtures(baseline, changed_manifest)["result"], "different")
        invalid = deepcopy(baseline)
        invalid[0]["hash"] = "0" * 64
        self.assertEqual(compare_legacy_fixtures(baseline, invalid)["result"], "invalid_fixture")

    def test_added_children_and_duplicate_or_missing_manifests_are_rejected(self):
        baseline = outputs()
        extra = baseline + [make_record("entity_projections", {"value": "new"}, "extra:fixture")]
        self.assertEqual(compare_legacy_fixtures(baseline, extra)["result"], "different")
        for malformed in (baseline + [baseline[0]], baseline[:-1], baseline + [baseline[-1]]):
            with self.subTest(count=len(malformed)):
                self.assertEqual(compare_legacy_fixtures(baseline, malformed)["result"], "invalid_fixture")


if __name__ == "__main__":
    unittest.main()
