import copy
from datetime import datetime, timedelta, timezone
import unittest

from cloud.notion import AmbiguousWriteError
from cloud.records import content_hash, make_record
from cloud.reporting import HYPOTHESES, ReportingConflict, run_reporting, effective_research_share


NOW = datetime(2026, 10, 12, 15, tzinfo=timezone.utc)


class Store:
    def __init__(self, rows=()):
        self.rows = {x['id']: copy.deepcopy(x) for x in rows}
        self.writes = []
        self.fail_kind = None
        self.hide_kind = None

    def query(self, kind=None):
        return [copy.deepcopy(x) for x in self.rows.values() if kind is None or x['kind'] == kind]

    def get(self, identity):
        row = self.rows.get(identity)
        return None if row and row['kind'] == self.hide_kind else copy.deepcopy(row)

    def put(self, record):
        if record['kind'] == self.fail_kind:
            raise AmbiguousWriteError('uncertain fixture write')
        previous = self.rows.get(record['id'])
        if previous and previous != record:
            raise ReportingConflict('fixture immutable conflict')
        self.writes.append(record['id'])
        self.rows[record['id']] = copy.deepcopy(record)
        return record


def confirmation(event, outcome, when=None):
    return {'event_id': event, 'outcome': outcome, 'verified': True,
            'evidence_url': 'https://example.test/evidence/' + event,
            'confirmed_at': (when or NOW).isoformat()}


def receipt(identity, when=None):
    return {'status': 'applied_verified', 'receipt_id': identity,
            'evidence_url': 'https://hire.lever.co/search?applicationId=' + identity,
            'confirmed_at': (when or NOW).isoformat(), 'http_status': 200}


def application(identity, revision, status, **data):
    return make_record('applications', {'application_id': identity, 'revision': revision, **data},
                       identity + ':' + str(revision), status)


def cohort(family, total, positives, age=20):
    rows = []
    when = NOW - timedelta(days=age)
    for i in range(total):
        identity = family + ':' + str(i)
        contact, job, event = 'contact:' + identity, 'job:' + identity, 'activity:' + identity
        activity = make_record('outreach_events', {'contact_id': contact, 'job_id': job,
                           'confirmation_evidence': confirmation(event, 'message_sent_confirmed', when)}, event)
        assignment = make_record('experiments', {'record_type': 'assignment', 'assignment_id': identity,
                     'hypothesis_id': HYPOTHESES[0][0], 'arm': 'warm_introduction', 'role_family': family,
                     'contact_id': contact, 'job_id': job, 'assigned_at': when.isoformat(),
                     'source_event_id': event, 'source_record_hash': activity['hash']}, 'assignment:' + identity)
        rows += [activity, assignment]
        if i < positives:
            rows.append(make_record('interviews', {'contact_id': contact, 'job_id': job, 'assignment_id': identity,
                        'confirmation_evidence': confirmation('outcome:' + identity, 'interview_confirmed', when + timedelta(days=2))}, 'interview:' + identity))
    return rows


class ReportingTests(unittest.TestCase):
    def test_status_prose_and_generic_http_are_not_outcomes(self):
        rows = [application('a', 0, 'applied_verified', receipt={'http_status': 200}),
                application('b', 0, 'interview', confirmation_evidence='interview in old log'),
                make_record('referrals', {'contact_id': 'c', 'confirmation_evidence': 'offered referral'}, 'r', 'submitted'),
                make_record('interviews', {'contact_id': 'c', 'confirmation_evidence': {'verified': True}}, 'i', 'confirmed')]
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['counts']['verified_submitted_applications'], 0)
        self.assertEqual(result['counts']['confirmed_interviews'], 0)
        self.assertEqual(result['counts']['confirmed_referrals_submitted'], 0)

    def test_valid_receipt_survives_later_rejection_and_is_not_double_counted(self):
        proof = receipt('exact-receipt')
        rows = [application('a', 0, 'submitting', attempt={'started_at': (NOW-timedelta(days=1)).isoformat()}),
                application('a', 1, 'applied_verified', receipt=proof), application('a', 2, 'rejected', receipt=proof)]
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['counts']['applications_attempted'], 1)
        self.assertEqual(result['counts']['verified_submission_receipts'], 1)
        self.assertEqual(result['counts']['verified_submitted_applications'], 1)
        self.assertEqual(result['counts']['application_status_observations'], {'rejected': 1})

    def test_duplicate_outcome_sources_count_exact_event_once(self):
        payload = {'contact_id': 'person', 'confirmation_evidence': confirmation('confirmed-one', 'interview_confirmed')}
        rows = [make_record('interviews', payload, 'i'), make_record('outreach_events', payload, 'event')]
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['counts']['confirmed_interviews'], 1)

    def test_distinct_source_identity_counts_observations_once(self):
        rows = [make_record('jobs', {'job_id': 'job:one'}, 'j1'), make_record('jobs', {'job_id': 'job:one'}, 'j2'),
                make_record('jobs', {'job_id': 'job:two'}, 'j3'),
                make_record('contacts', {'source_notion_id': '123456781234123412341234567890ab'}, 'c1'),
                make_record('contacts', {'source_notion_id': '12345678-1234-1234-1234-1234567890ab'}, 'c2')]
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['counts']['source_jobs'], 2)
        self.assertEqual(result['counts']['source_job_observations'], 3)
        self.assertEqual(result['counts']['source_contacts'], 1)

    def test_future_and_date_only_evidence_never_counts(self):
        rows = []
        for i, when in enumerate([NOW+timedelta(days=1), '2026-10-01']):
            evidence = confirmation(str(i), 'interview_confirmed')
            evidence['confirmed_at'] = when.isoformat() if isinstance(when, datetime) else when
            rows.append(make_record('interviews', {'contact_id': 'c', 'confirmation_evidence': evidence}, 'i'+str(i)))
        self.assertEqual(run_reporting(Store(rows), {}, NOW)['counts']['confirmed_interviews'], 0)

    def test_missing_verified_flag_source_or_identity_is_ignored(self):
        rows = []
        for i, field in enumerate(['verified', 'event_id', 'evidence_url', 'confirmed_at']):
            proof = confirmation(str(i), 'referral_submitted_confirmed'); del proof[field]
            rows.append(make_record('referrals', {'contact_id': 'c', 'confirmation_evidence': proof}, 'r'+str(i)))
        self.assertEqual(run_reporting(Store(rows), {}, NOW)['counts']['confirmed_referrals_submitted'], 0)

    def test_explicit_referral_stages_remain_separate(self):
        rows = [make_record('referrals', {'contact_id': 'c', 'confirmation_evidence': confirmation('offer', 'referral_offered_confirmed')}, 'offer'),
                make_record('referrals', {'contact_id': 'c', 'confirmation_evidence': confirmation('submit', 'referral_submitted_confirmed')}, 'submit')]
        counts = run_reporting(Store(rows), {}, NOW)['counts']
        self.assertEqual(counts['confirmed_referrals_offered'], 1)
        self.assertEqual(counts['confirmed_referrals_submitted'], 1)

    def test_hypotheses_and_drafts_never_enter_denominator(self):
        rows = [make_record('experiments', {'record_type': 'hypothesis'}, 'old-hypothesis'),
                make_record('conversation_observations', {'contact_id': 'c', 'draft': 'fixture only'}, 'draft')]
        store = Store(rows)
        result = run_reporting(store, {}, NOW, dry_run=False)
        self.assertEqual(result['counts']['actual_verified_assignments'], 0)
        self.assertEqual(result['allocation']['proposed_ai_share'], 0.6)
        self.assertEqual(len([x for x in store.query('experiments') if x['payload'].get('record_type') == 'hypothesis']), 5)

    def test_samples_required_separately_for_both_families(self):
        rows = cohort('ai_platform', 25, 20) + cohort('data_platform', 2, 1)
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['allocation']['proposed_ai_share'], 0.6)
        self.assertEqual(result['allocation']['reason'], 'insufficient_verified_qualified_outcomes')

    def test_verified_qualified_threshold_cannot_be_met_by_activity(self):
        result = run_reporting(Store(cohort('ai_platform', 30, 0)+cohort('data_platform', 30, 0)), {}, NOW)
        self.assertEqual(result['allocation']['proposed_ai_share'], 0.6)
        self.assertEqual(result['counts']['actual_verified_assignments'], 60)

    def test_immature_assignment_and_unlinked_outcome_do_not_qualify(self):
        rows = cohort('ai_platform', 1, 1, age=5)
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['allocation']['cohorts']['ai_platform']['matured_assignments'], 0)
        rows = cohort('ai_platform', 1, 1)
        rows[-1] = make_record('interviews', {**rows[-1]['payload'], 'assignment_id': 'unrelated'}, rows[-1]['id'])
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['allocation']['cohorts']['ai_platform']['qualified_assignments'], 0)

    def test_sufficient_distinct_evidence_proposes_but_does_not_mutate_config(self):
        config = {'outreach': {'research_ai_share': 0.6}}
        before = copy.deepcopy(config)
        result = run_reporting(Store(cohort('ai_platform', 20, 15)+cohort('data_platform', 20, 5)), config, NOW)
        self.assertEqual(result['allocation']['proposed_ai_share'], 0.7)
        self.assertFalse(result['configuration_changed'])
        self.assertEqual(config, before)

    def test_threshold_boundary_uses_exact_counts(self):
        config = {'reporting': {'min_matured_assignments_per_family': 10, 'min_qualified_assignments_per_family': 2}}
        result = run_reporting(Store(cohort('ai_platform', 10, 3)+cohort('data_platform', 10, 2)), config, NOW)
        self.assertEqual(result['allocation']['proposed_ai_share'], 0.7)

    def test_invalid_assignment_hash_is_not_experiment_evidence(self):
        rows = cohort('ai_platform', 1, 1)
        rows[1] = make_record('experiments', {**rows[1]['payload'], 'source_record_hash': 'incorrect'}, rows[1]['id'])
        counts = run_reporting(Store(rows), {}, NOW)['counts']
        self.assertEqual(counts['actual_verified_assignments'], 0)
        self.assertEqual(counts['invalid_or_unverified_assignments'], 1)

    def test_same_human_activity_cannot_inflate_independent_samples(self):
        rows = cohort('ai_platform', 1, 1)
        rows.append(make_record('experiments', {**rows[1]['payload'], 'assignment_id': 'duplicate'}, 'duplicate'))
        with self.assertRaises(ReportingConflict):
            run_reporting(Store(rows), {}, NOW)

    def test_receipt_reused_by_two_applications_fails_closed(self):
        rows = [application('a', 0, 'applied_verified', receipt=receipt('same')), application('b', 0, 'applied_verified', receipt=receipt('same'))]
        with self.assertRaises(ReportingConflict):
            run_reporting(Store(rows), {}, NOW)

    def test_stage_observation_is_distinct_from_qa_packet_evidence(self):
        packet = {'qa': {'verified': True}, 'missing': [], **{k: 'a'*64 for k in ('resume_sha256', 'source_sha256', 'ground_truth_sha256', 'answers_hash')}}
        rows = [application('old-draft', 0, 'draft_ready'), application('ready', 0, 'prepared', packet=packet)]
        result = run_reporting(Store(rows), {}, NOW)
        self.assertEqual(result['counts']['prepared_application_observations'], 2)
        self.assertEqual(result['counts']['qa_ready_packet_evidence_applications'], 1)
        self.assertEqual(result['counts']['verified_submitted_applications'], 0)

    def test_qualification_requires_real_ground_truth_links_and_official_jd(self):
        jd = 'Requirements:\nSQL and Python'
        job = {'company': 'Fixture', 'requisition_id': 'one', 'jd': jd, 'observed_at': NOW.isoformat(), 'evidence_url': 'https://example.test/jobs/one'}
        grade = {'qualified': True, 'reason': 'truthful_fit', 'coverage': 1.0,
                 'requirements': [{'requirement': 'SQL and Python', 'evidenced': True, 'gaps': [], 'fact_ids': ['E1-CDC', 'E-REPORTAUTO']}]}
        good = application('a', 0, 'qualified', job=job, jd_hash=content_hash(jd), qualification=grade)
        bad = application('b', 0, 'qualified', job={**job, 'requisition_id': 'two'}, jd_hash=content_hash(jd),
                          qualification={**grade, 'requirements': [{**grade['requirements'][0], 'fact_ids': ['INVENTED']}]})
        self.assertEqual(run_reporting(Store([good, bad]), {}, NOW)['counts']['jobs_with_qualification_evidence'], 1)

    def test_dry_run_never_mutates_base_and_live_rerun_is_idempotent(self):
        store = Store()
        run_reporting(store, {}, NOW)
        self.assertEqual(store.writes, [])
        self.assertEqual(store.rows, {})
        first = run_reporting(store, {}, NOW, dry_run=False)
        count = len(store.rows)
        second = run_reporting(store, {}, NOW, dry_run=False)
        self.assertEqual(first, second)
        self.assertEqual(len(store.rows), count)

    def test_no_cursor_advances_on_uncertain_write_or_failed_readback(self):
        for failure in ('fail_kind', 'hide_kind'):
            store = Store(); setattr(store, failure, 'funnel_reports')
            with self.assertRaises((ReportingConflict, AmbiguousWriteError)):
                run_reporting(store, {}, NOW, dry_run=False)
            self.assertEqual(store.query('sync_cursors'), [])

    def test_cursor_binds_complete_snapshot_and_report_readback(self):
        store = Store([make_record('contacts', {'name': 'private fixture'}, 'c')])
        result = run_reporting(store, {}, NOW, dry_run=False)
        cursor = store.get(result['sync_cursor_id'])['payload']
        report = store.get(cursor['report_id'])['payload']
        self.assertTrue(cursor['complete'])
        self.assertEqual(cursor['source_snapshot_hash'], report['source_snapshot_hash'])
        self.assertNotIn('private fixture', str(result))

    def test_invalid_policy_or_ambiguous_revision_blocks(self):
        with self.assertRaises(ValueError):
            run_reporting(Store(), {'reporting': {'min_matured_assignments_per_family': 0}}, NOW)
        with self.assertRaises(ValueError):
            run_reporting(Store(), {}, NOW.replace(tzinfo=None))
        rows = [application('a', 0, 'prepared'), make_record('applications', {'application_id': 'a', 'revision': 0}, 'other', 'prepared')]
        with self.assertRaises(ReportingConflict):
            run_reporting(Store(rows), {}, NOW)

    def test_research_feedback_requires_complete_cursor_and_fresh_source(self):
        config = {'outreach': {'research_ai_share': .6, 'research_baseline_ai_share': .6}}
        store = Store(cohort('ai_platform', 20, 15) + cohort('data_platform', 20, 5))
        report = run_reporting(store, config, NOW, dry_run=False)
        self.assertEqual(effective_research_share(store, config, NOW), .7)
        cursor = store.rows.pop(report['sync_cursor_id'])
        self.assertEqual(effective_research_share(store, config, NOW), .6)
        store.rows[cursor['id']] = cursor
        source = store.rows['activity:ai_platform:0']
        store.rows[source['id']] = make_record(source['kind'], {**source['payload'], 'contact_id': 'changed'}, source['id'], source['status'])
        self.assertEqual(effective_research_share(store, config, NOW), .6)

    def test_identical_outcomes_never_ratchet_daily_research_share(self):
        config = {'outreach': {'research_ai_share': .6, 'research_baseline_ai_share': .6}}
        store = Store(cohort('ai_platform', 20, 15) + cohort('data_platform', 20, 5))
        run_reporting(store, config, NOW, dry_run=False)
        next_config = {'outreach': {**config['outreach'], 'research_ai_share': effective_research_share(store, config, NOW)}}
        later = NOW + timedelta(days=1)
        report = run_reporting(store, next_config, later, dry_run=False)
        self.assertEqual(report['allocation']['current_ai_share'], .7)
        self.assertEqual(report['allocation']['proposed_ai_share'], .7)
        self.assertEqual(effective_research_share(store, config, later), .7)
        self.assertEqual(effective_research_share(store, config, later + timedelta(days=8)), .6)

    def test_feedback_rejects_forged_rates_and_changed_policy(self):
        config = {'outreach': {'research_ai_share': .6, 'research_baseline_ai_share': .6}}
        store = Store(cohort('ai_platform', 20, 15) + cohort('data_platform', 20, 5))
        result = run_reporting(store, config, NOW, dry_run=False)
        changed_config = {**config, 'reporting': {'min_matured_assignments_per_family': 100}}
        self.assertEqual(effective_research_share(store, changed_config, NOW), .6)
        report = store.rows[result['report_id']]
        payload = copy.deepcopy(report['payload']); payload['allocation']['proposed_ai_share'] = .9
        store.rows[report['id']] = make_record('funnel_reports', payload, report['id'], report['status'])
        self.assertEqual(effective_research_share(store, config, NOW), .6)


if __name__ == '__main__':
    unittest.main()
