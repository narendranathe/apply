"""Immutable changed-blocker evidence; repeated blockers remain idempotent."""
from copy import deepcopy
import unittest

from cloud.applications import fold_applications
from cloud.outreach import record
from tests import test_applications_daily as daily


class AmbiguousCompliance(daily.DailyATSTransport):
    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        response.data['compliance'] = [{'type': 'eeoc', 'description': 'Synthetic official context',
            'questions': [{'label': 'Voluntary', 'fields': [{'name': 'choice', 'type': 'input_text'}]}]}]
        return response


class ChangedQualification(daily.DailyATSTransport):
    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        response.data['content'] = '<h3>Requirements</h3><li>Python and production Kafka</li>'
        return response


class ApplicationBlockerRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = daily.DailyApplicationTests()
        self.fixture.setUp()
        self.fixture.feed["jobs"] = self.fixture.feed["jobs"][:1]

    @property
    def store(self):
        return self.fixture.store

    def latest(self):
        return fold_applications(self.store.query('applications'))

    def assert_preserved(self, before):
        self.assertTrue(set(before) <= set(self.store.records))
        self.assertTrue(all(self.store.records[identity] == row for identity, row in before.items()))

    def assert_one_revision_and_audit_per_role(self, before, previous):
        current = self.latest()
        self.assertEqual(set(current), set(previous))
        self.assertEqual(len(self.store.records) - len(before), 2 * len(previous))
        for identity, old in previous.items():
            row = current[identity]
            self.assertEqual(row['status'], 'submission_blocked')
            self.assertEqual(row['payload']['revision'], old['payload']['revision'] + 1)
            self.assertEqual(row['payload']['previous_record_id'], old['id'])
            for key in ('company', 'requisition_id', 'job_id', 'dedup_key', 'url',
                        'url_aliases', 'employer_history_unresolved', 'job'):
                self.assertEqual(row['payload'].get(key), old['payload'].get(key))
            events = [event for event in self.store.query('audit_events')
                      if event['payload'].get('transition_record_id') == row['id']]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['payload']['payload_hash'], row['hash'])
        self.assert_preserved(before)

    def test_parser_recovery_appends_current_qualification_evidence_once(self):
        first = self.fixture.run_day(17, AmbiguousCompliance())
        self.assertEqual(first['exceptions'], {'official_application_requirement_ambiguous': 1})
        before, previous = deepcopy(self.store.records), self.latest()
        transport = daily.DailyATSTransport()
        second = self.fixture.run_day(18, transport)
        self.assertEqual(second['exceptions'], {'qualification_gaps': 1})
        self.assertEqual(second['submission_attempts'], 0)
        self.assert_one_revision_and_audit_per_role(before, previous)
        for row in self.latest().values():
            self.assertEqual(row['payload']['reason'], 'qualification_gaps')
            self.assertFalse(row['payload']['qualification']['qualified'])
            self.assertEqual(row['payload']['qualification']['requirements'][0]['requirement'],
                             'SQL and production Kafka')
        self.assertTrue(all(method == 'GET' for method, _ in transport.calls))
        after = deepcopy(self.store.records)
        third = self.fixture.run_day(19)
        self.assertEqual(third['exceptions'], {'qualification_gaps': 1})
        self.assertEqual(self.store.records, after)  # New GET timestamps are not new evidence.

    def test_same_reason_with_changed_qualification_evidence_appends_once(self):
        self.fixture.run_day(17)
        before, previous = deepcopy(self.store.records), self.latest()
        second = self.fixture.run_day(18, ChangedQualification())
        self.assertEqual(second['exceptions'], {'qualification_gaps': 1})
        self.assert_one_revision_and_audit_per_role(before, previous)
        for row in self.latest().values():
            self.assertEqual(row['payload']['qualification']['requirements'][0]['requirement'],
                             'Python and production Kafka')
        after = deepcopy(self.store.records)
        self.fixture.run_day(19, ChangedQualification())
        self.assertEqual(self.store.records, after)

    def test_repeated_identical_parser_failure_never_appends_another_blocker(self):
        self.fixture.run_day(17, AmbiguousCompliance())
        before = deepcopy(self.store.records)
        result = self.fixture.run_day(18, AmbiguousCompliance())
        self.assertEqual(result['exceptions'], {'official_application_requirement_ambiguous': 1})
        self.assertEqual(self.store.records, before)

    def test_changed_official_verification_failure_appends_truthful_blocker_once(self):
        self.fixture.run_day(17, AmbiguousCompliance())
        before, previous = deepcopy(self.store.records), self.latest()
        result = self.fixture.run_day(18, daily.DailyATSTransport(unavailable=(100, 101)))
        self.assertEqual(result['exceptions'], {'official_post_unverified': 1})
        self.assert_one_revision_and_audit_per_role(before, previous)
        self.assertTrue(all(row['payload']['reason'] == 'official_post_unverified'
                            for row in self.latest().values()))
        after = deepcopy(self.store.records)
        self.fixture.run_day(19, daily.DailyATSTransport(unavailable=(100, 101)))
        self.assertEqual(self.store.records, after)

    def test_applied_and_unknown_submission_suppressions_are_never_refreshed(self):
        for status in ('applied_verified', 'submission_unknown'):
            with self.subTest(status=status):
                self.setUp()
                self.fixture.run_day(17)
                old = next(iter(self.latest().values()))
                payload = deepcopy(old['payload'])
                payload['revision'] += 1
                payload['previous_record_id'] = old['id']
                self.store.put(record('synthetic-suppression', 'applications', payload, status))
                before = deepcopy(self.store.records)
                transport = daily.DailyATSTransport()
                result = self.fixture.run_day(18, transport)
                self.assertEqual(result['duplicates'], 1)
                self.assertEqual(result['submission_attempts'], 0)
                self.assertEqual(transport.calls, [])
                self.assertEqual(self.store.records, before)


if __name__ == '__main__':
    unittest.main()
