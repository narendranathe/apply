import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs"))
import campaign_planner as planner


TODAY = "2026-10-01"  # Thursday
POLICY = {"version": 4, "max_new_requests_per_run": 6, "max_new_requests_per_day": 6,
          "max_new_contacts_per_company_per_7_days": 3, "company_contact_coverage_target": 4,
          "role_cache_days": 7, "role_mix_ai_share": 0.6, "research_batch_size": 3,
          "new_outreach_days": ["MO", "TU", "WE", "TH"]}
CONTACT_CHANNELS = {"referral_first_degree", "recruiter_linkedin", "hiring_manager", "public_email"}


def role(employer="Acme", job="1", **changes):
    row = {"employer": employer, "ats": "greenhouse", "board": employer.lower(), "role_key": f"{employer.lower()}:{job}",
           "job_id": job, "title": "Data Engineer", "url": f"https://boards.example/{employer}/{job}",
           "posted_at": "2026-09-30T15:00:00Z", "freshness": "posted", "age_days": 1.2, "fit_score": 50,
           "planner_role_family": "data", "flags": [], "exclude_reason": None, "public_recruiting_emails": [],
           "fit": {"production_evidence": ["SQL Server/T-SQL"], "independent_evidence": [], "gaps": []},
           "first_seen": "2026-10-01T21:00:00Z"}
    row.update(changes)
    return row


def scan(*roles):
    return {"generated_at": "2026-10-01T21:00:00Z", "roles": list(roles)}


def plays(snapshot=None, scan_report=None, **policy_changes):
    policy = dict(POLICY, **policy_changes)
    return planner.plan(snapshot or {"campaigns": []}, policy, TODAY, scan_report)


class MultichannelPlayTests(unittest.TestCase):
    def test_without_scan_output_is_backward_compatible(self):
        result = planner.plan({"campaigns": []}, POLICY, TODAY)
        self.assertEqual(result["plays"], [])
        self.assertNotIn("play_gates", result)
        self.assertFalse(result["sending_authorized"])
        self.assertEqual([a["action"] for a in result["actions"]], ["discover_roles", "discover_roles"])

    def test_fresh_role_gets_ordered_play_and_never_authorizes_sending(self):
        result = plays(scan_report=scan(role()))
        self.assertFalse(result["sending_authorized"])
        self.assertTrue(result["play_gates"])
        play = result["plays"][0]
        channels = [s["channel"] for s in play["steps"]]
        self.assertEqual(channels, ["fresh_apply", "referral_first_degree", "recruiter_linkedin", "proof_of_work",
                                    "hiring_manager", "community"])
        self.assertNotIn("public_email", channels, "no publicly listed inbox, so no email step")
        apply_step = play["steps"][0]
        self.assertTrue(apply_step["requires_naren"])
        self.assertFalse(apply_step["counts_toward_contact_caps"])
        self.assertEqual(apply_step["apply_by"], "2026-10-02")
        self.assertEqual(play["steps"][1]["fallback_channel"], "alumni")
        for step in play["steps"]:
            self.assertNotIn("send", step["action"])
        self.assertEqual(play["campaign_seed"]["fit"], None, "scanner score is not qualification")
        self.assertIsNone(play["campaign_seed"]["mandatory_blocker"])

    def test_contact_steps_only_on_outreach_days_and_strictly_spaced(self):
        play = plays(scan_report=scan(role()))["plays"][0]
        dates = [s["target_date"] for s in play["steps"] if s["channel"] in CONTACT_CHANNELS]
        self.assertEqual(dates, ["2026-10-01", "2026-10-05", "2026-10-06"], "Thu, then Mon, then Tue")
        self.assertEqual(len(set(dates)), len(dates))

    def test_company_cap_counts_recent_touches_and_shares_slots_across_roles(self):
        snapshot = {"campaigns": [{"company": "Acme", "role_key": "acme:old", "contacts": [
            {"profile_url": f"https://www.linkedin.com/in/p{i}/", "kind": "recruiter", "state": "pending",
             "last_touch_at": "2026-09-29"} for i in range(2)]}]}
        result = plays(snapshot, scan(role(job="1"), role(job="2", title="Analytics Engineer", fit_score=40)))
        contact_steps = [s for p in result["plays"] for s in p["steps"] if s["channel"] in CONTACT_CHANNELS]
        scheduled = [s for s in contact_steps if s["status"] == "proposed"]
        held = [s for s in contact_steps if s["status"] == "held_company_cap"]
        self.assertEqual(len(scheduled), 1, "3 per company per 7 days minus 2 recent touches")
        self.assertEqual(len(held), 5)
        self.assertEqual(result["plays"][0]["company_contacts_known"], {"pending": 2})

    def test_daily_cap_is_never_exceeded(self):
        roles = [role(employer=f"Co{i}", job=str(i), fit_score=60 - i) for i in range(5)]
        result = plays(scan_report=scan(*roles), max_new_requests_per_day=4)
        per_day = {}
        for play in result["plays"]:
            for step in play["steps"]:
                if step["channel"] in CONTACT_CHANNELS and step["status"] == "proposed":
                    per_day[step["target_date"]] = per_day.get(step["target_date"], 0) + 1
        self.assertTrue(per_day)
        self.assertTrue(all(count <= 4 for count in per_day.values()), per_day)
        for day in per_day:
            self.assertIn(planner.WEEKDAY_CODES[planner._day(day).weekday()], {"MO", "TU", "WE", "TH"})

    def test_known_sends_today_reduce_day_zero_capacity(self):
        result = plays({"campaigns": [], "pacing": {"new_requests_today": 6}}, scan(role()))
        referral = result["plays"][0]["steps"][1]
        self.assertEqual(referral["target_date"], "2026-10-05")

    def test_explicit_exclusions_stale_and_update_only_roles_get_no_play(self):
        roles = [role(job="x", exclude_reason="explicit role-level exclusion: no_sponsorship_language",
                      flags=["no_sponsorship_language"]),
                 role(job="old", posted_at="2026-09-20T00:00:00Z"),
                 role(job="upd", freshness="updated", posted_at="2026-06-01T00:00:00Z",
                      updated_at="2026-09-30T00:00:00Z")]
        result = plays(scan_report=scan(*roles))
        self.assertEqual(result["plays"], [])
        self.assertEqual(sum(result["plays_skipped_counts"].values()), 3)
        self.assertEqual([r["role_key"] for r in result["plays_skipped"]], ["acme:x"])
        allowed = plays(scan_report=scan(roles[2]), plays_include_updated_only=True)
        self.assertEqual(len(allowed["plays"]), 1)

    def test_seniority_flags_hold_everything_but_qualification(self):
        play = plays(scan_report=scan(role(flags=["staff_or_principal_title", "experience_8plus_years"])))["plays"][0]
        self.assertEqual(play["status"], "qualify_first")
        self.assertEqual(play["steps"][0]["status"], "qualify_first")
        self.assertTrue(all(s["status"] == "held_qualification" for s in play["steps"][1:]))
        self.assertEqual(play["company_new_contact_slots_left"], 3, "held plays consume no contact slots")

    def test_snapshot_campaign_state_is_respected(self):
        closed = {"campaigns": [{"company": "Acme", "role_key": "acme:1", "role_status": "closed"}]}
        self.assertEqual(plays(closed, scan(role()))["plays"], [])
        waiting = {"campaigns": [{"company": "Acme", "role_url": "https://boards.example/Acme/1",
                                  "role_status": "open", "next_review_at": "2026-10-03"}]}
        self.assertIn("waiting", plays(waiting, scan(role()))["plays_skipped"][0]["reason"])
        existing = {"campaigns": [{"company": "Acme", "role_key": "acme:1", "role_status": "open",
                                   "next_review_at": "2026-10-01"}]}
        play = plays(existing, scan(role()))["plays"][0]
        self.assertEqual(play["existing_campaign"], "acme:1")
        self.assertIsNone(play["campaign_seed"])
        cooldown = plays({"campaigns": [], "company_cooldowns": {"Acme": "2026-10-10"}}, scan(role()))
        self.assertEqual(cooldown["plays"], [])

    def test_public_email_step_only_when_employer_listed_one(self):
        play = plays(scan_report=scan(role(public_recruiting_emails=["recruiting@acme.example"])))["plays"][0]
        email = [s for s in play["steps"] if s["channel"] == "public_email"]
        self.assertEqual(len(email), 1)
        self.assertTrue(email[0]["requires_naren"])
        self.assertIn("Never guess", email[0]["note"])

    def test_max_plays_and_ranking(self):
        roles = [role(employer=f"Co{i}", job=str(i), fit_score=i * 10) for i in range(4)]
        result = plays(scan_report=scan(*roles), max_plays_per_run=2)
        self.assertEqual([p["company"] for p in result["plays"]], ["Co3", "Co2"])
        self.assertEqual(result["plays_not_selected"], 2)

    def test_policy_cannot_loosen_safety_fields(self):
        policy_channels = {"recruiter_linkedin": {"counts_toward_contact_caps": False},
                           "fresh_apply": {"requires_naren": False}, "community": {"enabled": False}}
        play = plays(scan_report=scan(role()), channels=policy_channels)["plays"][0]
        steps = {s["channel"]: s for s in play["steps"]}
        self.assertTrue(steps["recruiter_linkedin"]["counts_toward_contact_caps"])
        self.assertTrue(steps["fresh_apply"]["requires_naren"])
        self.assertNotIn("community", steps)

    def test_live_policy_file_loads_and_keeps_caps(self):
        path = Path(planner.__file__).resolve().parent / "campaign_policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(policy["max_new_contacts_per_company_per_7_days"], 3)
        self.assertEqual(policy["max_new_requests_per_day"], 6)
        self.assertFalse(policy["paid_api_calls_enabled"])
        result = planner.plan({"campaigns": []}, policy, TODAY, scan(role()))
        self.assertFalse(result["sending_authorized"])
        self.assertEqual(len(result["plays"]), 1)

    def test_cli_accepts_scan_without_modifying_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {"snapshot": root / "s.json", "policy": root / "p.json", "scan": root / "scan.json"}
            paths["snapshot"].write_text(json.dumps({"campaigns": []}), encoding="utf-8")
            paths["policy"].write_text(json.dumps(POLICY), encoding="utf-8")
            paths["scan"].write_text(json.dumps(scan(role())), encoding="utf-8")
            before = {k: v.read_bytes() for k, v in paths.items()}
            proc = subprocess.run([sys.executable, str(Path(planner.__file__)), "--input", str(paths["snapshot"]),
                                   "--policy", str(paths["policy"]), "--scan", str(paths["scan"]), "--today", TODAY],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            output = json.loads(proc.stdout)
            self.assertFalse(output["sending_authorized"])
            self.assertEqual(len(output["plays"]), 1)
            self.assertEqual(before, {k: v.read_bytes() for k, v in paths.items()})


if __name__ == "__main__":
    unittest.main()
