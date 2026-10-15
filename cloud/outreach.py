"""Conservative relationship planning. This module has no send capability."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from cloud.records import content_hash


def stamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def record(identity, kind, payload, status='research'):
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'id': identity, 'kind': kind, 'payload': payload, 'status': status, 'hash': digest}


def gate(contact, now, lane, company_count=None):
    """An eligible draft requires documented exact history, even when not sent."""
    if contact.get('opt_out') or contact.get('company_opt_out') or contact.get('suppressed'):
        return 'suppressed'
    if contact.get('state') in ('suppressed', 'pending', 'ambiguous', 'delivery_unknown'):
        return contact['state']
    if not contact.get('identity_verified') or not contact.get('history_complete'):
        return 'needs_verification'
    verified = stamp(contact.get('verified_at'))
    if not verified or now - verified > timedelta(hours=24) or verified > now:
        return 'needs_verification'
    if contact.get('delivery_unknown') or contact.get('submission_unknown'):
        return 'delivery_unknown'
    if lane == 'accepted':
        if contact.get('state') != 'connected':
            return 'not_connected'
        count = contact.get('unanswered_total')
        if count is None:
            return 'history_unknown'
        if count >= 2 and not contact.get('genuine_reply'):
            return 'unanswered_limit'
        if now.weekday() >= 4 and not contact.get('genuine_reply'):
            return 'research_only_day'
    else:
        if contact.get('state') != 'uncontacted':
            return 'already_contacted'
        if now.weekday() >= 4:
            return 'research_only_day'
        if company_count is None:
            return 'account_counts_unknown'
        if company_count >= 3:
            return 'company_cap'
    last = stamp(contact.get('last_touch_at'))
    if last and now - last < timedelta(hours=24):
        return 'minimum_gap'
    return None


def verified_proof(config):
    proof = config.get('proof_point', {})
    text = proof.get('text', '')
    if not text or any(word in text for word in ('[VERIFY]', '[BLOCKED]')):
        return None
    try:
        source = Path(proof['source']).read_text()
    except (KeyError, OSError):
        return None
    # Exact source substring and its cited evidence row must be available.
    rows = [line for line in source.splitlines() if proof.get('fact_id', '') in line and text in line]
    if len(rows) != 1 or '[VERIFY]' in rows[0] or '[BLOCKED]' in rows[0]:
        return None
    return text


def select_contacts(contacts, observations, lane, limit):
    """Prioritize genuine replies, then rotate toward least recently reviewed."""
    last_reviewed = {}
    for item in observations:
        payload = item.get('payload', {})
        when = stamp(payload.get('observed_at'))
        identity = payload.get('contact_id')
        if identity and when and payload.get('lane') == lane:
            last_reviewed[identity] = max(when, last_reviewed.get(identity, when))
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(contacts, key=lambda row: (not row.get('payload', {}).get('genuine_reply', False), last_reviewed.get(row['id'], oldest), row['id']))[:limit]


def run_outreach(store, config, lane, now, dry_run=True):
    from cloud.coordination_quarantine import ContactQuarantines
    quarantines = ContactQuarantines(store.query(kind='exceptions'))
    contacts = store.query(kind='contacts')
    observations = store.current_contact_observations() if hasattr(store, 'current_contact_observations') else []
    source_key = lambda value: str(value or '').replace('-', '').lower()
    fresh = {source_key(x['source_notion_id']): x for x in observations}
    events = store.query(kind='outreach_events')
    previous_plans = store.query(kind='conversation_observations')
    counts = {}
    known_counts = config.get('account_history_reconciled', False)
    for event in events:
        payload = event.get('payload', {})
        when = stamp(payload.get('occurred_at'))
        if when and timedelta(0) <= now - when < timedelta(days=7) and payload.get('action') == 'new_invitation' and payload.get('delivery_state') == 'Sent verified':
            counts.setdefault(payload.get('company_id'), set()).add(payload.get('contact_id'))
    proof = verified_proof(config)
    result = {'lane': lane, 'inbox': 'not checked', 'contacts_available': len(contacts), 'contacts_reviewed': 0, 'drafts': 0, 'research': 0, 'sent': 0, 'blocked': {}}
    for item in select_contacts(contacts, previous_plans, lane, config.get('max_contacts_per_run', 30)):
        result['contacts_reviewed'] += 1
        contact = dict(item.get('payload', {}))
        source = fresh.get(source_key(contact.get('source_notion_id')))
        if source:
            # New human suppression takes effect without editing historical envelopes.
            contact['opt_out'] = contact.get('opt_out') or source.get('no_contact') or source.get('suppressed')
            contact['source_observed_at'] = source['observed_at']
            verified_source_hash = contact.get('verified_source_hash')
            observed_hash = content_hash({'properties': source.get('properties', {}), 'body': source.get('body', [])})
            if verified_source_hash != observed_hash:
                contact['identity_verified'] = False
                contact['history_complete'] = False
        company_id = contact.get('company_id')
        hold = quarantines.hold_for([item])
        blocked = gate(contact, now, lane, len(counts.get(company_id, set())) if known_counts else None)
        if hold and blocked not in {'suppressed', 'pending', 'ambiguous', 'delivery_unknown'}:
            blocked = hold['reason']
        if not blocked and contact.get('source_notion_id') and not source:
            blocked = 'fresh_source_unavailable'
        blocked = blocked or (None if proof else 'verified_proof_missing')
        context = contact.get('reply_context') if lane == 'accepted' else contact.get('routing_context')
        if not blocked and not context:
            blocked = 'specific_context_missing'
        payload = {'contact_id': item['id'], 'company_id': company_id, 'lane': lane,
                   'observed_at': now.isoformat(), 'inbox': 'not checked', 'blocker': blocked,
                   'action': 'verify_exact_identity_and_history' if blocked else 'human_execute_draft'}
        if hold:
            payload['coordination_quarantine'] = hold
        if not blocked:
            name = contact.get('name', '').split(' ')[0]
            if lane == 'first_touch':
                draft = f"Hi {name}, I’m a Data Engineer at ExponentHR. {proof}. {context}"
                if len(draft) > min(280, contact.get('composer_limit', 280)) or '—' in draft:
                    blocked = payload['blocker'] = 'draft_composer_limit'
                else:
                    payload['draft'] = draft
            else:
                # Never compose a representational reply from an unobserved inbox.
                draft = contact.get('verified_reply_draft')
                if not draft or contact.get('reply_draft_context_hash') != sha256(context.encode()).hexdigest():
                    blocked = payload['blocker'] = 'reply_context_requires_review'
                else:
                    payload['draft'] = draft
        if blocked:
            result['research'] += 1
            result['blocked'][blocked] = result['blocked'].get(blocked, 0) + 1
        else:
            result['drafts'] += 1
        # Observations/drafts are immutable, source-dated, never a delivery claim.
        identity = 'plan:' + sha256(f"{now.date()}:{lane}:{item['id']}".encode()).hexdigest()
        store.put(record(identity, 'conversation_observations', payload, 'needs_verification' if blocked else 'draft'))
    return result
