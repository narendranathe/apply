"""Evidence-only funnel reporting and conservative, attributable experiments.

No outreach transport, application transport, config mutation, or inferred
outcome from prose exists here. Reporting shares the orchestrator's writer.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from .records import canonical_url, content_hash, make_record, normalized_identity, stable_id, validate_record

SOURCE_KINDS = ('jobs', 'contacts', 'applications', 'outreach_events', 'referrals', 'interviews',
                'conversation_observations', 'experiments')
QUALIFIED = {'recruiter_screen_confirmed', 'interview_confirmed'}
REFERRALS = {'referral_offered_confirmed', 'referral_submitted_confirmed'}
ACTIVITY = {'message_sent_confirmed', 'invitation_sent_confirmed'}
HYPOTHESES = (
    ('hypothesis:warm-introductions:v1', 'warm_introduction', ('warm_introduction', 'direct_route'),
     'A genuine warm introduction may produce more qualified conversations than a direct route.'),
    ('hypothesis:manager-versus-recruiter:v1', 'hiring_route', ('verified_manager', 'verified_recruiter'),
     'A verified manager route may differ from a verified recruiter route in qualified outcomes.'),
    ('hypothesis:proof-versus-curiosity:v1', 'message_focus', ('concise_proof', 'role_specific_curiosity'),
     'Concise verified proof and role-specific curiosity may differ in qualified outcomes.'),
    ('hypothesis:professional-network-versus-role-route:v1', 'network_scope', ('broader_professional_network', 'role_specific_route'),
     'Broader genuine professional networking may differ from a role-specific route in qualified outcomes.'),
)


class ReportingConflict(RuntimeError):
    pass


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _source_url(value):
    try:
        url = canonical_url(value)
    except (ValueError, TypeError, AttributeError):
        return None
    return url if url.startswith('https://') else None


def _confirmed(evidence, now, *, receipt=False):
    """Accept explicit structured evidence, never a label or free-text note."""
    if not isinstance(evidence, dict):
        return None
    event_id = evidence.get('receipt_id' if receipt else 'event_id')
    when = _stamp(evidence.get('confirmed_at'))
    url = _source_url(evidence.get('evidence_url'))
    if not isinstance(event_id, str) or not event_id.strip() or not url or not when or when > now:
        return None
    if receipt:
        if evidence.get('status') != 'applied_verified' or not isinstance(evidence.get('http_status'), int) or not 200 <= evidence['http_status'] < 300:
            return None
        outcome = 'submission_verified'
    else:
        if evidence.get('verified') is not True or evidence.get('outcome') not in QUALIFIED | REFERRALS | ACTIVITY | {'useful_reply_confirmed'}:
            return None
        outcome = evidence['outcome']
    return {'event_id': event_id, 'confirmed_at': when.isoformat(), 'evidence_url': url,
            'outcome': outcome, 'identity': stable_id('confirmed_event', urlsplit(url).hostname, event_id)}


def _unique(rows):
    found = {}
    for row in rows:
        value = validate_record(row)
        old = found.get(value['id'])
        if old and old != value:
            raise ReportingConflict('source_identity_conflict')
        found[value['id']] = value
    return list(found.values())


def _application_id(record):
    payload = record['payload']
    return payload.get('application_id') or payload.get('dedup_key') or record['id']


def _latest_applications(rows):
    latest = {}
    for row in rows:
        identity = _application_id(row)
        revision = row['payload'].get('revision', 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ReportingConflict('invalid_application_revision')
        old = latest.get(identity)
        if old and old['payload'].get('revision', 0) == revision and old['id'] != row['id']:
            raise ReportingConflict('ambiguous_application_revision')
        if not old or revision > old['payload'].get('revision', 0):
            latest[identity] = row
    return latest


def _job_identity(payload, fallback):
    job = payload.get('job') if isinstance(payload.get('job'), dict) else payload
    if payload.get('job_id'):
        return payload['job_id']
    if isinstance(job.get('company'), str) and job.get('requisition_id'):
        return stable_id('job', normalized_identity(job['company']), str(job['requisition_id']))
    source = payload.get('source_notion_id')
    return 'notion:' + source.replace('-', '').lower() if isinstance(source, str) and source else fallback


def _qualification(payload, facts, now, min_coverage):
    """Count recorded requirement-to-fact evidence, never a feed/fit score."""
    grade, job = payload.get('qualification'), payload.get('job', payload)
    if not isinstance(grade, dict) or grade.get('qualified') is not True or grade.get('reason') != 'truthful_fit' or not isinstance(job, dict):
        return False
    jd, observed = job.get('jd'), _stamp(job.get('observed_at'))
    requirements = grade.get('requirements')
    if (not isinstance(jd, str) or not jd or payload.get('jd_hash') != content_hash(jd)
            or not _source_url(payload.get('official_evidence_url') or job.get('evidence_url'))
            or not observed or observed > now or not isinstance(requirements, list) or not requirements):
        return False
    matched = 0
    for requirement in requirements:
        if not isinstance(requirement, dict) or not isinstance(requirement.get('requirement'), str) or requirement['requirement'] not in jd:
            return False
        keys = requirement.get('fact_ids')
        if (requirement.get('evidenced') is True and requirement.get('gaps') == [] and isinstance(keys, list) and keys
                and all(isinstance(k, str) and k in facts and '[VERIFY]' not in facts[k] and '[BLOCKED]' not in facts[k] for k in keys)):
            matched += 1
    coverage = round(matched / len(requirements), 4)
    return matched > 0 and grade.get('coverage') == coverage and coverage >= min_coverage


def _qa_packet(payload):
    packet = payload.get('packet')
    if not isinstance(packet, dict) or not isinstance(packet.get('qa'), dict) or packet['qa'].get('verified') is not True or packet.get('missing'):
        return False
    return all(isinstance(packet.get(k), str) and re.fullmatch('[0-9a-f]{64}', packet[k])
               for k in ('resume_sha256', 'source_sha256', 'ground_truth_sha256', 'answers_hash'))


def _policy(config):
    value = config.get('reporting', {})
    policy = {k: value.get(k, default) for k, default in (
        ('maturity_days', 14), ('min_matured_assignments_per_family', 20),
        ('min_qualified_assignments_per_family', 5), ('min_rate_difference', 0.10),
        ('max_mix_shift', 0.10))}
    for key in ('maturity_days', 'min_matured_assignments_per_family', 'min_qualified_assignments_per_family'):
        if not isinstance(policy[key], int) or isinstance(policy[key], bool) or policy[key] < 1:
            raise ValueError('Invalid reporting sample policy')
    for key in ('min_rate_difference', 'max_mix_shift'):
        if isinstance(policy[key], bool) or not isinstance(policy[key], (int, float)) or not 0 < policy[key] <= 1:
            raise ValueError('Invalid reporting rate policy')
    return policy


def _outcomes(sources, now):
    events, ignored = {}, 0
    for kind in ('outreach_events', 'referrals', 'interviews', 'conversation_observations'):
        for record in sources[kind]:
            payload = record['payload']
            raw = payload.get('confirmation_evidence')
            if raw is None:
                continue
            evidence = _confirmed(raw, now)
            if evidence is None:
                ignored += 1
                continue
            if not payload.get('contact_id'):
                ignored += 1
                continue
            evidence.update(contact_id=payload['contact_id'], job_id=payload.get('job_id'),
                            assignment_id=payload.get('assignment_id'))
            old = events.get(evidence['identity'])
            if old and old['evidence'] != evidence:
                raise ReportingConflict('confirmation_event_conflict')
            if old:
                old['source_record_ids'].add(record['id'])
            else:
                events[evidence['identity']] = {'evidence': evidence, 'source_record_ids': {record['id']}}
    return events, ignored


def _assignment_attribution(assignments, outcomes, source_index, now, policy):
    """Only prospectively recorded human assignments with exact source evidence."""
    definitions = {key: set(arms) for key, _, arms, _ in HYPOTHESES}
    assignments_by_id, used_activity, invalid = {}, {}, 0
    for row in assignments:
        payload = row['payload']
        identity = payload.get('assignment_id')
        if identity in assignments_by_id and assignments_by_id[identity] != row:
            raise ReportingConflict('experiment_assignment_identity_conflict')
        if identity:
            assignments_by_id[identity] = row
        else:
            invalid += 1
    valid = []
    for identity, row in assignments_by_id.items():
        payload = row['payload']
        activity_row = source_index.get(payload.get('source_event_id'))
        activity = _confirmed(activity_row['payload'].get('confirmation_evidence'), now) if activity_row else None
        assigned_at = _stamp(payload.get('assigned_at'))
        if (payload.get('hypothesis_id') not in definitions or payload.get('arm') not in definitions[payload['hypothesis_id']]
                or payload.get('role_family') not in {'ai_platform', 'data_platform'} or not payload.get('contact_id')
                or not assigned_at or assigned_at > now or not activity or activity['outcome'] not in ACTIVITY
                or assigned_at > _stamp(activity['confirmed_at'])
                or activity_row['payload'].get('contact_id') != payload['contact_id']
                or activity_row['payload'].get('job_id') != payload.get('job_id')
                or payload.get('source_record_hash') != activity_row['hash']):
            invalid += 1
            continue
        key = activity['identity']
        if key in used_activity:
            raise ReportingConflict('same_human_activity_assigned_more_than_once')
        used_activity[key] = identity
        matured = now - _stamp(activity['confirmed_at']) >= timedelta(days=policy['maturity_days'])
        linked = []
        for item in outcomes.values():
            outcome = item['evidence']
            if (outcome['outcome'] in QUALIFIED and outcome.get('assignment_id') == identity
                    and outcome['contact_id'] == payload['contact_id'] and outcome.get('job_id') == payload.get('job_id')
                    and _stamp(outcome['confirmed_at']) >= _stamp(activity['confirmed_at'])):
                linked.append(outcome['identity'])
        valid.append({'assignment_id': identity, 'assignment_record_id': row['id'], 'hypothesis_id': payload['hypothesis_id'],
                      'arm': payload['arm'], 'role_family': payload['role_family'], 'source_event_id': activity_row['id'],
                      'source_record_hash': activity_row['hash'], 'activity_event_id': activity['identity'],
                      'activity_confirmed_at': activity['confirmed_at'], 'matured': matured,
                      'qualified_event_ids': sorted(set(linked)), 'qualified': bool(linked)})
    return sorted(valid, key=lambda entry: entry['assignment_id']), invalid


def _allocation(attributions, policy, current_share, baseline_share=None):
    baseline_share = current_share if baseline_share is None else baseline_share
    by_family = {family: [x for x in attributions if x['role_family'] == family and x['matured']]
                 for family in ('ai_platform', 'data_platform')}
    cohorts = {family: {'matured_assignments': len(rows), 'qualified_assignments': sum(x['qualified'] for x in rows)}
               for family, rows in by_family.items()}
    sufficient = all(c['matured_assignments'] >= policy['min_matured_assignments_per_family']
                     and c['qualified_assignments'] >= policy['min_qualified_assignments_per_family'] for c in cohorts.values())
    for cohort in cohorts.values():
        cohort['observed_qualified_rate'] = cohort['qualified_assignments'] / cohort['matured_assignments'] if cohort['matured_assignments'] else None
    suggested, reason = baseline_share, 'insufficient_verified_qualified_outcomes'
    if sufficient:
        ai, data = cohorts['ai_platform'], cohorts['data_platform']
        delta = Fraction(ai['qualified_assignments'], ai['matured_assignments']) - Fraction(data['qualified_assignments'], data['matured_assignments'])
        reason = 'difference_below_threshold'
        if abs(delta) >= Fraction(str(policy['min_rate_difference'])):
            suggested = round(max(0.0, min(1.0, baseline_share + policy['max_mix_shift'] * (1 if delta > 0 else -1))), 4)
            reason = 'evidence_supports_reviewable_mix_proposal'
    return {'baseline_ai_share': baseline_share, 'current_ai_share': current_share, 'proposed_ai_share': suggested,
            'configuration_changed': False, 'reason': reason, 'cohorts': cohorts,
            'causal_effect_claimed': False, 'policy': policy}


def effective_research_share(store, config, now):
    """Use complete, recent, freshly revalidated qualified evidence for research.

    Every proposal is bounded from the configured baseline. Repeated evidence
    cannot ratchet allocation on successive days. This never authorizes a send.
    """
    if now.tzinfo is None:
        raise ValueError('Research feedback requires an aware date')
    outreach = config.get('outreach', {})
    baseline = outreach.get('research_baseline_ai_share', outreach.get('research_ai_share', 0.60))
    if isinstance(baseline, bool) or not isinstance(baseline, (int, float)) or not 0 <= baseline <= 1:
        raise ValueError('Invalid research baseline')
    policy = _policy(config)
    from .preparation import FACTS
    facts_path = config.get('applications', {}).get('ground_truth_path', FACTS)
    facts_hash = hashlib.sha256(Path(facts_path).read_bytes()).hexdigest()
    candidates = []
    for row in _unique(store.query(kind='funnel_reports')):
        payload = row['payload']
        if not isinstance(payload, dict):
            raise ReportingConflict('reporting_feedback_payload_invalid')
        observed = _stamp(payload.get('observed_at'))
        if observed and timedelta(0) <= now.astimezone(timezone.utc) - observed <= timedelta(days=7):
            candidates.append((observed, row))
    if not candidates:
        return baseline
    _, report = max(candidates, key=lambda pair: (pair[0], pair[1]['id']))
    payload = report['payload']
    allocation = payload.get('allocation', {})
    if (report['status'] != 'evidence_observation' or payload.get('schema') != 'qualified-funnel/v1'
            or payload.get('ground_truth_sha256') != facts_hash
            or allocation.get('policy') != policy or allocation.get('baseline_ai_share') != baseline):
        return baseline
    cursor = store.get(stable_id('reporting_cursor', report['id']))
    if not cursor:
        return baseline
    cursor = validate_record(cursor)
    if (cursor['kind'] != 'sync_cursors' or cursor['status'] != 'readback_verified'
            or cursor['payload'].get('complete') is not True
            or cursor['payload'].get('report_id') != report['id']
            or cursor['payload'].get('source_snapshot_hash') != payload.get('source_snapshot_hash')
            or cursor['payload'].get('observed_at') != payload.get('observed_at')):
        return baseline
    signatures = payload.get('cohort_source_signatures')
    if not isinstance(signatures, list) or len(signatures) > 500:
        return baseline
    sources = {kind: [] for kind in SOURCE_KINDS}
    index = {}
    for signature in signatures:
        if not isinstance(signature, list) or len(signature) != 4:
            return baseline
        kind, identity, expected_hash, status = signature
        source = store.get(identity)
        if not source:
            return baseline
        source = validate_record(source)
        if (kind not in sources or [source['kind'], source['id'], source['hash'], source['status']] != signature
                or identity in index):
            return baseline
        sources[kind].append(source)
        index[identity] = source
    observed = _stamp(payload['observed_at'])
    outcomes, _ = _outcomes(sources, observed)
    attributed, invalid = _assignment_attribution(sources['experiments'], outcomes, index, observed, policy)
    if invalid or content_hash(attributed) != payload.get('cohort_evidence_hash'):
        return baseline
    recomputed = _allocation(attributed, policy, allocation.get('current_ai_share', baseline), baseline)
    if recomputed != allocation:
        return baseline
    return recomputed['proposed_ai_share']


def run_reporting(store, config, now, dry_run=True):
    """Persist source-dated evidence, returning aggregates without personal bodies."""
    if now.tzinfo is None:
        raise ValueError('Reporting requires an aware source timestamp')
    now = now.astimezone(timezone.utc)
    policy = _policy(config)
    current_share = config.get('outreach', {}).get('research_ai_share', 0.60)
    if isinstance(current_share, bool) or not isinstance(current_share, (int, float)) or not 0 <= current_share <= 1:
        raise ValueError('Invalid research allocation')
    sources = {kind: _unique(store.query(kind=kind)) for kind in SOURCE_KINDS}
    for kind, rows in sources.items():
        if any(not isinstance(x['payload'], dict) or x['kind'] != kind for x in rows):
            raise ReportingConflict('reporting_source_kind_or_payload_invalid')
    # Reporting output is not its own statistical input.
    sources['experiments'] = [x for x in sources['experiments'] if x['payload'].get('record_type') == 'assignment']
    source_index = {}
    for rows in sources.values():
        for row in rows:
            if row['id'] in source_index and source_index[row['id']] != row:
                raise ReportingConflict('source_identity_conflict_across_kinds')
            source_index[row['id']] = row
    signatures = sorted((x['kind'], x['id'], x['hash'], x['status']) for x in source_index.values())
    snapshot_hash = content_hash(signatures)
    applications = _latest_applications(sources['applications'])
    receipts, attempted = {}, set()
    for row in sources['applications']:
        identity, payload = _application_id(row), row['payload']
        attempt = payload.get('attempt')
        if isinstance(attempt, dict) and _stamp(attempt.get('started_at')) and _stamp(attempt['started_at']) <= now:
            attempted.add(identity)
        receipt = _confirmed(payload.get('receipt'), now, receipt=True)
        if receipt:
            old = receipts.get(receipt['identity'])
            if old and old != identity:
                raise ReportingConflict('receipt_identity_claimed_by_multiple_applications')
            receipts[receipt['identity']] = identity
    outcomes, ignored = _outcomes(sources, now)
    attributions, invalid = _assignment_attribution(sources['experiments'], outcomes, source_index, now, policy)
    job_ids = {_job_identity(x['payload'], x['id']) for x in sources['jobs']}
    contact_ids = {x['payload'].get('contact_id') or ('notion:' + x['payload']['source_notion_id'].replace('-', '').lower() if x['payload'].get('source_notion_id') else x['id']) for x in sources['contacts']}
    from .preparation import FACTS, evidence_rows
    facts_path = config.get('applications', {}).get('ground_truth_path', FACTS)
    facts = evidence_rows(facts_path)
    min_coverage = config.get('applications', {}).get('min_coverage', 0.7)
    if isinstance(min_coverage, bool) or not isinstance(min_coverage, (int, float)) or not 0 <= min_coverage <= 1:
        raise ValueError('Invalid qualification coverage policy')
    qualified_jobs = {_job_identity(x['payload'], x['id']) for x in sources['jobs'] + sources['applications']
                      if _qualification(x['payload'], facts, now, min_coverage)}
    prepared = {_application_id(x) for x in sources['applications'] if x['status'] in {'prepared', 'draft_ready', 'approved'}}
    qa_ready = {_application_id(x) for x in sources['applications'] if _qa_packet(x['payload'])}
    counts = {'source_jobs': len(job_ids), 'source_contacts': len(contact_ids), 'source_applications': len(applications),
              'source_job_observations': len(sources['jobs']), 'source_contact_observations': len(sources['contacts']),
              'jobs_with_qualification_evidence': len(qualified_jobs), 'prepared_application_observations': len(prepared),
              'qa_ready_packet_evidence_applications': len(qa_ready),
              'applications_attempted': len(attempted), 'verified_submission_receipts': len(receipts),
              'verified_submitted_applications': len(set(receipts.values())),
              'confirmed_recruiter_screens': sum(x['evidence']['outcome'] == 'recruiter_screen_confirmed' for x in outcomes.values()),
              'confirmed_interviews': sum(x['evidence']['outcome'] == 'interview_confirmed' for x in outcomes.values()),
              'confirmed_referrals_offered': sum(x['evidence']['outcome'] == 'referral_offered_confirmed' for x in outcomes.values()),
              'confirmed_referrals_submitted': sum(x['evidence']['outcome'] == 'referral_submitted_confirmed' for x in outcomes.values()),
              'confirmed_useful_replies': sum(x['evidence']['outcome'] == 'useful_reply_confirmed' for x in outcomes.values()),
              'ignored_unverified_evidence': ignored, 'actual_verified_assignments': len(attributions),
              'invalid_or_unverified_assignments': invalid,
              'application_status_observations': dict(sorted(Counter(x['status'] for x in applications.values()).items()))}
    baseline_share = config.get('outreach', {}).get('research_baseline_ai_share', current_share)
    if isinstance(baseline_share, bool) or not isinstance(baseline_share, (int, float)) or not 0 <= baseline_share <= 1:
        raise ValueError('Invalid research baseline')
    allocation = _allocation(attributions, policy, current_share, baseline_share)
    cohort_ids = set()
    for attribution in attributions:
        cohort_ids.update((attribution['assignment_record_id'], attribution['source_event_id']))
        for identity in attribution['qualified_event_ids']:
            cohort_ids.update(outcomes[identity]['source_record_ids'])
    cohort_signatures = sorted([source_index[key]['kind'], key, source_index[key]['hash'], source_index[key]['status']]
                               for key in cohort_ids)
    report_payload = {'schema': 'qualified-funnel/v1', 'observed_at': now.isoformat(), 'source_snapshot_hash': snapshot_hash,
                      'ground_truth_sha256': hashlib.sha256(Path(facts_path).read_bytes()).hexdigest(),
                      'cohort_evidence_hash': content_hash(attributions), 'cohort_source_signatures': cohort_signatures,
                      'actor': 'github_reporting', 'counts': counts, 'allocation': allocation,
                      'confirmed_event_ids': sorted(outcomes), 'source_record_ids': sorted(source_index),
                      'receipt_event_ids': sorted(receipts), 'activity_is_not_outcome': True}
    if dry_run:
        from .orchestrator import ShadowStore
        writer = ShadowStore(store)
    else:
        writer = store
    written = []
    def put(kind, payload, record_id=None, status='observed'):
        record = make_record(kind, payload, record_id=record_id or stable_id(kind, payload), status=status)
        writer.put(record)
        readback = writer.get(record['id'])
        if not readback or any(readback.get(k) != record[k] for k in ('id', 'kind', 'payload', 'status', 'hash')):
            raise ReportingConflict('reporting_readback_uncertain')
        written.append(record['id'])
        return record['id']
    for identity, dimension, arms, hypothesis in HYPOTHESES:
        put('experiments', {'schema': 'qualified-experiment/v1', 'record_type': 'hypothesis', 'hypothesis_id': identity,
                           'dimension': dimension, 'arms': list(arms), 'hypothesis': hypothesis,
                           'evidence_level': 'hypothesis', 'execution': 'human_only', 'not_a_response_rate_claim': True}, identity, 'hypothesis')
    for attribution in attributions:
        put('experiments', {'schema': 'qualified-experiment/v1', 'record_type': 'attribution',
                           'observed_at': now.isoformat(), 'source_snapshot_hash': snapshot_hash, **attribution}, status='evidence_observation')
    report_id = put('funnel_reports', report_payload, status='evidence_observation')
    # Advance only after all preceding records have exactly read back.
    cursor_id = put('sync_cursors', {'schema': 'reporting-source-cursor/v1', 'observed_at': now.isoformat(),
                                   'actor': 'github_reporting', 'source_snapshot_hash': snapshot_hash,
                                   'source_counts': {k: len(v) for k, v in sources.items()},
                                   'report_id': report_id, 'complete': True}, stable_id('reporting_cursor', report_id), status='readback_verified')
    return {'dry_run': dry_run, 'observed_at': now.isoformat(), 'counts': counts, 'allocation': allocation,
            'report_id': report_id, 'sync_cursor_id': cursor_id, 'reporting_records': len(written),
            'external_messages_sent': 0, 'configuration_changed': False}
