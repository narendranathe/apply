"""Synthetic checks for numerical claims; no claim of semantic verification."""
import hashlib
import unittest

from cloud.technical_analysis import AnalysisRejected, validate_hypothesis, validate_observation, validate_opportunity


def case(body="The queue stores work durably before acknowledgment."):
    evidence = {"source_id": "source:test", "url": "https://example.org/blog", "body": body,
                "sha256": hashlib.sha256(body.encode()).hexdigest()}
    observation = validate_observation({"classification": "source_statement", "source_id": "source:test",
        "source_sha256": evidence["sha256"], "quote": body}, evidence)
    hypothesis = validate_hypothesis({"kind": "inferred", "scope": "engineering_hypothesis",
        "observation_ids": ["obs"], "confidence": 0.4, "hypothesis": "Recovery may matter.",
        "alternative": "Perhaps capacity matters.", "falsification": "Inspect public material."}, {"obs": observation})
    proposal = {"hypothesis_ids": ["hyp"], "before": "Current queue.", "after": "Add replay.",
        "failure_modes": "Retries can fail.", "feasibility": "Depends on ordering.",
        "validation_experiment": "Measure replay.", "counterargument": "State adds complexity.",
        "tradeoffs": {"latency": "reads", "cost": "storage", "reliability": "replay",
                      "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
    return proposal, {"hyp": hypothesis}, {"obs": observation}, {"source:test": evidence}


class NumericClaimTests(unittest.TestCase):
    def test_uncited_common_numeric_measurement_forms_are_rejected(self):
        proposal, hypotheses, observations, sources = case()
        for text in ("Save 99 percent storage.", "Completes in 5 milliseconds.", "Completes in 10 seconds.",
                     "Reduce latency by 9 MS.", "Save 1,000 percent storage.", "Delay 1e3 ms."):
            with self.subTest(text=text), self.assertRaises(AnalysisRejected):
                validate_opportunity({**proposal, "after": text}, hypotheses, observations, sources)

    def test_exact_cited_percent_still_accepts_existing_supported_spelling(self):
        proposal, hypotheses, observations, sources = case("A synthetic example reports 50% overhead.")
        proposal["benchmarks"] = [{"value": "50%", "observation_id": "obs"}]
        for text in ("The source reports 50 % overhead.", "The source reports 50 percent overhead."):
            with self.subTest(text=text):
                result = validate_opportunity({**proposal, "after": text}, hypotheses, observations, sources)
                self.assertTrue(result["requires_human_review"])
                self.assertFalse(result["semantic_support_verified"])

    def test_percent_is_not_treated_as_percentage_points(self):
        proposal, hypotheses, observations, sources = case("A synthetic example reports 50% overhead.")
        proposal.update(after="The change reduces latency by 50 percentage points.",
                        benchmarks=[{"value": "50%", "observation_id": "obs"}])
        with self.assertRaises(AnalysisRejected):
            validate_opportunity(proposal, hypotheses, observations, sources)

    def test_a_cited_positive_measurement_does_not_support_a_negative_one(self):
        proposal, hypotheses, observations, sources = case("A synthetic example reports 50% overhead.")
        proposal["benchmarks"] = [{"value": "50%", "observation_id": "obs"}]
        for text in ("The result is -50%.", "The result is −50 percent."):
            with self.subTest(text=text), self.assertRaises(AnalysisRejected):
                validate_opportunity({**proposal, "after": text}, hypotheses, observations, sources)
