"""Synthetic structural analysis checks; no provider or real personal data."""
import hashlib
import unittest


def source():
    body = "The queue stores work durably before a worker acknowledges it."
    return {"source_id": "source:fixture", "url": "https://example.org/post",
            "body": body, "sha256": hashlib.sha256(body.encode()).hexdigest(), "kind": "article"}


class TechnicalAnalysisTests(unittest.TestCase):
    def test_observation_requires_an_exact_source_revision_and_literal_citation(self):
        from cloud.technical_analysis import validate_observation, AnalysisRejected
        evidence = source()
        observation = {"classification": "source_statement", "source_id": evidence["source_id"],
                       "source_sha256": evidence["sha256"], "quote": "stores work durably before a worker acknowledges it"}
        result = validate_observation(observation, evidence)
        self.assertTrue(result["requires_human_review"])
        self.assertFalse(result["source_independently_verified"])
        for invalid in ({**observation, "quote": "the company has a secret roadmap"},
                        {**observation, "source_sha256": "0" * 64},
                        {**observation, "source_id": "source:other"}):
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisRejected):
                validate_observation(invalid, evidence)

    def test_hypothesis_is_labeled_inference_with_alternatives_and_falsification(self):
        from cloud.technical_analysis import validate_observation, validate_hypothesis, AnalysisRejected
        evidence = source()
        observation = validate_observation({"classification": "author_opinion", "source_id": evidence["source_id"],
            "source_sha256": evidence["sha256"], "quote": "queue stores work durably"}, evidence)
        draft = {"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs:1"],
                 "hypothesis": "The team may value recovery after worker failures.", "confidence": 0.5,
                 "alternative": "The design may instead be driven by burst capacity.",
                 "falsification": "Review public failure-recovery testing descriptions."}
        result = validate_hypothesis(draft, {"obs:1": observation})
        self.assertEqual(result["classification"], "inferred")
        self.assertTrue(result["requires_human_review"])
        for invalid in ({**draft, "kind": "fact"}, {**draft, "scope": "secret_roadmap"},
                        {**draft, "observation_ids": ["obs:missing"]}, {**draft, "confidence": True},
                        {**draft, "alternative": ""}, {**draft, "falsification": ""}):
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisRejected):
                validate_hypothesis(invalid, {"obs:1": observation})

    def test_opportunity_needs_specific_design_tradeoffs_and_supported_benchmarks(self):
        from cloud.technical_analysis import validate_observation, validate_hypothesis, validate_opportunity, AnalysisRejected
        evidence = source()
        obs = validate_observation({"classification": "source_statement", "source_id": evidence["source_id"],
            "source_sha256": evidence["sha256"], "quote": "stores work durably"}, evidence)
        hyp = validate_hypothesis({"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs:1"],
            "hypothesis": "Recovery could matter.", "confidence": 0.4, "alternative": "Burst smoothing.",
            "falsification": "Inspect public recovery material."}, {"obs:1": obs})
        proposal = {"hypothesis_ids": ["hyp:1"], "before": "Durable queue without replay bounds.",
            "after": "Idempotent consumers with replay watermarks.", "failure_modes": "Poison messages can stall partitions.",
            "feasibility": "Requires ordering and state constraints.", "validation_experiment": "Inject duplicate and delayed events.",
            "counterargument": "Watermarks add state and operational load.",
            "tradeoffs": {"latency": "extra state reads", "cost": "storage and retries", "reliability": "less duplicate impact",
                          "security": "restrict replay access", "developer_productivity": "more test setup"}, "benchmarks": []}
        result = validate_opportunity(proposal, {"hyp:1": hyp}, {"obs:1": obs}, {"source:fixture": evidence})
        self.assertTrue(result["requires_human_review"])
        self.assertFalse(result["novelty_independently_verified"])
        for invalid in ({**proposal, "hypothesis_ids": ["hyp:missing"]},
                        {**proposal, "counterargument": ""},
                        {**proposal, "tradeoffs": {"latency": "faster"}},
                        {**proposal, "after": "The change reduces latency by 99%."},
                        {**proposal, "benchmarks": [{"value": "99%", "observation_id": "obs:1"}]}):
            with self.subTest(invalid=invalid), self.assertRaises(AnalysisRejected):
                validate_opportunity(invalid, {"hyp:1": hyp}, {"obs:1": obs}, {"source:fixture": evidence})

    def test_benchmark_must_be_a_complete_number_in_the_cited_quote(self):
        from cloud.technical_analysis import validate_observation, validate_hypothesis, validate_opportunity, AnalysisRejected
        body = "A synthetic test reports 199% additional storage overhead."
        evidence = {"source_id": "source:test", "url": "https://example.org/a", "body": body,
                    "sha256": hashlib.sha256(body.encode()).hexdigest(), "kind": "article"}
        obs = validate_observation({"classification": "source_statement", "source_id": "source:test",
            "source_sha256": evidence["sha256"], "quote": body}, evidence)
        hyp = validate_hypothesis({"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs"],
            "hypothesis": "Storage cost may be a concern.", "confidence": 0.4,
            "alternative": "Capacity may not matter.", "falsification": "Measure cost."}, {"obs": obs})
        proposal = {"hypothesis_ids": ["hyp"], "before": "Original queue.", "after": "Save 99% storage.",
            "failure_modes": "Replay failures.", "feasibility": "Depends on volume.",
            "validation_experiment": "Measure storage.", "counterargument": "Cost could increase.",
            "tradeoffs": {"latency": "reads", "cost": "storage", "reliability": "replay",
                          "security": "access", "developer_productivity": "testing"},
            "benchmarks": [{"value": "99%", "observation_id": "obs"}]}
        with self.assertRaises(AnalysisRejected):
            validate_opportunity(proposal, {"hyp": hyp}, {"obs": obs}, {"source:test": evidence})

    def test_opportunity_cannot_use_forged_or_missing_observation_source(self):
        from cloud.technical_analysis import validate_opportunity, AnalysisRejected
        hypothesis = {"classification": "inferred", "observation_ids": ["obs"], "requires_human_review": True}
        observation = {"classification": "source_statement", "requires_human_review": True, "source_id": "missing"}
        proposal = {"hypothesis_ids": ["hyp"], "before": "Original queue.", "after": "Bound retries.",
            "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
            "validation_experiment": "Measure retries.", "counterargument": "Retries may be useful.",
            "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                          "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
        with self.assertRaises(AnalysisRejected):
            validate_opportunity(proposal, {"hyp": hypothesis}, {"obs": observation}, {})

    def test_opportunity_rejects_a_forged_incomplete_hypothesis(self):
        from cloud.technical_analysis import validate_observation, validate_opportunity, AnalysisRejected
        evidence = source()
        obs = validate_observation({"classification": "source_statement", "source_id": evidence["source_id"],
            "source_sha256": evidence["sha256"], "quote": "stores work durably"}, evidence)
        fake = {"classification": "inferred", "observation_ids": ["obs"], "requires_human_review": True}
        proposal = {"hypothesis_ids": ["hyp"], "before": "Original queue.", "after": "Bound retries.",
            "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
            "validation_experiment": "Measure retries.", "counterargument": "Retries may be useful.",
            "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                          "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
        with self.assertRaises(AnalysisRejected):
            validate_opportunity(proposal, {"hyp": fake}, {"obs": obs}, {"source:fixture": evidence})

    def test_numeric_claim_with_whitespace_still_requires_evidence(self):
        from cloud.technical_analysis import validate_observation, validate_hypothesis, validate_opportunity, AnalysisRejected
        evidence = source()
        obs = validate_observation({"classification": "source_statement", "source_id": evidence["source_id"],
            "source_sha256": evidence["sha256"], "quote": "stores work durably"}, evidence)
        hyp = validate_hypothesis({"kind": "inferred", "scope": "engineering_hypothesis", "observation_ids": ["obs"],
            "hypothesis": "Recovery could matter.", "confidence": 0.4, "alternative": "Burst smoothing.",
            "falsification": "Inspect public material."}, {"obs": obs})
        proposal = {"hypothesis_ids": ["hyp"], "before": "Original queue.", "after": "Save 99 % storage.",
            "failure_modes": "Messages may fail.", "feasibility": "Depends on volume.",
            "validation_experiment": "Measure retries.", "counterargument": "Costs may rise.",
            "tradeoffs": {"latency": "backoff", "cost": "storage", "reliability": "replay",
                          "security": "access", "developer_productivity": "tests"}, "benchmarks": []}
        with self.assertRaises(AnalysisRejected):
            validate_opportunity(proposal, {"hyp": hyp}, {"obs": obs}, {"source:fixture": evidence})
