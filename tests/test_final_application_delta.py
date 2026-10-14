import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cloud.applications import fold_applications
from cloud.migration import application_delta, combine_exports
from scripts.archive_application_delta import capture, orphan_registry


def fixture():
    files, observations = {}, []
    for company, role, folder, url, status in (
        ("Google", "Analytics Engineer", "2026-10-14-google-analytics-engineer",
         "https://www.linkedin.com/jobs/view/4477916471/", "applied"),
        ("Coinbase", "Senior Software Engineer, Data Platform", "2026-10-14-coinbase-data-platform",
         "https://boards.greenhouse.io/coinbase/jobs/7812407", "draft_ready"),
    ):
        prefix = "applications/" + folder + "/"
        jd = f"# {company}, {role}\nURL: {url}\n".encode()
        state = json.dumps({"status": status, "claimed_by": "legacy-agent", "claimed_at": "2026-10-14"}).encode()
        files[prefix + "jd.md"] = jd
        files[prefix + "status.json"] = state
        files[prefix + "resume.tex"] = b"Unreviewed historical source"
        observations.append({"folder": folder, "company": company, "role": role, "url": url,
                             "jd_sha256": hashlib.sha256(jd).hexdigest(),
                             "status_sha256": hashlib.sha256(state).hexdigest()})
    return files, {"schema": "apply-orphan-application-observations/v1", "observations": observations}


class FinalApplicationDeltaTests(unittest.TestCase):
    def test_raw_applied_is_unknown_and_draft_is_never_qa_ready(self):
        files, observations = fixture()
        registry, identities = orphan_registry(files, observations, {})
        files["applications/registry.json"] = json.dumps(registry).encode()
        files["audit/identity-observations.json"] = json.dumps(identities).encode()
        delta = application_delta(files, source_commit="c" * 40, base_commit="b" * 40)
        states = {row["payload"]["company"]: row for row in delta["records"] if row["kind"] == "applications"}
        self.assertEqual(states["Google"]["status"], "submission_unknown")
        self.assertEqual(states["Coinbase"]["status"], "prepared")
        for row in states.values():
            payload = row["payload"]
            self.assertFalse(payload["official_verified"])
            self.assertFalse(payload["receipt_verified"])
            self.assertFalse(payload["qa_verified"])
            self.assertFalse(payload["answers_verified"])
            self.assertFalse(payload["replay_allowed"])
            self.assertFalse(payload["packet"]["ready"])
        self.assertEqual(len(combine_exports([delta])["records"]), 5)
        self.assertEqual(len(fold_applications(list(states.values()))), 2)

    def test_hash_mismatch_or_invented_identity_fails_closed(self):
        files, observations = fixture()
        wrong_hash = copy.deepcopy(observations)
        wrong_hash["observations"][0]["jd_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            orphan_registry(files, wrong_hash, {})
        invented = copy.deepcopy(observations)
        invented["observations"][0]["company"] = "InventedEmployer"
        with self.assertRaisesRegex(ValueError, "exact captured JD"):
            orphan_registry(files, invented, {})

    def test_every_tracked_orphan_is_explicit_and_duplicates_are_rejected(self):
        files, observations = fixture()
        incomplete = copy.deepcopy(observations)
        incomplete["observations"].pop()
        with self.assertRaisesRegex(ValueError, "Every tracked orphan"):
            orphan_registry(files, incomplete, {})
        registered = {"old": {"folder": observations["observations"][0]["folder"]}}
        with self.assertRaisesRegex(ValueError, "already registered"):
            orphan_registry(files, observations, registered)

    def test_capture_preserves_raw_ignored_registry_and_binds_derived_provenance(self):
        files, observations = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for path, body in {**files, "AGENTS.md": b"contract", "docs/ground-truth.md": b"facts",
                               ".gitignore": b"applications/registry.json\n.migration-private/\n"}.items():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "fixture"], cwd=repo, check=True)
            raw = b'{"historical": {"folder":"legacy", "status":"applied"}}\n'
            local_registry = repo / "applications/registry.json"
            local_registry.write_bytes(raw)
            observation_path = repo / ".migration-private/observations.json"
            observation_path.parent.mkdir()
            observation_path.write_text(json.dumps(observations))
            _, _, captured = capture(repo, "HEAD", "HEAD", repo / ".migration-private/staging",
                                      compatibility_registry=local_registry, orphan_observations=observation_path)
            self.assertEqual(captured["audit/private-compatibility-registry.json"], raw)
            self.assertEqual(local_registry.read_bytes(), raw)
            derived = json.loads(captured["applications/registry.json"])
            self.assertEqual(len(derived), 2)
            self.assertNotIn("historical", derived)
            provenance = json.loads(captured["audit/registry-capture-provenance.json"])
            self.assertEqual(provenance["private_source_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertFalse(provenance["derived_registry_is_operational"])
            self.assertFalse(provenance["replay_allowed"])
            with self.assertRaisesRegex(ValueError, "source hashes differ"):
                changed = copy.deepcopy(observations)
                changed["observations"][0]["status_sha256"] = "0" * 64
                orphan_registry(files, changed, {})


if __name__ == "__main__":
    unittest.main()
