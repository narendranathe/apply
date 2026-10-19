"""Deterministic daily replay regressions for previously blocked applications."""

import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from cloud.applications import fold_applications, run_applications
from cloud.shadow import ShadowStore
from cloud.submission import Response


class FrozenDateTime(datetime):
    current = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class DailyATSTransport:
    """Official-read fixture; any attempted external submission fails the test."""

    def __init__(self, unavailable=()):
        self.unavailable = set(unavailable)
        self.calls = []

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url))
        if method != "GET":
            raise AssertionError("daily blocked-role replay must never submit")
        posting = int(url.split("/jobs/", 1)[1].split("?", 1)[0])
        if posting in self.unavailable:
            return Response(503, {})
        return Response(200, {
            "id": posting,
            "requisition_id": f"req-{posting}",
            "company_name": "Acme",
            "title": "Data Engineer",
            "content": "<h3>Requirements</h3><li>SQL and production Kafka</li>",
            "location": {"name": "Dallas, TX"},
            "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{posting}",
            "questions": [],
        })


class DailyApplicationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"applications": {"max_candidates": 5}}
        self.feed = {
            "exported_at": "2026-10-17T12:00:00+00:00",
            "jobs": [{
                "company": "Acme",
                "title": "Data Engineer",
                "requisition_id": f"req-{posting}",
                "url": f"https://job-boards.greenhouse.io/acme/jobs/{posting}",
                "location": "Dallas, TX",
                "relevance_score": 0.6,
                "posted_at": "2026-10-17T11:00:00+00:00",
                "is_active": True,
            } for posting in (100, 101)],
        }
        self.store = ShadowStore()

    def run_day(self, day, transport=None):
        FrozenDateTime.current = datetime(2026, 10, day, 12, tzinfo=timezone.utc)
        feed = copy.deepcopy(self.feed)
        feed["exported_at"] = FrozenDateTime.current.isoformat()
        with patch("cloud.applications.dt.datetime", FrozenDateTime):
            return run_applications(
                self.store, self.config, False, feed=feed,
                transport=transport or DailyATSTransport(),
                prepare=lambda *_: self.fail("qualification-blocked role must not prepare"),
            )

    def seed_first_day_blocked_applications(self):
        first = self.run_day(17)
        self.assertEqual(first["exceptions"], {"qualification_gaps": 2})
        self.assertEqual(first["statuses"], {"submission_blocked": 2})
        self.assertEqual(first["submission_attempts"], 0)
        self.assertEqual(len(fold_applications(self.store.query("applications"))), 2)

    def test_same_qualification_blocks_can_be_rechecked_next_day(self):
        self.seed_first_day_blocked_applications()
        original = copy.deepcopy(self.store.records)

        second = self.run_day(18)

        self.assertEqual(second["exceptions"], {"qualification_gaps": 2})
        self.assertEqual(second["statuses"], {"submission_blocked": 2})
        self.assertEqual(second["submission_attempts"], 0)
        self.assertEqual(self.store.records, original)

    def test_existing_blocked_role_with_unavailable_official_post_remains_an_exception(self):
        self.seed_first_day_blocked_applications()
        original_ids = set(fold_applications(self.store.query("applications")))
        transport = DailyATSTransport(unavailable={100})

        second = self.run_day(18, transport)

        self.assertEqual(second["exceptions"], {"official_post_unverified": 1, "qualification_gaps": 1})
        self.assertEqual(second["statuses"], {"submission_blocked": 2})
        self.assertEqual(second["submission_attempts"], 0)
        self.assertEqual(set(fold_applications(self.store.query("applications"))), original_ids)
        self.assertEqual(len(transport.calls), 2)

    def test_existing_url_alias_without_feed_requisition_never_creates_a_second_identity(self):
        self.seed_first_day_blocked_applications()
        original_ids = set(fold_applications(self.store.query("applications")))
        for lead in self.feed["jobs"]:
            lead.pop("requisition_id")

        second = self.run_day(18, DailyATSTransport(unavailable={100}))

        self.assertEqual(second["exceptions"], {"official_post_unverified": 1, "qualification_gaps": 1})
        self.assertEqual(set(fold_applications(self.store.query("applications"))), original_ids)
        self.assertEqual(second["statuses"], {"submission_blocked": 2})
        self.assertEqual(second["submission_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
