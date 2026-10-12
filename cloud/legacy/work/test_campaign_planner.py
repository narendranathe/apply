import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs"))
import campaign_planner as planner


TODAY = "2026-09-30"
POLICY = {"version": 4, "max_new_requests_per_run": 6,
          "max_new_contacts_per_company_per_7_days": 3,
          "company_contact_coverage_target": 4, "role_cache_days": 7,
          "role_mix_ai_share": 0.6, "research_batch_size": 3}


def campaign(company="Acme", role="r1", **changes):
    result = {"company": company, "role_key": role, "role_title": "Data Platform Engineer",
              "role_url": "https://example.com/job", "role_verified_at": TODAY,
              "role_status": "open", "role_family": "data", "fit": "direct",
              "mandatory_blocker": False, "contacts": [], "next_review_at": None}
    result.update(changes)
    return result


def actions(snapshot, **policy_changes):
    policy = dict(POLICY, **policy_changes)
    return planner.plan(snapshot, policy, TODAY)["actions"]


class PlannerTests(unittest.TestCase):
    def test_duplicates_across_roles_are_deduped_and_restrictive_state_wins(self):
        a = campaign(role="a", contacts=[{"profile_url": "https://www.linkedin.com/in/A/?trk=x",
                                             "kind": "manager", "state": "uncontacted"}])
        b = campaign(role="b", contacts=[{"profile_url": "https://linkedin.com/in/A",
                                             "kind": "recruiter", "state": "pending"}])
        acts = actions({"campaigns": [a, b]})
        self.assertFalse(any(x["action"] == "verify_contact" for x in acts))
        self.assertEqual(sum(x["action"] == "research_recruiter_or_manager" for x in acts), 2)

    def test_pending_unknown_and_blockers_never_become_contact_verification(self):
        for state in ("pending", "delivery_unknown", "ambiguous", "suppressed", "exhausted"):
            c = campaign(contacts=[{"profile_url": "https://linkedin.com/in/x/", "kind": "manager", "state": state}])
            self.assertFalse(any(x["action"] == "verify_contact" for x in actions({"campaigns": [c]})))
        c = campaign(role_status="unknown", contacts=[{"profile_url": "https://linkedin.com/in/x/",
                 "kind": "manager", "state": "uncontacted"}])
        self.assertFalse(any(x["action"] == "verify_contact" for x in actions({"campaigns": [c]})))
        c["role_status"] = "open"
        c["mandatory_blocker"] = None
        self.assertFalse(any(x["action"] == "verify_contact" for x in actions({"campaigns": [c]})))

    def test_empty_queue_replenishes_by_research(self):
        c = campaign(contacts=[{"profile_url": "", "kind": "peer", "state": "uncontacted"}])
        self.assertEqual(actions({"campaigns": [c]})[0]["action"], "research_recruiter_or_manager")

    def test_empty_campaigns_discover_roles_only_with_configured_companies(self):
        plan = planner.plan({"campaigns": []}, dict(POLICY, discovery_companies=["Acme", "AI Co"]), TODAY)
        self.assertEqual([a["action"] for a in plan["actions"]], ["discover_roles"] * 2)
        self.assertTrue(all("profile_url" not in a for a in plan["actions"]))
        self.assertTrue(all(a["status"] == "unresolved_evidence" and a.get("reason") for a in plan["actions"]))
        generic = planner.plan({"campaigns": []}, POLICY, TODAY)["actions"]
        self.assertEqual([a["role_family"] for a in generic], ["applied_ai", "data"])

    def test_emitted_verification_is_deduped_across_roles(self):
        url = "https://www.linkedin.com/in/Unique/"
        campaigns = [campaign(role="a", contacts=[{"profile_url": url, "kind": "manager", "state": "uncontacted"}]),
                     campaign(role="b", contacts=[{"profile_url": url + "?trk=foo", "kind": "manager",
                                                   "state": "uncontacted"}])]
        result = planner.plan({"campaigns": campaigns}, dict(POLICY, research_batch_size=6), TODAY)["actions"]
        emitted = [a["profile_url"] for a in result if a["action"] == "verify_contact"]
        self.assertEqual(len(emitted), len(set(emitted)))

    def test_linkedin_identifier_case_is_preserved(self):
        upper = planner._profile_key("https://www.linkedin.com/in/ACoAbC/?trk=x")
        lower = planner._profile_key("https://linkedin.com/in/acoabc")
        self.assertEqual(upper, "https://www.linkedin.com/in/ACoAbC/")
        self.assertNotEqual(upper, lower)

    def test_future_role_verification_is_invalid_and_requires_refresh(self):
        c = campaign(role_verified_at="2026-10-01", contacts=[{"profile_url": "https://linkedin.com/in/x",
                     "kind": "manager", "state": "uncontacted"}])
        acts = actions({"campaigns": [c]})
        self.assertIn("refresh_role", [a["action"] for a in acts])
        self.assertNotIn("verify_contact", [a["action"] for a in acts])

    def test_missing_qualification_fields_never_verify_contact(self):
        for field in ("company", "role_identity", "role_family", "fit", "mandatory_blocker"):
            c = campaign(contacts=[{"profile_url": "https://linkedin.com/in/x", "kind": "manager",
                                    "state": "uncontacted"}])
            if field == "company":
                c[field] = ""
            elif field == "role_identity":
                c["role_key"] = None
                c["role_url"] = None
            elif field in {"role_family", "fit", "mandatory_blocker"}:
                c[field] = None
            acts = actions({"campaigns": [c]})
            self.assertNotIn("verify_contact", [a["action"] for a in acts], field)
            self.assertIn("research/qualify_role", [a["action"] for a in acts], field)

    def test_pending_contacts_yield_route_review(self):
        contacts = [{"profile_url": f"https://linkedin.com/in/p{i}", "kind": "recruiter",
                     "state": "pending"} for i in range(4)]
        acts = actions({"campaigns": [campaign(contacts=contacts)]})
        self.assertIn("review_route", [a["action"] for a in acts])
        self.assertTrue(all(a.get("reason") and a.get("status") == "unresolved_evidence" for a in acts))

    def test_known_unsuitable_roles_are_skipped_and_discovery_replaces_empty_plan(self):
        campaigns = [campaign(role="closed", role_status="closed"),
                     campaign(role="unsupported", fit="unsupported"),
                     campaign(role="blocked", mandatory_blocker=True)]
        result = planner.plan({"campaigns": campaigns}, POLICY, TODAY)["actions"]
        self.assertTrue(result)
        self.assertTrue(all(a["action"] == "discover_roles" for a in result))
        self.assertFalse(any(a["action"] == "research/qualify_role" for a in result))

    def test_all_future_reviews_remain_waiting_without_discovery(self):
        campaigns = [campaign(next_review_at="2026-10-01"), campaign("Other", "r2", next_review_at="2026-10-02")]
        self.assertEqual(planner.plan({"campaigns": campaigns}, POLICY, TODAY)["actions"], [])

    def test_one_campaign_emits_refresh_before_qualification(self):
        c = campaign(role_verified_at=None, fit=None)
        result = actions({"campaigns": [c]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["action"], "refresh_role")

    def test_duplicate_company_role_rows_use_one_batch_slot(self):
        campaigns = [campaign("Acme", "same", contacts=[{"profile_url": "https://linkedin.com/in/a",
                     "kind": "manager", "state": "uncontacted"}]),
                     campaign(" ACME ", "SAME", contacts=[{"profile_url": "https://linkedin.com/in/b",
                     "kind": "manager", "state": "uncontacted"}])]
        result = planner.plan({"campaigns": campaigns}, dict(POLICY, research_batch_size=6), TODAY)["actions"]
        self.assertEqual(len([a for a in result if a.get("role_key", "").casefold() == "same"]), 1)

    def test_contact_touch_with_recent_future_or_malformed_date_is_ineligible(self):
        for touched in ("2026-09-29", "2026-10-01", "not-a-date"):
            c = campaign(contacts=[{"profile_url": "https://linkedin.com/in/x", "kind": "manager",
                                    "state": "uncontacted", "last_touch_at": touched}])
            self.assertNotIn("verify_contact", [a["action"] for a in actions({"campaigns": [c]})], touched)

    def test_profile_dedup_happens_before_quota_so_batch_slots_refill(self):
        duplicate_url = "https://linkedin.com/in/shared"
        campaigns = [
            campaign("AI A", "a", role_family="ai_platform", contacts=[{"profile_url": duplicate_url,
                "kind": "manager", "state": "uncontacted"}]),
            campaign("AI B", "b", role_family="ai_platform", contacts=[{"profile_url": duplicate_url,
                "kind": "manager", "state": "uncontacted"}]),
            campaign("AI C", "c", role_family="ai_platform", contacts=[{"profile_url": "https://linkedin.com/in/unique",
                "kind": "manager", "state": "uncontacted"}]),
            campaign("Data", "d", role_family="data", contacts=[{"profile_url": "https://linkedin.com/in/data",
                "kind": "manager", "state": "uncontacted"}]),
        ]
        result = planner.plan({"campaigns": campaigns}, dict(POLICY, research_batch_size=2,
                                role_mix_ai_share=1.0), TODAY)["actions"]
        self.assertEqual(len(result), 2)
        self.assertEqual(len({a["profile_url"] for a in result}), 2)
        self.assertEqual({a["company"] for a in result}, {"AI A", "AI C"})

    def test_role_mix_allocates_ai_share_when_both_lanes_available(self):
        campaigns = []
        for i in range(4):
            campaigns.append(campaign(company=f"AI{i}", role=f"a{i}", role_family="ai_platform",
                contacts=[{"profile_url": f"https://linkedin.com/in/ai{i}", "kind": "manager", "state": "uncontacted"}]))
        for i in range(4):
            campaigns.append(campaign(company=f"Data{i}", role=f"d{i}", role_family="data",
                contacts=[{"profile_url": f"https://linkedin.com/in/d{i}", "kind": "manager", "state": "uncontacted"}]))
        result = planner.plan({"campaigns": campaigns}, dict(POLICY, research_batch_size=3), TODAY)["actions"]
        self.assertEqual(sum(a["company"].startswith("AI") for a in result), 2)
        self.assertEqual(sum(a["company"].startswith("Data") for a in result), 1)
        self.assertTrue(all(a.get("reason") and a.get("status") == "unresolved_evidence" for a in result))

    def test_credible_stretch_ai_role_is_kept_and_prioritized(self):
        stretch = campaign(company="AI Co", role="ai", role_family="applied_ai", fit="credible_stretch",
                           contacts=[{"profile_url": "https://linkedin.com/in/ai-mgr", "kind": "manager",
                                      "state": "uncontacted"}])
        direct = campaign(company="Data Co", role="data", contacts=[{"profile_url": "https://linkedin.com/in/data-mgr",
                         "kind": "manager", "state": "uncontacted"}])
        acts = actions({"campaigns": [direct, stretch]}, research_batch_size=1)
        self.assertEqual(acts[0]["company"], "AI Co")
        self.assertEqual(acts[0]["action"], "verify_contact")

    def test_missing_role_freshness_queues_refresh_not_contact_verification(self):
        c = campaign(role_verified_at=None, contacts=[{"profile_url": "https://linkedin.com/in/x",
                     "kind": "recruiter", "state": "uncontacted"}])
        acts = actions({"campaigns": [c]})
        self.assertIn("refresh_role", [a["action"] for a in acts])
        self.assertNotIn("verify_contact", [a["action"] for a in acts])

    def test_company_cap_suppresses_contact_selection_but_keeps_role_research(self):
        contacts = [{"profile_url": f"https://linkedin.com/in/p{i}", "kind": "manager",
                     "state": "connected", "last_touch_at": TODAY} for i in range(3)]
        c = campaign(contacts=contacts)
        another = campaign(role="r2", contacts=[{"profile_url": "https://linkedin.com/in/new",
                           "kind": "recruiter", "state": "uncontacted"}])
        acts = actions({"campaigns": [c, another]})
        self.assertFalse(any(a["action"] == "verify_contact" for a in acts))
        self.assertTrue(any(a["action"] == "research_recruiter_or_manager" for a in acts))

    def test_deterministic_order_and_future_review(self):
        items = [campaign("Zeta", "z"), campaign("Alpha", "a", next_review_at="2026-10-01")]
        self.assertEqual(actions({"campaigns": items}), actions({"campaigns": items}))
        self.assertFalse(any(a["company"] == "Alpha" for a in actions({"campaigns": items})))

    def test_cli_prints_compact_json_without_modifying_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path, policy_path = root / "snapshot.json", root / "policy.json"
            snapshot_path.write_text(json.dumps({"campaigns": [campaign()]}), encoding="utf-8")
            policy_path.write_text(json.dumps(POLICY), encoding="utf-8")
            before = (snapshot_path.read_bytes(), policy_path.read_bytes())
            proc = subprocess.run([sys.executable, str(Path(planner.__file__)), "--input", str(snapshot_path),
                                   "--policy", str(policy_path), "--today", TODAY], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["sending_authorized"], False)
            self.assertNotIn("\n", proc.stdout.strip())
            self.assertEqual(before, (snapshot_path.read_bytes(), policy_path.read_bytes()))


if __name__ == "__main__":
    unittest.main()
