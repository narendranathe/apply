import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sync', Path(__file__).resolve().parents[1] / 'outputs/outreach_sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
REMOTE = 'https://app.notion.com/p/11111111111111111111111111111111'
OTHER_REMOTE = 'https://app.notion.com/p/22222222222222222222222222222222'
PAIR_EVENT = 'v3-send-intent-hannah-20260924-0111'
PAIR_URL = 'https://app.notion.com/p/3e5e10221fac8113b2d8dc7b4b21250f'

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.j = sync.Journal(self.base/'state', self.base/'log.md', self.base/'offline.md')
        self.e = {'event_id':'test-1','title':'Exact message','actor':'Muse','kind':'Message','occurred_at':'2026-09-22T12:00:00Z','content':'Hello — exact Unicode.\n\nLine two.','delivery_state':'Sent verified','evidence':'Fictional test confirmation'}

    def tearDown(self):
        self.j.db.close()
        self.tmp.cleanup()

    def test_replay_and_two_matching_files(self):
        self.j.record(self.e)
        self.j.record(self.e)
        self.assertEqual(self.j.status()['events'],1)
        self.assertEqual(self.j.log.read_text().count('<!-- outreach-event:test-1 -->'),1)
        self.assertEqual(self.j.log.read_bytes(),self.j.mirror.read_bytes())

    def test_changed_payload_and_hash_are_rejected(self):
        self.j.record(self.e)
        with self.assertRaises(ValueError):
            self.j.record(dict(self.e,content='Changed'))
        with self.assertRaises(ValueError):
            self.j.record(dict(self.e,event_id='test-2',content_hash='bad'))

    def test_crash_after_commit_recovers(self):
        original = self.j._mirror
        self.j._mirror = lambda: (_ for _ in ()).throw(OSError('simulated crash'))
        with self.assertRaises(OSError):
            self.j.record(self.e)
        self.assertEqual(self.j.status()['events'],1)
        self.j._mirror = original
        self.j.record(self.e)
        self.assertIn(self.e['content'],self.j.log.read_text())

    def test_ack_validates_remote_hash_and_duplicate_url(self):
        e = self.j.record(self.e)
        with self.assertRaises(ValueError):
            self.j.acknowledge([{'event_id':'test-1','content_hash':'bad','notion_url':REMOTE}])
        self.j.acknowledge([{'event_id':'test-1','content_hash':e['content_hash'],'notion_url':REMOTE}])
        self.assertEqual(self.j.pending(),[])
        with self.assertRaises(ValueError):
            self.j.record(e,OTHER_REMOTE)

    def test_inbound_event_is_mirrored_exactly_once(self):
        self.j.record(self.e,REMOTE)
        self.j.record(self.e,REMOTE)
        self.assertEqual(self.j.pending(),[])
        self.assertIn(self.e['content'],self.j.log.read_text())

    def test_local_edit_or_partial_append_stops_sync(self):
        self.j.record(self.e)
        self.j.log.write_text(self.j.log.read_text().replace('Line two.','Changed locally.'))
        with self.assertRaises(ValueError):
            self.j.record(self.e)

    def test_legacy_preserved_without_duplicate_append(self):
        original = '# Outreach log\n\n## September 9, 2026: Test Person, Fictional Company\n\nProfile: https://www.linkedin.com/in/fictional/\n\nExact note:\n\n> Unicode — message\n'
        self.j.log.write_text(original)
        events = sync.legacy_events(self.j.log)
        self.assertEqual(len(events),1)
        self.j.record(events[0],legacy=True)
        self.j.record(events[0],legacy=True)
        self.assertEqual(self.j.log.read_text(),original)

    def test_verified_send_without_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            self.j.record(dict(self.e,evidence=''))

    def test_equivalent_notion_urls_do_not_create_a_false_conflict(self):
        self.j.record(self.e,REMOTE+'?pvs=204')
        self.j.record(self.e,'https://www.notion.so/Some-title-11111111-1111-1111-1111-111111111111')
        self.assertEqual(self.j.pending(),[])

    def test_external_ack_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.j.record(self.e,'https://example.com/11111111111111111111111111111111')

    def test_batch_is_atomic_idempotent_and_mirrors_once(self):
        calls = []
        original = self.j._mirror
        def mirror():
            calls.append(1)
            original()
        self.j._mirror = mirror
        items = [{'payload': self.e}, {'payload': dict(self.e, event_id='test-2')}]
        self.j.record_batch(items)
        self.assertEqual(len(calls), 1)
        self.j.record_batch(items)
        self.assertEqual(self.j.status()['events'], 2)
        with self.assertRaises(ValueError):
            self.j.record_batch([{'payload':dict(self.e,event_id='test-3')},
                                 {'payload':dict(self.e,content='conflict')}])
        self.assertEqual(self.j.status()['events'], 2)
        self.assertEqual(self.j.log.read_bytes(), self.j.mirror.read_bytes())

    def test_exact_pair_metadata_reconciliation_is_pending_then_acknowledged(self):
        local = dict(self.e, event_id=PAIR_EVENT, actor='Codex', kind='Message intent',
                     delivery_state='Approved for send', evidence='approval proof')
        local['content_hash'] = sync.digest(local['content'])
        remote = dict(local, company='H1', work_key='wk-example', worker='Codex/run', claim_token='token')
        self.j.record(local, PAIR_URL)
        self.j.acknowledge([{'event_id':PAIR_EVENT,'content_hash':local['content_hash'],'notion_url':PAIR_URL}])
        prefix = self.j.log.read_bytes()
        export_before = self.j.export()
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':remote,'notion_url':PAIR_URL}], 'complete':False})
        proof = self.j.reconcile_metadata({
            'correction_event_id':'repair-hannah', 'occurred_at':'2026-10-01T12:00:00Z',
            'authorization':'Naren approved the narrow exact-pair repair on October 1, 2026.',
            'reason':'Restore acknowledged optional queue metadata without changing the original event.',
            'notion_url':PAIR_URL, 'local_payload':local, 'notion_payload':remote})
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':remote,'notion_url':PAIR_URL}], 'complete':False})
        self.j.acknowledge([{'event_id':proof['event_id'],'content_hash':proof['content_hash'],
                             'notion_url':OTHER_REMOTE}])
        envelope={'events':[{'payload':remote,'notion_url':PAIR_URL}], 'complete':False}
        self.assertEqual(self.j.ingest(envelope),[remote])
        self.assertEqual(self.j.ingest(envelope),[remote])
        self.assertEqual(self.j.export()[0],export_before[0])
        self.assertEqual(self.j.log.read_bytes()[:len(prefix)],prefix)

    def test_metadata_proof_rejects_unapproved_differences_and_bad_authority(self):
        local = dict(self.e, event_id=PAIR_EVENT, actor='Codex', kind='Message intent',
                     delivery_state='Approved for send', evidence='approval proof')
        local['content_hash'] = sync.digest(local['content'])
        self.j.record(local, PAIR_URL)
        self.j.acknowledge([{'event_id':PAIR_EVENT,'content_hash':local['content_hash'],'notion_url':PAIR_URL}])
        remote = dict(local, company='H1')
        base={'correction_event_id':'repair-bad','occurred_at':'2026-10-01T12:00:00Z',
              'authorization':'Naren approved this on October 1, 2026.','reason':'Approved repair.',
              'notion_url':PAIR_URL,'local_payload':local,'notion_payload':remote}
        for field, value in [('authorization',''),('reason',''),('notion_url',OTHER_REMOTE),
                             ('local_payload',dict(local,title='changed')),
                             ('notion_payload',dict(remote,unknown='x')),
                             ('notion_payload',dict(remote,content='changed')),
                             ('notion_payload',dict(remote,delivery_state='Sent verified')),
                             ('notion_payload',{k:v for k,v in remote.items() if k != 'actor'}),
                             ('notion_payload',dict(remote,company=''))]:
            with self.subTest(field=field, value=str(value)[:30]), self.assertRaises(ValueError):
                self.j.reconcile_metadata(dict(base, **{field:value}))

        for key in ('content_hash','contact_key','delivery_state','evidence','occurred_at','title','actor','kind'):
            altered=dict(remote)
            altered[key]='changed'
            if key == 'content_hash':
                altered[key]='0'*64
            with self.subTest(changed_original_key=key), self.assertRaises(ValueError):
                self.j.reconcile_metadata(dict(base, notion_payload=altered))

    def test_variant_after_proof_change_and_second_target_stay_rejected(self):
        local=dict(self.e,event_id=PAIR_EVENT,actor='Codex',kind='Message intent',delivery_state='Approved for send')
        local['content_hash']=sync.digest(local['content'])
        remote=dict(local,company='H1')
        self.j.record(local,PAIR_URL)
        self.j.acknowledge([{'event_id':PAIR_EVENT,'content_hash':local['content_hash'],'notion_url':PAIR_URL}])
        proof=self.j.reconcile_metadata({'correction_event_id':'repair-exact','occurred_at':'2026-10-01T12:00:00Z',
            'authorization':'Naren approved the narrow exact-pair repair on October 1, 2026.',
            'reason':'Restore optional metadata.','notion_url':PAIR_URL,'local_payload':local,'notion_payload':remote})
        self.j.acknowledge([{'event_id':proof['event_id'],'content_hash':proof['content_hash'],'notion_url':OTHER_REMOTE}])
        with self.assertRaises(ValueError):
            self.j.record(remote)
        for changed, url in [(dict(remote,company='Changed'),PAIR_URL),(dict(remote,work_key='wk-extra'),PAIR_URL),
                             (dict(remote,company='H1'),OTHER_REMOTE)]:
            with self.subTest(changed=changed,url=url), self.assertRaises(ValueError):
                self.j.ingest({'events':[{'payload':changed,'notion_url':url}], 'complete':False})

        second=dict(local,event_id='v3-send-intent-shirl-20260924-0111')
        second['content_hash']=sync.digest(second['content'])
        second_url='https://app.notion.com/p/3e5e10221fac81178bdffac0a5dcef81'
        second_variant=dict(second,company='ServiceNow')
        self.j.record(second,second_url)
        self.j.acknowledge([{'event_id':second['event_id'],'content_hash':second['content_hash'],'notion_url':second_url}])
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':second_variant,'notion_url':second_url}], 'complete':False})

    def test_ingest_collision_rolls_back_entire_batch_and_keeps_watermark(self):
        self.j.record(self.e, REMOTE)
        complete={'events':[{'payload':self.e,'notion_url':REMOTE}], 'complete':True,'captured_at':sync.now()}
        self.j.ingest(complete)
        watermark=self.j.status()['last_complete_pull']
        new_event=dict(self.e,event_id='new-before-collision')
        changed=dict(self.e,content='collision')
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':new_event,'notion_url':OTHER_REMOTE},
                                     {'payload':changed,'notion_url':REMOTE}],
                           'complete':True,'captured_at':sync.now()})
        self.assertNotIn('new-before-collision',[row['payload']['event_id'] for row in self.j.export()])
        self.assertEqual(self.j.status()['last_complete_pull'],watermark)

    def test_proof_exact_json_comparison_distinguishes_missing_null_and_bool_number(self):
        local=dict(self.e,event_id=PAIR_EVENT,actor='Codex',kind='Message intent',
                   delivery_state='Approved for send',optional_null=None,optional_bool=True)
        local['content_hash']=sync.digest(local['content'])
        self.j.record(local,PAIR_URL)
        self.j.acknowledge([{'event_id':PAIR_EVENT,'content_hash':local['content_hash'],'notion_url':PAIR_URL}])
        remote=dict(local,company='H1')
        request={'correction_event_id':'repair-json-exact','occurred_at':'2026-10-01T12:00:00Z',
                 'authorization':'Naren approved the narrow exact-pair repair on October 1, 2026.',
                 'reason':'Exact JSON comparison regression.','notion_url':PAIR_URL,
                 'local_payload':local,'notion_payload':remote}
        for altered in (dict(remote,optional_null='missing'),
                        {k:v for k,v in remote.items() if k!='optional_null'},
                        dict(remote,optional_bool=1)):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                self.j.reconcile_metadata(dict(request,notion_payload=altered))
        proof=self.j.reconcile_metadata(dict(request,correction_event_id='repair-json-valid'))
        self.j.acknowledge([{'event_id':proof['event_id'],'content_hash':proof['content_hash'],'notion_url':OTHER_REMOTE}])
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':dict(remote,optional_bool=1),'notion_url':PAIR_URL}], 'complete':False})

    def test_ingest_batch_mirrors_once(self):
        events=[dict(self.e,event_id='batch-a'),dict(self.e,event_id='batch-b')]
        calls=[]
        original=self.j._mirror
        def mirror_once():
            calls.append(1)
            original()
        self.j._mirror=mirror_once
        self.j.ingest({'events':[{'payload':event,'notion_url':url} for event,url in
                                 zip(events,(REMOTE,OTHER_REMOTE))], 'complete':False})
        self.assertEqual(len(calls),1)

    def test_failed_log_reconciliation_keeps_successful_pull_watermark(self):
        self.j.record(self.e, REMOTE)
        self.j.ingest({'events':[], 'complete':True, 'captured_at':sync.now()})
        watermark = self.j.status()['last_complete_pull']
        self.j.log.write_text(self.j.log.read_text().replace('Line two.', 'corrupted'))
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[], 'complete':True, 'captured_at':sync.now()})
        self.assertEqual(self.j.status()['last_complete_pull'], watermark)
        self.assertIsNotNone(self.j.status()['ingest_error'])

    def test_proof_timestamp_is_bound_to_outer_event_timestamp(self):
        local=dict(self.e,event_id='v3-send-intent-shirl-20260924-0111',actor='Codex',
                   kind='Message intent',delivery_state='Approved for send')
        local['content_hash']=sync.digest(local['content'])
        remote=dict(local,company='ServiceNow')
        source='https://app.notion.com/p/3e5e10221fac81178bdffac0a5dcef81'
        self.j.record(local,source)
        self.j.acknowledge([{'event_id':local['event_id'],'content_hash':local['content_hash'],'notion_url':source}])
        content={'schema':'outreach-metadata-reconciliation/v1','event_id':local['event_id'],
                 'notion_url':source,'local_payload':local,'notion_payload':remote,
                 'authorization':'Naren approved on October 1, 2026.','reason':'Timestamp binding test.',
                 'occurred_at':'2026-10-01T12:00:00Z'}
        malformed={'event_id':'proof-time-mismatch','title':'Bad proof','actor':'Codex','kind':'Correction',
                   'occurred_at':'2026-10-01T12:01:00Z','content':sync.dump(content),
                   'delivery_state':'Not applicable'}
        proof=self.j.record(malformed)
        self.j.acknowledge([{'event_id':proof['event_id'],'content_hash':proof['content_hash'],'notion_url':OTHER_REMOTE}])
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':remote,'notion_url':source}], 'complete':False})

    def test_ingest_failure_blocks_fresh_readiness_until_complete_success(self):
        valid={'events':[],'complete':True,'captured_at':sync.now()}
        self.j.ingest(valid)
        self.assertIsNone(self.j.status()['ingest_error'])
        self.j.record(self.e, REMOTE)
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[{'payload':dict(self.e,content='bad'),'notion_url':REMOTE}], 'complete':False})
        self.assertIsNotNone(self.j.status()['ingest_error'])
        with self.assertRaises(ValueError):
            self.j.ingest({'events':[],'complete':True,'captured_at':'2026-10-01T12:00:00'})
        self.assertIsNotNone(self.j.status()['ingest_error'])
        args=[sys.executable,str(Path(sync.__file__)),'--state',str(self.base/'state'),
              '--log',str(self.base/'log.md'),'--mirror',str(self.base/'offline.md'),'preflight']
        blocked=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(blocked.returncode,2)
        self.assertFalse(json.loads(blocked.stdout)['sync_ready'])
        self.j.ingest(valid)
        self.assertIsNone(self.j.status()['ingest_error'])
        ready=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(ready.returncode,0,ready.stdout+ready.stderr)
        self.assertTrue(json.loads(ready.stdout)['sync_ready'])

    def test_pull_timestamp_boundaries_and_ack_do_not_advance_watermark(self):
        future=(sync.dt.datetime.now(sync.dt.timezone.utc)+sync.dt.timedelta(minutes=1)).isoformat()
        for stamp in ('2026-10-01T12:00:00', '2026-09-01T12:00:00Z', future):
            with self.subTest(captured_at=stamp), self.assertRaises(ValueError):
                self.j.ingest({'events':[],'complete':True,'captured_at':stamp})
        self.j.record(self.e)
        event=self.j.export()[0]['payload']
        self.j.acknowledge([{'event_id':event['event_id'],'content_hash':event['content_hash'],'notion_url':REMOTE}])
        self.assertIsNone(self.j.status()['last_complete_pull'])

    def test_reconcile_metadata_cli_uses_only_temporary_paths(self):
        # Exercise CLI dispatch and pending proof status without shared state.
        state=self.base/'cli-state'; log=self.base/'cli.log'; mirror=self.base/'cli-mirror.md'
        local=dict(self.e,event_id=PAIR_EVENT,actor='Codex',kind='Message intent',delivery_state='Approved for send')
        local['content_hash']=sync.digest(local['content'])
        seeded=sync.Journal(state,log,mirror)
        seeded.record(local,PAIR_URL)
        seeded.acknowledge([{'event_id':PAIR_EVENT,'content_hash':local['content_hash'],'notion_url':PAIR_URL}])
        seeded.db.close()
        args=[sys.executable,str(Path(sync.__file__)),'--state',str(state),'--log',str(log),'--mirror',str(mirror),
              'reconcile-metadata','--input',str(self.base/'request.json')]
        request={'correction_event_id':'invalid-proof','occurred_at':'2026-10-01T12:00:00Z',
                 'authorization':'Naren approved this on October 1, 2026.','reason':'test',
                 'notion_url':PAIR_URL,'local_payload':local,'notion_payload':dict(local,company='H1')}
        (self.base/'request.json').write_text(json.dumps(request))
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        status=json.loads(result.stdout)
        self.assertEqual(status['pending_notion'],1)
        self.assertEqual(status['events'],2)

if __name__ == '__main__':
    unittest.main()
