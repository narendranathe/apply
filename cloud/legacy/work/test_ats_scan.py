import contextlib
import copy
import io
import datetime as dt
import json
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs"))
import ats_scan as scanner


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ats"
NOW = dt.datetime(2026, 10, 1, 21, 0, tzinfo=dt.timezone.utc)
URLS = {
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true": "greenhouse_acme.json",
    "https://api.lever.co/v0/postings/beta?mode=json": "lever_beta.json",
    "https://api.ashbyhq.com/posting-api/job-board/gamma?includeCompensation=true": "ashby_gamma.json",
    "https://api.smartrecruiters.com/v1/companies/Delta/postings?limit=100&offset=0": "smartrecruiters_delta_page0.json",
    "https://api.smartrecruiters.com/v1/companies/Delta/postings/1001": "smartrecruiters_delta_1001.json",
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeFetch:
    """Maps known URLs to fixtures; anything else fails like a dead source."""

    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def __call__(self, url):
        self.calls.append(url)
        if url in self.overrides:
            return copy.deepcopy(self.overrides[url])
        if url not in URLS:
            raise scanner.FetchError("HTTP 404")
        return load(URLS[url])


def registry():
    return scanner.load_registry(FIXTURES / "registry.json")


def by_title(report):
    return {(r["employer"], r["title"], r["job_id"]): r for r in report["roles"]}


class NoNetworkMixin:
    def setUp(self):
        patcher = mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used in test"))
        patcher.start()
        self.addCleanup(patcher.stop)


class ClassificationTests(unittest.TestCase):
    def test_title_families(self):
        cases = {
            "Data Engineer": "data_engineering",
            "Software Engineer, Data Platform": "data_engineering",
            "Senior Data Infrastructure Engineer": "data_engineering",
            "Analytics Engineer": "analytics_engineering",
            "Software Engineer, Research Data Platform": "ai_platform",
            "ML Platform Engineer": "ai_platform",
            "MLOps Engineer": "ai_platform",
            "Applied AI Engineer": "applied_ai",
            "Software Engineer, AI Agents": "applied_ai",
            "Solutions Architect, Data & AI": "solutions_data",
            "Account Executive, AI Native": None,
            "Data Scientist": None,
            "Data Center Technician": None,
            "Engineering Manager, Data Platform": None,
            "Data Engineering Intern": None,
            "AI Infrastructure Operations, Demand Planning": None,
            "Technical Recruiter, Data": None,
            "Developer Advocate, AI": None,
            "Backend Engineer, Database Internals": None,
        }
        for title, expected in cases.items():
            self.assertEqual(scanner.classify_title(title), expected, title)

    def test_solutions_role_needs_data_focus_in_text(self):
        data_text = "Design ETL and data pipeline work with SQL and Python for the data warehouse."
        self.assertEqual(scanner.classify_title("Solutions Engineer", data_text), "solutions_data")
        self.assertIsNone(scanner.classify_title("Solutions Engineer", "Run product demos for marketing buyers."))

    def test_location_buckets(self):
        cases = [
            (["Dallas, TX"], [], None, ("us", True)),
            (["Remote - US"], [], None, ("us_remote", False)),
            (["Remote (US or Canada)"], [], None, ("us_remote", False)),
            (["Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA"], [], None, ("us_remote", False)),
            (["London, UK"], [], None, ("non_us", False)),
            (["Toronto, ON, CA"], [], None, ("non_us", False)),
            (["Bengaluru, India"], [], None, ("non_us", False)),
            (["Remote"], [], None, ("remote_unspecified", False)),
            (["Remote"], ["US"], True, ("us_remote", False)),
            (["Dublin, CA"], [], None, ("us", False)),
            (["Albuquerque, New Mexico"], [], None, ("us", False)),
            (["Indianapolis, Indiana"], [], None, ("us", False)),
            (["Plano, TX, United States"], ["us"], None, ("us", True)),
        ]
        for locations, countries, remote, expected in cases:
            bucket, texas, _ = scanner.classify_location(locations, countries, remote)
            self.assertEqual((bucket, texas), expected, locations)

    def test_sponsorship_explicit_exclusions(self):
        negatives = [
            "Unfortunately, we are unable to sponsor visas for this role.",
            "Applicants must be authorized to work in the U.S. without the need for visa sponsorship.",
            "This position is not eligible for visa sponsorship.",
            "We do not offer sponsorship for this position.",
            "No sponsorship is available.",
            "The company will not sponsor applicants for work visas.",
            "Candidates must be a U.S. citizen.",
            "This role requires an active Secret clearance.",
        ]
        for text in negatives:
            status, flags = scanner.sponsorship_assessment(text)
            self.assertEqual(status["status"], "explicit_exclusion", text)
            self.assertTrue(flags, text)
            self.assertTrue(status["evidence"])

    def test_general_sponsorship_statement_is_not_an_exclusion_or_clearance(self):
        text = ("We do sponsor visas! However, we aren't able to successfully sponsor visas for every role "
                "and every candidate.")
        status, flags = scanner.sponsorship_assessment(text)
        self.assertEqual(status["status"], "general_positive_statement")
        self.assertEqual(flags, ["sponsorship_positive_statement"])
        status, flags = scanner.sponsorship_assessment("Will you now or in the future require sponsorship?")
        self.assertEqual((status["status"], flags), ("unstated", []))

    def test_seniority_guard(self):
        info, flags = scanner.seniority_assessment("Staff Data Engineer", "8+ years of experience with Spark.")
        self.assertIn("staff_or_principal_title", flags)
        self.assertIn("experience_8plus_years", flags)
        info, flags = scanner.seniority_assessment("Data Engineer", "3+ years of professional experience; 8+ years preferred experience")
        self.assertEqual(info["years_min"], 3)
        self.assertNotIn("experience_8plus_years", flags)
        self.assertIn("mentions_8plus_years", flags)
        info, flags = scanner.seniority_assessment("Senior Data Engineer", "5-7 years of experience")
        self.assertIn("senior_title", flags)
        self.assertIn("experience_5_to_7_years", flags)

    def test_comp_from_fields_and_text(self):
        comp, multiple = scanner.extract_comp({"comp": None, "text": "Pay: $140,000—$180,000 USD. Other: $150K - $200K"})
        self.assertEqual((comp["min"], comp["max"], comp["source"]), (140000, 180000, "description_text"))
        self.assertTrue(multiple)
        comp, _ = scanner.extract_comp({"comp": {"min": 1, "max": 2, "summary": "$1 – $2", "source": "ats_field"}})
        self.assertNotIn("–", comp["summary"])
        self.assertIsNone(scanner.extract_comp({"comp": None, "text": "$35 - $38 per hour"})[0])

    def test_public_emails_only_recruiting_inboxes(self):
        text = "Email recruiting@gamma.example, accommodations@gamma.example, jane.doe@gmail.com or jobs@gamma.example"
        self.assertEqual(scanner.public_recruiting_emails(text), ["recruiting@gamma.example", "jobs@gamma.example"])

    def test_html_to_text_handles_escaped_greenhouse_content(self):
        text = scanner.html_to_text("&lt;p&gt;SQL &amp;amp; Python&lt;/p&gt;&lt;li&gt;CDC&lt;/li&gt;", escaped=True)
        self.assertEqual(text, "SQL & Python\nCDC")

    def test_tls_context_keeps_verification(self):
        context = scanner._ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class RuleRegressionTests(unittest.TestCase):
    def test_export_license_boilerplate_is_not_a_visa_exclusion(self):
        text = ("Please note that any offer of employment may be conditioned on your authorization to receive "
                "software or technology controlled under these US export laws without sponsorship for an export license.")
        status, flags = scanner.sponsorship_assessment(text)
        self.assertEqual(status["status"], "unstated")
        self.assertEqual(flags, ["export_control_notice"])
        truncated = "controlled under these US export laws without sponsorship for..."
        self.assertEqual(scanner.sponsorship_assessment(truncated)[0]["status"], "unstated")

    def test_employer_own_product_is_not_a_gap(self):
        _, fit = scanner.fit_assessment("applied_ai", "AI Engineer", "Snowflake and Airflow", "us", False,
                                        "posted", 1, [], employer="Snowflake")
        self.assertEqual(fit["gaps"], ["Airflow"])

    def test_rerender_marks_cleared_evidence_for_recheck_and_ranks_posted_first(self):
        base = {"employer": "Cloud Co", "fit": {"production_evidence": [], "independent_evidence": [], "gaps": []},
                "texas": False, "location": "Austin, TX", "posted_at": None, "url": "u", "age_days": 1.0}
        old_false_positive = dict(base, role_key="cloud-co:1", title="Data Platform Engineer", freshness="updated",
                                  fit_score=0, flags=["no_sponsorship_language"],
                                  exclude_reason="explicit role-level exclusion: no_sponsorship_language",
                                  sponsorship={"status": "explicit_exclusion", "evidence":
                                               "Offers may be conditioned on export laws without sponsorship for an export license."})
        real_block = dict(base, role_key="cloud-co:2", title="Analytics Engineer", freshness="posted", fit_score=5,
                          flags=["no_sponsorship_language"], exclude_reason="explicit role-level exclusion: x",
                          sponsorship={"status": "explicit_exclusion", "evidence": "We cannot sponsor visas for this role."})
        newer = dict(base, role_key="cloud-co:3", title="Data Engineer", freshness="posted", fit_score=10, flags=[],
                     exclude_reason=None, sponsorship={"status": "unstated", "evidence": None})
        report = scanner.rerender({"roles": [old_false_positive, real_block, newer], "counts": {}})
        order = [r["role_key"] for r in report["roles"]]
        self.assertEqual(order, ["cloud-co:3", "cloud-co:1", "cloud-co:2"])
        cleared = report["roles"][1]
        self.assertIsNone(cleared["exclude_reason"])
        self.assertEqual(cleared["sponsorship"]["status"], "needs_recheck")
        self.assertIn("sponsorship_needs_recheck", cleared["flags"])
        self.assertEqual(report["counts"]["roles_excluded_explicit"], 1)


class ScanTests(NoNetworkMixin, unittest.TestCase):
    def test_end_to_end_filters_flags_and_isolates_failures(self):
        fetch = FakeFetch()
        report = scanner.scan(registry(), fetch, NOW, days=3)
        roles = by_title(report)
        self.assertFalse(report["sending_authorized"])
        keys = {(employer, title) for employer, title, _ in roles}
        self.assertEqual(keys, {
            ("Acme Data", "Data Engineer"), ("Acme Data", "Senior Staff Data Platform Engineer"),
            ("Acme Data", "Analytics Engineer"), ("Acme Data", "Software Engineer, Data Platform"),
            ("Beta AI", "AI Platform Engineer"), ("Beta AI", "Solutions Engineer"),
            ("Gamma", "Applied AI Engineer"), ("Delta", "Data Engineer")})
        dallas = roles[("Acme Data", "Data Engineer", "101")]
        self.assertEqual(dallas["role_key"], "acme-data:101")
        self.assertTrue(dallas["texas"])
        self.assertEqual(dallas["freshness"], "posted")
        self.assertEqual((dallas["comp"]["min"], dallas["comp"]["max"]), (140000, 180000))
        self.assertEqual(dallas["sponsorship"]["status"], "general_positive_statement")
        self.assertIn("SQL Server/T-SQL", dallas["fit"]["production_evidence"])
        self.assertIn("CDC", dallas["fit"]["production_evidence"])
        self.assertEqual(dallas["planner_role_family"], "data")
        staff = roles[("Acme Data", "Senior Staff Data Platform Engineer", "102")]
        self.assertIn("staff_or_principal_title", staff["flags"])
        self.assertIn("experience_8plus_years", staff["flags"])
        self.assertLess(staff["fit_score"], dallas["fit_score"])
        blocked = roles[("Acme Data", "Analytics Engineer", "103")]
        self.assertTrue(blocked["exclude_reason"])
        self.assertEqual(report["roles"][-1]["role_key"], blocked["role_key"], "excluded roles rank last")
        updated = roles[("Acme Data", "Software Engineer, Data Platform", "106")]
        self.assertEqual(updated["freshness"], "updated")
        self.assertIn("updated_not_new", updated["flags"])
        lever_ai = roles[("Beta AI", "AI Platform Engineer", "lev-1")]
        self.assertEqual(lever_ai["family"], "ai_platform")
        self.assertIn("no_sponsorship_language", lever_ai["flags"])
        self.assertEqual(lever_ai["comp"]["source"], "ats_field")
        self.assertIn("MCP integration (independent project)", lever_ai["fit"]["independent_evidence"])
        solutions = roles[("Beta AI", "Solutions Engineer", "lev-2")]
        self.assertEqual((solutions["family"], solutions["location_bucket"]), ("solutions_data", "us_remote"))
        gamma = roles[("Gamma", "Applied AI Engineer", "ash-1")]
        self.assertEqual(gamma["public_recruiting_emails"], ["recruiting@gamma.example"])
        self.assertEqual(gamma["comp"]["summary"], "$160K - $210K")
        delta = roles[("Delta", "Data Engineer", "1001")]
        self.assertEqual(delta["url"], "https://jobs.smartrecruiters.com/Delta/1001-data-engineer")
        self.assertEqual(delta["hiring_team_hints"][0]["name"], "Pat Example")
        self.assertIn("SSIS", delta["fit"]["production_evidence"])
        self.assertNotIn("description_not_fetched", delta["flags"])
        statuses = {s["employer"]: s for s in report["sources"]}
        self.assertEqual(set(statuses), {"Acme Data", "Beta AI", "Gamma", "Delta", "Broken Co"})
        self.assertEqual(statuses["Broken Co"]["status"], "error")
        self.assertIn("404", statuses["Broken Co"]["error"])
        self.assertEqual(report["counts"]["boards_failed"], 1)
        self.assertEqual(statuses["Delta"]["requests"], 2, "one list page plus one detail fetch")
        self.assertNotIn("https://api.smartrecruiters.com/v1/companies/Delta/postings/1002", fetch.calls,
                         "non-US SmartRecruiters postings never trigger detail fetches")

    def test_posted_basis_drops_update_only_roles(self):
        report = scanner.scan(registry(), FakeFetch(), NOW, days=3, basis="posted")
        titles = {r["title"] for r in report["roles"] if r["employer"] == "Acme Data"}
        self.assertNotIn("Software Engineer, Data Platform", titles)

    def test_wider_window_surfaces_older_roles(self):
        report = scanner.scan(registry(), FakeFetch(), NOW, days=120, basis="posted")
        lead = next(r for r in report["roles"] if r["role_key"] == "acme-data:101")
        self.assertEqual([d["role_key"] for d in lead["duplicate_postings"]], ["acme-data:107"],
                         "same-title postings collapse into the best-ranked row without losing the URL")
        self.assertTrue(lead["texas"])

    def test_cache_only_surfaces_new_or_changed_roles(self):
        cache = {"version": 1, "roles": {}}
        first = scanner.scan(registry(), FakeFetch(), NOW, cache=cache)
        self.assertTrue(first["roles"])
        self.assertTrue(all(r["change"] == "new" for r in first["roles"]))
        second = scanner.scan(registry(), FakeFetch(), NOW + dt.timedelta(hours=2), cache=cache)
        self.assertEqual(second["roles"], [])
        changed = load("greenhouse_acme.json")
        changed["jobs"][0]["content"] = changed["jobs"][0]["content"].replace("CDC", "CDC and SSIS")
        changed["jobs"][0]["updated_at"] = "2026-10-01T10:00:00Z"
        url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        third = scanner.scan(registry(), FakeFetch({url: changed}), NOW + dt.timedelta(hours=3), cache=cache)
        self.assertEqual([(r["role_key"], r["change"]) for r in third["roles"]], [("acme-data:101", "changed")])
        self.assertEqual(third["roles"][0]["first_seen"], first["generated_at"])
        replay = scanner.scan(registry(), FakeFetch(), NOW + dt.timedelta(hours=4), cache=cache, include_seen=True)
        self.assertTrue(all(r["change"] in {"seen", "changed"} for r in replay["roles"]))

    def test_dates_alone_do_not_resurface_roles(self):
        cache = {"version": 1, "roles": {}}
        scanner.scan(registry(), FakeFetch(), NOW, cache=cache)
        bumped = load("greenhouse_acme.json")
        for job in bumped["jobs"]:
            job["updated_at"] = "2026-10-01T20:00:00Z"
        url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        again = scanner.scan(registry(), FakeFetch({url: bumped}), NOW, cache=cache)
        self.assertNotIn("acme-data:101", {r["role_key"] for r in again["roles"]})

    def test_detail_budget_bounds_smartrecruiters_requests(self):
        rows = [dict(r, max_detail_fetches=0) if r["ats"] == "smartrecruiters" else r for r in registry()]
        fetch = FakeFetch()
        report = scanner.scan(rows, fetch, NOW)
        delta = by_title(report)[("Delta", "Data Engineer", "1001")]
        self.assertIn("description_not_fetched", delta["flags"])
        self.assertFalse(any("/postings/1001" in url for url in fetch.calls))

    def test_malformed_board_payload_is_an_error_not_zero_roles(self):
        url = "https://api.ashbyhq.com/posting-api/job-board/gamma?includeCompensation=true"
        report = scanner.scan(registry(), FakeFetch({url: {"unexpected": True}}), NOW)
        status = {s["employer"]: s for s in report["sources"]}["Gamma"]
        self.assertEqual(status["status"], "error")

    def test_registry_loader_skips_disabled_and_unsupported(self):
        names = [r["employer"] for r in registry()]
        self.assertNotIn("Disabled Co", names)
        self.assertNotIn("Unsupported Co", names)

    def test_summary_has_top_roles_and_no_em_dash(self):
        report = scanner.scan(registry(), FakeFetch(), NOW)
        text = scanner.render_summary(report, top=3)
        self.assertNotIn("—", text)
        self.assertIn("## Top 3 roles (3 newly posted)", text)
        self.assertIn("Broken Co", text)
        self.assertIn("Excluded by explicit role language", text)
        top_title = next(r for r in report["roles"] if not r["exclude_reason"])["title"]
        self.assertIn(top_title, text)

    def test_cli_writes_report_summary_and_cache_with_injected_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out, summary, cache = root / "scan.json", root / "summary.md", root / "state" / "cache.json"
            argv = ["--registry", str(FIXTURES / "registry.json"), "--output", str(out), "--summary", str(summary),
                    "--cache", str(cache), "--days", "3"]
            self.assertEqual(scanner.main(argv, fetch=FakeFetch(), now=NOW), 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(report["sending_authorized"])
            self.assertTrue(summary.read_text(encoding="utf-8").startswith("# ATS scan summary"))
            self.assertTrue(json.loads(cache.read_text(encoding="utf-8"))["roles"])
            self.assertEqual(scanner.main(argv + ["--dry-run"], fetch=FakeFetch(), now=NOW), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["roles"], [])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(scanner.main(["--registry", str(root / "missing.json")], fetch=FakeFetch(), now=NOW), 2)
            self.assertIn("error", err.getvalue())


class RegistryFileTests(unittest.TestCase):
    def test_seed_registry_is_well_formed(self):
        path = Path(scanner.__file__).resolve().parent / "ats_registry.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        enabled = scanner.load_registry(path)
        self.assertGreaterEqual(len(enabled), 25)
        pairs = [(r["ats"], r["slug"]) for r in data["employers"]]
        self.assertEqual(len(pairs), len(set(pairs)))
        for row in data["employers"]:
            self.assertIn(row["ats"], scanner.ENDPOINTS)
            self.assertTrue(row.get("verified_at"))
            if row.get("enabled") is False:
                self.assertTrue(row.get("disabled_reason"))
        names = {r["employer"] for r in enabled}
        for required in ("Anthropic", "Databricks", "Ramp", "MongoDB", "Datadog", "Stripe", "Confluent",
                         "Elastic", "Snowflake"):
            self.assertIn(required, names)


if __name__ == "__main__":
    unittest.main()
