"""Read-only public role verification and truthful relationship research plans."""
from math import ceil

from scripts.jobscout_feed import candidates, fetch_feed, feed_metadata
from cloud.applications import _employer, fold_applications, matching_application
from cloud.records import make_record, normalized_identity, stable_id
from cloud.submission import SubmissionBlocked, adapter_for


def choose_mix(leads, limit, ai_share=0.6):
    ai, data = [], []
    for lead in leads:
        title = str(lead.get('title', '')).casefold()
        (ai if any(x in title for x in ('ai ', ' ai', 'machine learning', 'llm', 'agent', 'ml ')) else data).append(lead)
    split = ceil(limit * ai_share)
    chosen = ai[:split] + data[:limit - split]
    chosen.extend([row for row in leads if row not in chosen][:limit - len(chosen)])
    return chosen


def run_discovery(store, config, now, feed=None, transport=None):
    settings = config.get('applications', {})
    result = {'roles_checked': 0, 'official_open': 0, 'research_tasks': 0, 'duplicates': 0, 'exceptions': {}}
    def error(reason):
        result['exceptions'][reason] = result['exceptions'].get(reason, 0) + 1
    try:
        data = feed if feed is not None else fetch_feed(settings.get('feed_url'))
        metadata = feed_metadata(data, now, settings.get('max_feed_age_hours', 24))
    except (OSError, ValueError):
        error('feed_unavailable')
        return result
    result['feed_exported_at'] = metadata['exported_at']
    if metadata['stale']:
        error('feed_stale_or_timestamp_unknown')
        return result
    applications = fold_applications(store.query(kind='applications'))
    contacts = store.query(kind='contacts')
    leads = candidates(data, settings.get('min_score', 0.51), settings.get('max_age_days', 7), now)
    selected = choose_mix(leads, settings.get('max_candidates', 5), config.get('outreach', {}).get('research_ai_share', 0.6))
    for lead in selected:
        # This is the central cloud dedup check before any role preparation.
        prior = matching_application(applications, lead['company'], lead.get('requisition_id'), [lead['url']], lead.get('title'))
        if prior:
            result['duplicates'] += 1
        result['roles_checked'] += 1
        try:
            employer = _employer(lead, settings)
            job = adapter_for(employer, transport).verify(lead)
        except SubmissionBlocked as exc:
            error(str(exc))
            continue
        except (OSError, ValueError):
            error('official_role_verification_failed')
            continue
        if not job.get('open'):
            error('official_role_closed')
            continue
        result['official_open'] += 1
        company_id = 'company:' + normalized_identity(job['company'])
        job_id = stable_id('job', normalized_identity(job['company']), str(job['requisition_id']))
        if not store.get(company_id):
            store.put(make_record('companies', {'company_id': company_id, 'name': job['company']}, record_id=company_id))
        payload = {**job, 'company_id': company_id, 'job_id': job_id, 'feed_exported_at': metadata['exported_at'],
                   'prior_application_id': prior.get('payload', {}).get('application_id') if prior else None}
        store.put(make_record('jobs', payload, record_id=stable_id('job_observation', job_id, payload), status='open_observed'))
        known = [row['id'] for row in contacts if row.get('payload', {}).get('company_id') == company_id or normalized_identity(row.get('payload', {}).get('company', '')) == normalized_identity(job['company'])]
        plan = {'company_id': company_id, 'job_id': job_id, 'source_url': job['evidence_url'], 'observed_at': now.isoformat(),
                'known_contact_ids': known, 'verified_hiring_owner': None,
                'next_action': 'verify_existing_routes' if known else 'research_official_hiring_team_or_public_professional_route',
                'constraints': ['no_invented_owner', 'exact_identity_and_history_before_draft', 'respect_opt_out_scope', 'no_email_guessing'],
                'application_context': 'already_in_history' if prior else 'fresh_verified_role', 'hypothesis': 'role_specific_route_to_qualified_conversation'}
        store.put(make_record('conversation_observations', plan, record_id=stable_id('role_route_plan', job_id, plan), status='research'))
        result['research_tasks'] += 1
    return result


def funnel(store):
    applications = fold_applications(store.query(kind='applications'))
    return {'applications_verified': sum(x['status'] == 'applied_verified' for x in applications.values()),
            'applications_unknown': sum(x['status'] in ('submission_unknown', 'submitting') for x in applications.values()),
            'interview_stage_applications': sum(x['status'] == 'interview' for x in applications.values()),
            'interviews_with_evidence': sum(bool(x.get('payload', {}).get('confirmation_evidence')) for x in store.query(kind='interviews')),
            'referrals_with_evidence': sum(bool(x.get('payload', {}).get('confirmation_evidence')) for x in store.query(kind='referrals'))}
