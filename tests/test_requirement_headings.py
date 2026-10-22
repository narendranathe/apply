"""Explicit requirement boundaries; parsing never supplies candidate facts."""
import unittest

from cloud.preparation import grade_job, requirements_from_jd
from cloud.submission import plain_html


class RequirementHeadingTests(unittest.TestCase):
    def test_official_good_fit_you_have_heading_preserves_requirements(self):
        jd = plain_html("<p><strong>You might be a good fit if you have:</strong></p>"
                        "<ul><li>SQL and Python</li><li>CI/CD</li></ul>"
                        "<p><strong>Logistics</strong></p><p>Unrelated policy context.</p>")
        self.assertEqual(requirements_from_jd(jd), ["SQL and Python", "CI/CD"])

    def test_logistics_ends_an_already_recognized_requirement_section(self):
        jd = "You might be a good fit if you:\nSQL and Python\nLogistics\nUnrelated policy context."
        self.assertEqual(requirements_from_jd(jd), ["SQL and Python"])

    def test_existing_good_fit_variants_accept_exact_optional_have(self):
        for modal in ("may", "might", "could"):
            for ending in ("", " you", " you have"):
                with self.subTest(modal=modal, ending=ending):
                    jd = f"You {modal} be a good fit if{ending}:\nSQL and Python\nLOGISTICS:\nPolicy."
                    self.assertEqual(requirements_from_jd(jd), ["SQL and Python"])

    def test_heading_like_prose_does_not_create_a_requirement_section(self):
        self.assertEqual(requirements_from_jd(
            "You might be a good fit if you have SQL and Python experience.\nA friendly team."), [])

    def test_logistics_inside_a_requirement_does_not_end_the_section(self):
        clause = "SQL and Python for logistics pipelines"
        self.assertEqual(requirements_from_jd(
            "You may be a good fit if you have:\n" + clause + "\nLogistics\nPolicy."), [clause])

    def test_requirement_count_bound_is_unchanged(self):
        heading = "You might be a good fit if you have:\n"
        requirements = [f"SQL and Python requirement {i}" for i in range(41)]
        self.assertEqual(requirements_from_jd(heading + "\n".join(requirements)), [])
        self.assertEqual(requirements_from_jd(heading + "\n".join(requirements[:40])), requirements[:40])

    def test_logistics_sponsorship_prohibition_remains_in_full_jd_screening(self):
        jd = "You might be a good fit if you have:\nSQL and Python\nLogistics\nNo visa sponsorship."
        result = grade_job({"jd": jd, "title": "Data Engineer", "location": "Dallas, TX"})
        self.assertEqual(result["coverage"], 1)
        self.assertFalse(result["qualified"])
        self.assertIn("No visa sponsorship.", jd)

    def test_tenure_production_truth_and_us_location_gates_are_unchanged(self):
        cases = [
            ("SQL and Python\n7+ years data engineering", "Dallas, TX", "Data Engineer"),
            ("Production PyTorch model deployment", "Dallas, TX", "Data Engineer"),
            ("SQL and Python", "London, England", "Data Engineer"),
            ("SQL and Python", "Dallas, TX", "Staff Data Engineer"),
        ]
        for criteria, location, title in cases:
            with self.subTest(criteria=criteria, location=location, title=title):
                result = grade_job({"jd": "You might be a good fit if you have:\n" + criteria + "\nLogistics\nPolicy.",
                                    "title": title, "location": location})
                self.assertFalse(result["qualified"])

    def test_coverage_threshold_does_not_change_when_heading_becomes_supported(self):
        criteria = "SQL and Python\nUnmapped specialist requirement"
        old = grade_job({"jd": "Requirements\n" + criteria, "title": "Data Engineer", "location": "Dallas, TX"})
        new = grade_job({"jd": "You might be a good fit if you have:\n" + criteria + "\nLogistics\nPolicy.",
                         "title": "Data Engineer", "location": "Dallas, TX"})
        self.assertEqual(new, old)
        self.assertEqual(new["coverage"], 0.5)
        self.assertFalse(new["qualified"])


if __name__ == "__main__":
    unittest.main()
