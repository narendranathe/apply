"""Reject malformed untrusted analysis inputs with fixed error codes."""
import unittest

from cloud.technical_analysis import AnalysisRejected, validate_observation, validate_opportunity


class TechnicalAnalysisUntrustedTests(unittest.TestCase):
    def test_unhashable_observation_classification_is_rejected(self):
        from tests.test_technical_analysis import source
        evidence = source()
        observation = {"classification": ["secret input"], "source_id": evidence["source_id"],
                       "source_sha256": evidence["sha256"], "quote": "stores work durably"}
        with self.assertRaises(AnalysisRejected) as result:
            validate_observation(observation, evidence)
        self.assertNotIn("secret input", str(result.exception))

    def test_hypothesis_rejects_unhashable_classification_in_supplied_observation(self):
        from cloud.technical_analysis import validate_hypothesis
        hypothesis = {"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs"],
                      "hypothesis": "Recovery could matter.", "confidence": 0.4, "alternative": "Burst smoothing.",
                      "falsification": "Inspect public material."}
        with self.assertRaises(AnalysisRejected):
            validate_hypothesis(hypothesis, {"obs": {"classification": ["secret input"], "requires_human_review": True}})

    def test_invalid_unicode_source_is_rejected_without_exposing_evidence(self):
        evidence = {"body": "secret\ud800", "url": "https://example.org/post", "source_id": "source:test"}
        with self.assertRaises(AnalysisRejected) as result:
            validate_observation({}, evidence)
        self.assertNotIn("secret", str(result.exception))

    def test_unhashable_observation_reference_is_rejected_without_crashing(self):
        proposal = {"hypothesis_ids": ["hyp"], "before": "Current design.", "after": "Bound retries.",
                    "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
                    "validation_experiment": "Measure retries.", "counterargument": "Retries may help.",
                    "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                                  "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
        malformed = {"classification": "inferred", "observation_ids": [["secret input"]],
                     "requires_human_review": True}
        with self.assertRaises(AnalysisRejected) as result:
            validate_opportunity(proposal, {"hyp": malformed}, {}, {})
        self.assertNotIn("secret input", str(result.exception))

    def test_unhashable_benchmark_reference_is_rejected_without_crashing(self):
        from tests.test_technical_analysis import source
        from cloud.technical_analysis import validate_hypothesis
        evidence = source()
        observation = validate_observation({"classification": "source_statement", "source_id": evidence["source_id"],
            "source_sha256": evidence["sha256"], "quote": "stores work durably"}, evidence)
        hypothesis = validate_hypothesis({"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs"],
            "hypothesis": "Recovery could matter.", "confidence": 0.4, "alternative": "Burst smoothing.",
            "falsification": "Inspect public material."}, {"obs": observation})
        proposal = {"hypothesis_ids": ["hyp"], "before": "Current design.", "after": "Bound retries.",
                    "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
                    "validation_experiment": "Measure retries.", "counterargument": "Retries may help.",
                    "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                                  "security": "access", "developer_productivity": "tests"},
                    "benchmarks": [{"value": "5ms", "observation_id": ["secret input"]}]}
        with self.assertRaises(AnalysisRejected) as result:
            validate_opportunity(proposal, {"hyp": hypothesis}, {"obs": observation}, {evidence["source_id"]: evidence})
        self.assertNotIn("secret input", str(result.exception))

    def test_unhashable_source_reference_is_rejected_without_crashing(self):
        proposal = {"hypothesis_ids": ["hyp"], "before": "Current design.", "after": "Bound retries.",
                    "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
                    "validation_experiment": "Measure retries.", "counterargument": "Retries may help.",
                    "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                                  "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
        hypothesis = {"classification": "inferred", "observation_ids": ["obs"], "requires_human_review": True}
        observation = {"classification": "source_statement", "source_id": ["secret input"]}
        with self.assertRaises(AnalysisRejected) as result:
            validate_opportunity(proposal, {"hyp": hypothesis}, {"obs": observation}, {})
        self.assertNotIn("secret input", str(result.exception))
