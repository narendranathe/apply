"""Publication freshness uses the trusted clock after current source reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
import unittest
from unittest.mock import Mock, patch

from cloud.agent_index import build_index
from cloud.index_publisher import IndexPublicationError, publish_index, snapshot_hash
from cloud.orchestrator import ShadowStore, execute
from tests.test_agent_index import app, job, projection, tasks
from tests.test_index_publisher import FreshStore


START = datetime(2026, 10, 22, 16, 39, 11, 546727, tzinfo=timezone.utc)
OBSERVED = datetime(2026, 10, 22, 16, 48, 27, 785697, tzinfo=timezone.utc)
PUBLISHED = datetime(2026, 10, 22, 16, 50, tzinfo=timezone.utc)


class OrchestratorPublicationClockTests(unittest.TestCase):
    def test_runtime_indexes_an_official_observation_made_after_run_start_as_fresh(self):
        # Same ordering as actual run39809642771: the application lane observes
        # the official job more than nine minutes after the run clock freezes.
        source = job(observed_at=OBSERVED.isoformat(), source='fresh_official_job')
        staged = []

        def applications(store, config, **kwargs):
            store.put(source)
            return {'external_effects': 0}

        config = {'destination_private': True, 'cloud_research_enabled': True,
                  'reconcile_lookback_days': 0}
        with patch('cloud.orchestrator.datetime', wraps=datetime) as clock, \
             patch('cloud.reporting.effective_research_share', return_value=.6), \
             patch('cloud.reporting.run_reporting', return_value={'counts': {}}) as reporting, \
             patch('cloud.applications.run_applications', side_effect=applications):
            clock.now.side_effect = [START, PUBLISHED]
            execute(ShadowStore(), config, mode='research-only', requested='applications',
                    preview_sink=staged.extend)
        self.assertEqual(projection(staged, 'job:acme:req1')['freshness'], 'fresh')
        manifest = next(row for row in staged if row['kind'] == 'index_manifests')
        self.assertEqual(manifest['payload']['generated_at'], PUBLISHED.isoformat())
        self.assertFalse(manifest['payload']['complete'])
        self.assertEqual(reporting.call_args.args[2], START)
        self.assertEqual(clock.now.call_count, 2)

    def test_explicit_logical_time_keeps_replay_frozen_unless_clock_is_provided(self):
        source = job(observed_at=OBSERVED.isoformat(), source='fresh_official_job')
        for publication_clock, expected, freshness in (
                (None, START, 'unknown'), (Mock(return_value=PUBLISHED), PUBLISHED, 'fresh')):
            with self.subTest(publication_clock=publication_clock):
                staged = []
                base = ShadowStore()
                base.put(source)
                with patch('cloud.orchestrator.datetime', wraps=datetime) as clock, \
                     patch('cloud.reporting.effective_research_share', return_value=.6), \
                     patch('cloud.reporting.run_reporting', return_value={'counts': {}}), \
                     patch('cloud.applications.run_applications', return_value={'external_effects': 0}):
                    clock.now.side_effect = AssertionError('Explicit time must not sample wall clock')
                    execute(base, {}, now=START, requested='applications', preview_sink=staged.extend,
                            publication_clock=publication_clock)
                self.assertEqual(projection(staged, 'job:acme:req1')['freshness'], freshness)
                manifest = next(row for row in staged if row['kind'] == 'index_manifests')
                self.assertEqual(manifest['payload']['generated_at'], expected.isoformat())
                clock.now.assert_not_called()
                if publication_clock is not None:
                    publication_clock.assert_called_once_with()


class PublisherPublicationClockTests(unittest.TestCase):
    def publish(self, source, publication_clock, *, now=START):
        store = FreshStore(source)
        result = publish_index(store, now, source_complete=True,
                               source_snapshot_hash=snapshot_hash(source),
                               publication_clock=publication_clock)
        return store, result

    def test_clock_is_sampled_once_after_both_fresh_reads_and_previous_manifest(self):
        trace = []

        class OrderedStore(FreshStore):
            def query(self, kind=None):
                trace.append(('query', kind))
                return super().query(kind)

            def latest_index_manifest(self):
                trace.append(('previous', None))
                return super().latest_index_manifest()

            def put(self, row):
                trace.append(('put', row['kind']))
                return super().put(row)

        def sample():
            self.assertEqual(trace, [('query', None), ('query', 'outreach_events'), ('previous', None)])
            trace.append(('clock', None))
            return PUBLISHED

        source = [job(observed_at=OBSERVED.isoformat())]
        store = OrderedStore(source)
        clock = Mock(side_effect=sample)
        result = publish_index(store, START, source_complete=True,
                               source_snapshot_hash=snapshot_hash(source), publication_clock=clock)
        clock.assert_called_once_with()
        self.assertTrue(result['complete'])
        self.assertEqual(projection(list(store.records.values()), 'job:acme:req1')['freshness'], 'fresh')
        self.assertEqual(trace[4][0], 'put')
        self.assertEqual(trace[-1], ('put', 'index_manifests'))

    def test_genuine_future_and_stale_sources_are_not_advanced_or_refreshed(self):
        for observed, expected in ((PUBLISHED + timedelta(seconds=1), 'unknown'),
                                   (PUBLISHED - timedelta(hours=24, seconds=1), 'stale')):
            with self.subTest(observed=observed):
                source = job(observed_at=observed.isoformat(), source='fresh_official_job')
                store, result = self.publish([source], lambda: PUBLISHED)
                self.assertEqual(projection(list(store.records.values()), 'job:acme:req1')['freshness'], expected)
                self.assertEqual(result['manifest']['payload']['generated_at'], PUBLISHED.isoformat())
                self.assertEqual(store.get(source['id']), source)

    def test_freshness_correction_preserves_ambiguous_history_suppression(self):
        legacy = app(status='prepared', requisition_id=None, employer_history_unresolved=True)
        source = [legacy, job(requisition_id='different-req',
                              url='https://jobs.lever.co/acme/different-req',
                              observed_at=OBSERVED.isoformat())]
        store, _ = self.publish(source, lambda: PUBLISHED)
        rows = list(store.records.values())
        value = projection(rows, 'job:acme:req1')
        self.assertEqual(value['freshness'], 'fresh')
        self.assertEqual(value['state'], 'needs_reconciliation')
        self.assertTrue(value['suppression']['retry_blocked'])
        self.assertIn('duplicate_application_suppressed', value['blockers'])
        self.assertIn('ambiguous_application_identity', value['blockers'])
        self.assertEqual([task['type'] for task in tasks(rows, 'job:acme:req1')], ['reconcile'])

    def test_invalid_or_backwards_clock_stops_before_generated_writes(self):
        class NotAware(tzinfo):
            def utcoffset(self, value):
                return None

        invalid = (None, '2026-10-22T16:50:00Z', PUBLISHED.replace(tzinfo=None),
                   PUBLISHED.replace(tzinfo=NotAware()), START - timedelta(microseconds=1))
        for sampled in invalid:
            with self.subTest(sampled=sampled):
                store = FreshStore([job(observed_at=OBSERVED.isoformat())])
                with self.assertRaises(ValueError):
                    publish_index(store, START, publication_clock=lambda: sampled)
                self.assertEqual(store.put_calls, [])
        for invalid_clock in (True, 'not a callable'):
            with self.subTest(invalid_clock=invalid_clock):
                store = FreshStore([job()])
                with self.assertRaises(ValueError):
                    publish_index(store, START, publication_clock=invalid_clock)
                self.assertEqual(store.put_calls, [])

    def test_clock_failure_is_fixed_and_stops_before_generated_writes(self):
        store = FreshStore([job()])
        clock = Mock(side_effect=RuntimeError('private clock diagnostic'))
        with self.assertRaisesRegex(ValueError, '^Index publication clock unavailable$'):
            publish_index(store, START, publication_clock=clock)
        self.assertEqual(store.put_calls, [])

    def test_fresh_clock_cannot_publish_before_the_previous_manifest(self):
        store = FreshStore([job()])
        previous = publish_index(store, PUBLISHED + timedelta(seconds=1))['manifest']
        original = deepcopy(store.records)
        store.put_calls.clear()
        with self.assertRaisesRegex(ValueError, 'precedes previous manifest'):
            publish_index(store, START, publication_clock=lambda: PUBLISHED)
        self.assertEqual(store.put_calls, [])
        self.assertEqual(store.records, original)
        self.assertEqual(store.latest_index_manifest(), previous)

    def test_equal_logical_and_previous_time_is_permitted(self):
        store = FreshStore([job(observed_at=START.isoformat())])
        publish_index(store, START)
        result = publish_index(store, START, publication_clock=lambda: START)
        self.assertEqual(result['manifest']['payload']['generated_at'], START.isoformat())

    def test_source_failure_does_not_sample_clock_or_write(self):
        store = FreshStore([job()])
        store.fail_query = 'outreach_events'
        clock = Mock(return_value=PUBLISHED)
        with self.assertRaisesRegex(IndexPublicationError, 'authoritative_snapshot_unavailable'):
            publish_index(store, START, publication_clock=clock)
        clock.assert_not_called()
        self.assertEqual(store.put_calls, [])

    def test_explicit_time_preserves_exact_historical_constructor_output(self):
        source = [job(observed_at=OBSERVED.isoformat())]
        evidence = {'hash': snapshot_hash(source), 'complete': True, 'conflicts': []}
        expected = build_index(source, START, source_snapshot_hash=evidence)
        store = FreshStore(source)
        publish_index(store, START, source_complete=True, source_snapshot_hash=evidence)
        observed = [store.get(row['id']) for row in expected]
        self.assertEqual(observed, expected)
        self.assertEqual(set(store.records) - {row['id'] for row in source}, {row['id'] for row in expected})


if __name__ == '__main__':
    unittest.main()
