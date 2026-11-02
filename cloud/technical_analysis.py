"""Offline structural checks for technical claims; not a truth verifier.

Evidence bodies and proposals are untrusted caller input. Passing a check does
not authenticate a source, establish entailment, or approve publication. A
human must review claims and the host must independently authenticate sources.
"""
from hashlib import sha256
import math
import re

from .technical_records import canonical_technical_url


class AnalysisRejected(ValueError):
    """Return fixed error codes, never private evidence text."""


def _bounded_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def validate_observation(observation, evidence):
    if not isinstance(observation, dict) or not isinstance(evidence, dict):
        raise AnalysisRejected("invalid_evidence")
    body = evidence.get("body")
    if not isinstance(body, str) or len(body) > 1_000_000:
        raise AnalysisRejected("invalid_evidence")
    try:
        body_bytes = body.encode("utf-8")
    except UnicodeEncodeError:
        raise AnalysisRejected("invalid_evidence") from None
    if len(body_bytes) > 1_000_000:
        raise AnalysisRejected("invalid_evidence")
    try:
        canonical_technical_url(evidence.get("url"))
    except ValueError:
        raise AnalysisRejected("invalid_evidence") from None
    digest = sha256(body_bytes).hexdigest()
    quote = observation.get("quote")
    if (not isinstance(observation.get("classification"), str)
            or observation["classification"] not in {"source_statement", "author_opinion"}
            or not isinstance(evidence.get("source_id"), str) or not evidence["source_id"]
            or observation.get("source_id") != evidence["source_id"]
            or digest != evidence.get("sha256") or observation.get("source_sha256") != digest
            or not isinstance(quote, str) or not quote.strip() or len(quote) > 240
            or len(quote.split()) > 25 or quote not in body):
        raise AnalysisRejected("observation_not_cited")
    return {"classification": observation["classification"], "source_id": evidence["source_id"],
            "source_sha256": digest, "quote": quote, "requires_human_review": True,
            "source_independently_verified": False}


def validate_hypothesis(hypothesis, observations):
    if not isinstance(hypothesis, dict) or not isinstance(observations, dict):
        raise AnalysisRejected("hypothesis_invalid")
    refs, confidence = hypothesis.get("observation_ids"), hypothesis.get("confidence")
    if (hypothesis.get("kind") != "inferred" or hypothesis.get("scope") != "engineering_hypothesis"
            or not isinstance(refs, list) or not refs or len(refs) > 20
            or any(not isinstance(ref, str) or not ref or ref not in observations for ref in refs)
            or len(set(refs)) != len(refs)
            or type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or any(not _bounded_text(hypothesis.get(field)) for field in ("hypothesis", "alternative", "falsification"))):
        raise AnalysisRejected("hypothesis_invalid")
    if any(not isinstance(observations[ref], dict)
           or not isinstance(observations[ref].get("classification"), str)
           or observations[ref].get("classification") not in {"source_statement", "author_opinion"}
           or observations[ref].get("requires_human_review") is not True for ref in refs):
        raise AnalysisRejected("observation_unavailable")
    return {"classification": "inferred", "scope": "engineering_hypothesis", "observation_ids": list(refs), "confidence": confidence,
            "hypothesis": hypothesis["hypothesis"], "alternative": hypothesis["alternative"],
            "falsification": hypothesis["falsification"], "requires_human_review": True,
            "source_independently_verified": False}


def validate_opportunity(proposal, hypotheses, observations, sources):
    if not all(isinstance(value, dict) for value in (proposal, hypotheses, observations, sources)):
        raise AnalysisRejected("opportunity_invalid")
    refs = proposal.get("hypothesis_ids")
    fields = ("before", "after", "failure_modes", "feasibility", "validation_experiment", "counterargument")
    tradeoffs = proposal.get("tradeoffs")
    axes = ("latency", "cost", "reliability", "security", "developer_productivity")
    if (not isinstance(refs, list) or not refs or len(refs) > 20
            or any(not isinstance(ref, str) or ref not in hypotheses for ref in refs)
            or len(refs) != len(set(refs)) or not isinstance(tradeoffs, dict)
            or any(not _bounded_text(proposal.get(field)) for field in fields)
            or any(not _bounded_text(tradeoffs.get(axis)) for axis in axes)):
        raise AnalysisRejected("opportunity_invalid")
    observation_ids = set()
    for ref in refs:
        hypothesis = hypotheses[ref]
        if (not isinstance(hypothesis, dict) or hypothesis.get("classification") != "inferred"
                or hypothesis.get("requires_human_review") is not True
                or not isinstance(hypothesis.get("observation_ids"), list)
                or any(not isinstance(ref, str) or not ref for ref in hypothesis["observation_ids"])):
            raise AnalysisRejected("hypothesis_unavailable")
        observation_ids.update(hypothesis["observation_ids"])
    if not observation_ids or any(ref not in observations for ref in observation_ids):
        raise AnalysisRejected("observation_unavailable")
    validated_observations = {}
    for ref in observation_ids:
        observation = observations[ref]
        if not isinstance(observation, dict) or not isinstance(observation.get("source_id"), str):
            raise AnalysisRejected("observation_unavailable")
        validated_observations[ref] = validate_observation(observation, sources.get(observation.get("source_id")))
    for ref in refs:
        hypothesis = hypotheses[ref]
        validate_hypothesis({**hypothesis, "kind": hypothesis.get("classification")}, validated_observations)
    benchmarks = proposal.get("benchmarks")
    if not isinstance(benchmarks, list) or len(benchmarks) > 20:
        raise AnalysisRejected("benchmark_invalid")
    values = set()
    for benchmark in benchmarks:
        if not isinstance(benchmark, dict):
            raise AnalysisRejected("benchmark_invalid")
        value, ref = benchmark.get("value"), benchmark.get("observation_id")
        if (not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?(?:%|x|ms|s)", value)
                or not isinstance(ref, str) or ref not in observation_ids):
            raise AnalysisRejected("benchmark_invalid")
        observation = validated_observations[ref]
        if not re.search(r"(?<![A-Za-z0-9.])" + re.escape(value) + r"(?![A-Za-z0-9.])", observation["quote"]):
            raise AnalysisRejected("benchmark_not_cited")
        values.add(value)
    texts = [proposal[field] for field in fields] + [tradeoffs[axis] for axis in axes]
    for text in texts:
        numeric_claims = re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:%|x|ms|s)(?![A-Za-z])", text)
        numeric_claims = [re.sub(r"\s+", "", claim) for claim in numeric_claims]
        if not set(numeric_claims) <= values:
            raise AnalysisRejected("benchmark_not_cited")
    return {"classification": "proposed", "hypothesis_ids": list(refs),
            "requires_human_review": True, "source_independently_verified": False,
            "novelty_independently_verified": False, "semantic_support_verified": False}
