#!/usr/bin/env python3
"""Deterministic, offline planner for bounded hiring research.

Version 4 research/verification actions are unchanged. The optional multi-channel
layer turns fresh employer ATS roles (ats_scan.py output) into ordered plays:
apply fast, referral or alumni route, recruiter, proof of work, hiring manager.
Every play step is research, preparation or a gated proposal. Nothing here sends,
submits or authorizes contact; caps and identity/history gates still apply.
"""

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit


CONTACT_STATES = ("uncontacted", "pending", "connected", "suppressed",
                  "delivery_unknown", "ambiguous", "exhausted")
AI_FAMILIES = {"applied_ai", "ai_platform"}


def _day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _profile_key(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower()
        if host not in {"linkedin.com", "www.linkedin.com"}:
            return None
        path = "/" + "/".join(p for p in parts.path.split("/") if p)
        if not path.rstrip("/").lower().startswith("/in/"):
            return None
        segments = [p for p in path.split("/") if p]
        if len(segments) < 2 or segments[0].lower() != "in":
            return None
        normalized_path = "/in/" + "/".join(segments[1:]) + "/"
        return urlunsplit(("https", "www.linkedin.com", normalized_path, "", ""))
    except ValueError:
        return None


def _company_key(campaign):
    return str(campaign.get("company") or "").strip().casefold()


def _role_key(campaign):
    return str(campaign.get("role_key") or campaign.get("role_url") or campaign.get("role_title") or "").strip().casefold()


def _with_evidence(action, reason):
    action["reason"] = reason
    action["status"] = "unresolved_evidence"
    return action


def _discover_actions(policy):
    configured = policy.get("discovery_companies")
    if isinstance(configured, list) and configured:
        rows = []
        for item in configured:
            if isinstance(item, str) and item.strip():
                rows.append((item.strip(), None))
            elif isinstance(item, dict) and isinstance(item.get("company"), str) and item["company"].strip():
                family = item.get("role_family")
                rows.append((item["company"].strip(), family if family in AI_FAMILIES | {"data"} else None))
        if rows:
            return [_with_evidence({"action": "discover_roles", "company": company,
                                    "role_family": family},
                                   "Find and verify current role listings; no contact is selected.")
                    for company, family in rows]
    return [_with_evidence({"action": "discover_roles", "company": None, "role_family": family},
                           "Search for current roles in this family; no contact is selected.")
            for family in ("applied_ai", "data")]


def _contact_unions(campaigns, today):
    """Collapse aliases globally and retain the most restrictive state/oldest touch."""
    priority = {state: i for i, state in enumerate(
        ("uncontacted", "connected", "exhausted", "pending", "ambiguous", "delivery_unknown", "suppressed"))}
    by_key = {}
    for camp in campaigns:
        for contact in camp.get("contacts", []) if isinstance(camp.get("contacts"), list) else []:
            if not isinstance(contact, dict):
                continue
            key = _profile_key(contact.get("profile_url"))
            if not key:
                continue
            state = contact.get("state") if contact.get("state") in CONTACT_STATES else "ambiguous"
            row = by_key.setdefault(key, {"state": state, "touches": [], "companies": set(),
                                          "unverified_touch": False})
            if priority[state] > priority[row["state"]]:
                row["state"] = state
            raw_touch = contact.get("last_touch_at")
            if isinstance(raw_touch, str) and raw_touch.strip():
                touched = _day(raw_touch)
                if touched is None or touched > today:
                    row["unverified_touch"] = True
                else:
                    row["touches"].append(touched)
            company = _company_key(camp)
            if company:
                row["companies"].add(company)
    recent = {key for key, row in by_key.items()
              if row["unverified_touch"] or
              any(0 <= (today - touched).days <= 7 for touched in row["touches"])}
    return by_key, recent


def _research_plan(snapshot, policy, today):
    """Return only bounded research/verification actions; never authorizes contact."""
    today = _day(today)
    if today is None:
        raise ValueError("today must be YYYY-MM-DD")
    campaigns = snapshot.get("campaigns", []) if isinstance(snapshot, dict) else []
    campaigns = [c for c in campaigns if isinstance(c, dict)] if isinstance(campaigns, list) else []
    policy = policy if isinstance(policy, dict) else {}
    limit = max(0, int(policy.get("research_batch_size", 0)))
    age_limit = max(0, int(policy.get("role_cache_days", 0)))
    coverage_target = max(0, int(policy.get("company_contact_coverage_target", 0)))

    if not campaigns:
        return {"as_of": today.isoformat(), "actions": _discover_actions(policy)[:limit],
                "limits": {"research_batch_size": limit,
                           "max_new_requests_per_run": max(0, int(policy.get("max_new_requests_per_run", 0)))},
                "sending_authorized": False,
                "note": "Research plan only; role freshness does not replace live verification."}

    unions, recent_profiles = _contact_unions(campaigns, today)
    companies_touched = {}
    for profile, row in unions.items():
        if profile in recent_profiles:
            for company in row["companies"]:
                companies_touched.setdefault(company, set()).add(profile)

    ranked = []
    due_campaign_seen = False
    for c in campaigns:
        if _day(c.get("next_review_at")) and _day(c.get("next_review_at")) > today:
            continue
        due_campaign_seen = True
        company = _company_key(c)
        family = c.get("role_family")
        fit = c.get("fit")
        status = c.get("role_status")
        if status == "closed" or fit == "unsupported" or c.get("mandatory_blocker") is True:
            continue
        verified = _day(c.get("role_verified_at"))
        stale = verified is None or verified > today or (today - verified).days > age_limit
        required_role_data = (bool(company) and bool(c.get("role_key") or c.get("role_url")) and
                              family in AI_FAMILIES | {"data"} and fit in {"direct", "credible_stretch"} and
                              type(c.get("mandatory_blocker")) is bool)
        actions = []
        if status not in {"open", "closed"} or stale:
            actions.append(_with_evidence({"action": "refresh_role", "company": c.get("company"),
                                           "role_key": c.get("role_key")},
                                          "Confirm the role's current status and refresh its dated source evidence."))
        elif not required_role_data:
            actions.append(_with_evidence({"action": "research/qualify_role", "company": c.get("company"),
                                           "role_key": c.get("role_key")},
                                          "Resolve missing company, role identity, role family, fit, or mandatory-blocker evidence."))
        allowed_role = (not actions and required_role_data and status == "open" and
                        fit in {"direct", "credible_stretch"} and c.get("mandatory_blocker") is False)
        if allowed_role:
            candidates = []
            cap_blocked = False
            raw_contacts = c.get("contacts", []) if isinstance(c.get("contacts"), list) else []
            for contact in raw_contacts:
                if not isinstance(contact, dict) or contact.get("state") != "uncontacted":
                    continue
                profile = _profile_key(contact.get("profile_url"))
                if not profile or unions.get(profile, {}).get("state") != "uncontacted" or profile in recent_profiles:
                    continue
                if contact.get("kind") not in {"recruiter", "manager"}:
                    continue
                cap = max(0, int(policy.get("max_new_contacts_per_company_per_7_days", 0)))
                if len(companies_touched.get(company, set())) >= cap:
                    cap_blocked = True
                    continue
                candidates.append((contact.get("kind") != "manager", profile, contact))
            candidates.sort(key=lambda x: (x[0], x[1]))
            coverage = len({p for p, row in unions.items() if company in row["companies"]})
            if candidates:
                contact = candidates[0][2]
                actions.append(_with_evidence({"action": "verify_contact", "company": c.get("company"),
                                               "role_key": c.get("role_key"),
                                               "profile_url": _profile_key(contact["profile_url"])},
                                              "Verify this exact profile and complete contact history before any draft."))
            elif cap_blocked or coverage < coverage_target or not any(
                    isinstance(x, dict) and x.get("kind") in {"recruiter", "manager"} for x in raw_contacts):
                actions.append(_with_evidence({"action": "research_recruiter_or_manager", "company": c.get("company"),
                                               "role_key": c.get("role_key")},
                                              "Find a suitable recruiter or manager; current profiles are absent or ineligible."))
            elif (any(isinstance(x, dict) and x.get("kind") in {"recruiter", "manager"}
                      for x in raw_contacts) and
                  all(not isinstance(x, dict) or x.get("kind") not in {"recruiter", "manager"} or
                      (x.get("state") in CONTACT_STATES and x.get("state") != "uncontacted")
                      for x in raw_contacts)):
                actions.append(_with_evidence({"action": "review_route", "company": c.get("company"),
                                               "role_key": c.get("role_key")},
                                              "Known recruiter and manager contacts are exhausted or blocked; identify another route."))

        # A campaign contributes at most one primary action. Refresh takes precedence over qualification.
        if actions:
            action = actions[0]
            score = (3 if family in AI_FAMILIES else 0) + (2 if fit == "direct" else 1 if fit == "credible_stretch" else 0)
            ranked.append((-score, company, _role_key(c), action["action"], family in AI_FAMILIES, action))

    if not ranked and due_campaign_seen:
        ranked = [(0, "", "", action["action"], action.get("role_family") in AI_FAMILIES, action)
                  for action in _discover_actions(policy)]

    action_order = {"refresh_role": 0, "research/qualify_role": 1, "verify_contact": 2,
                    "research_recruiter_or_manager": 3, "review_route": 4, "discover_roles": 5}
    ranked.sort(key=lambda item: (item[0], item[1], item[2], action_order.get(item[3], 9), item[3]))
    # Collapse duplicate role rows and exact profiles before lane quotas.
    unique_ranked = []
    seen_roles = set()
    seen_candidate_profiles = set()
    for item in ranked:
        action = item[5]
        company_role = (str(action.get("company") or "").strip().casefold(), item[2])
        if item[2] and company_role in seen_roles:
            continue
        if item[2]:
            seen_roles.add(company_role)
        profile = action.get("profile_url") if action.get("action") == "verify_contact" else None
        if profile and profile in seen_candidate_profiles:
            continue
        if profile:
            seen_candidate_profiles.add(profile)
        unique_ranked.append(item)
    ranked = unique_ranked
    share = min(1.0, max(0.0, float(policy.get("role_mix_ai_share", 0.0))))
    ai_items = [item for item in ranked if item[4]]
    data_items = [item for item in ranked if not item[4]]
    if ai_items and data_items and limit:
        ai_target = int(limit * share + 0.999999)
        selected = ai_items[:ai_target] + data_items[:max(0, limit - ai_target)]
        selected_ids = {id(item) for item in selected}
        selected.extend(item for item in ranked if id(item) not in selected_ids and len(selected) < limit)
        selected.sort(key=lambda item: item[:4])
    else:
        selected = ranked[:limit]
    result = []
    seen_profiles = set()
    for item in selected:
        action = item[5]
        profile = action.get("profile_url") if action.get("action") == "verify_contact" else None
        if profile and profile in seen_profiles:
            continue
        if profile:
            seen_profiles.add(profile)
        result.append(action)
    return {"as_of": today.isoformat(), "actions": result,
            "limits": {"research_batch_size": limit,
                       "max_new_requests_per_run": max(0, int(policy.get("max_new_requests_per_run", 0)))},
            "sending_authorized": False,
            "note": "Research plan only; role freshness does not replace live verification."}


# ---------------------------------------------------------------------------
# Multi-channel plays for fresh roles (additive; research/preparation only)
# ---------------------------------------------------------------------------

WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
ROLE_EXCLUSION_FLAGS = {"no_sponsorship_language", "citizenship_required", "security_clearance",
                        "us_person_export_control"}
QUALIFY_FIRST_FLAGS = {"staff_or_principal_title", "experience_8plus_years", "sponsorship_needs_recheck"}

DEFAULT_CHANNELS = {
    "fresh_apply": {"enabled": True, "counts_toward_contact_caps": False, "requires_naren": True,
                    "authorization": "naren_approves_and_submits",
                    "action": "prepare_application_packet"},
    "referral_first_degree": {"enabled": True, "counts_toward_contact_caps": True, "requires_naren": True,
                              "authorization": "naren_approval_per_message",
                              "action": "find_and_verify_referral_route", "fallback": "alumni"},
    "alumni": {"enabled": True, "counts_toward_contact_caps": True, "requires_naren": False,
               "authorization": "standing_v4_first_touch_routing_question_only",
               "action": "find_and_verify_alumni_route"},
    "recruiter_linkedin": {"enabled": True, "counts_toward_contact_caps": True, "requires_naren": False,
                           "authorization": "standing_v4_first_touch",
                           "action": "find_and_verify_requisition_recruiter"},
    "proof_of_work": {"enabled": True, "counts_toward_contact_caps": False, "requires_naren": True,
                      "authorization": "naren_reviews_artifact_before_it_is_shared",
                      "action": "prepare_proof_of_work"},
    "hiring_manager": {"enabled": True, "counts_toward_contact_caps": True, "requires_naren": False,
                       "authorization": "standing_v4_first_touch",
                       "action": "find_and_verify_hiring_manager"},
    "community": {"enabled": True, "counts_toward_contact_caps": False, "requires_naren": True,
                  "authorization": "research_only; any public post or DM needs naren_approval",
                  "action": "check_community_channels"},
    "public_email": {"enabled": True, "counts_toward_contact_caps": True, "requires_naren": True,
                     "authorization": "naren_approval_per_message; publicly listed address only, never guessed",
                     "action": "consider_public_recruiting_inbox"},
}
DEFAULT_PLAY = (
    {"channel": "fresh_apply", "day_offset": 0},
    {"channel": "referral_first_degree", "day_offset": 0},
    {"channel": "recruiter_linkedin", "day_offset": 1},
    {"channel": "proof_of_work", "day_offset": 1},
    {"channel": "hiring_manager", "day_offset": 2},
    {"channel": "community", "day_offset": 0, "optional": True},
    {"channel": "public_email", "day_offset": 3, "optional": True, "only_if": "public_email_listed"},
)
PLAY_GATES = (
    "Planner output is not send or submit authorization; sending_authorized stays false.",
    "Before any draft: exact live identity, complete conversation/invitation history, all-agent/manual "
    "touches and suppression (QUEUE_PROTOCOL.md draft gate).",
    "Recount new requests across all lanes and companies before using any slot; caps are internal pacing.",
    "New outbound Monday-Thursday only; never message pending invitations or bypass a refusal via another channel.",
    "First touch at most 280 characters, verified evidence only, no em dashes, no sponsorship or location pitch.",
    "Applications, referral asks to existing connections, proof-of-work sharing, community posts and email "
    "need Naren's approval.",
    "Recheck the official posting before role-specific outreach; scanner fit score is not qualification.",
)


def _channel_config(policy):
    configured = policy.get("channels") if isinstance(policy.get("channels"), dict) else {}
    merged = {}
    for name, defaults in DEFAULT_CHANNELS.items():
        override = configured.get(name) if isinstance(configured.get(name), dict) else {}
        row = dict(defaults)
        row.update({k: v for k, v in override.items() if k in {"enabled", "fallback", "window_hours", "note",
                                                                 "max_prep_hours"}})
        # Safety-relevant fields can be tightened by policy but never loosened.
        if override.get("counts_toward_contact_caps") is True:
            row["counts_toward_contact_caps"] = True
        if override.get("requires_naren") is True:
            row["requires_naren"] = True
        merged[name] = row
    return merged


def _play_template(policy):
    template = policy.get("multichannel_play")
    if not isinstance(template, list) or not template:
        return list(DEFAULT_PLAY)
    rows = []
    for item in template:
        if isinstance(item, dict) and item.get("channel") in DEFAULT_CHANNELS:
            rows.append({"channel": item["channel"], "day_offset": max(0, int(item.get("day_offset", 0))),
                         "optional": bool(item.get("optional", False)), "only_if": item.get("only_if")})
    return rows or list(DEFAULT_PLAY)


def _int_policy(policy, key, default):
    try:
        return max(0, int(policy.get(key, default)))
    except (TypeError, ValueError):
        return default


def _fresh_role_rows(scan, snapshot):
    rows = []
    for source in (scan, snapshot):
        if not isinstance(source, dict):
            continue
        key = "roles" if source is scan else "fresh_roles"
        for role in source.get(key) or []:
            if isinstance(role, dict) and isinstance(role.get("employer"), str) and role.get("title"):
                rows.append(role)
    return rows


def _next_outreach_day(start, allowed, used, cap, lookahead):
    for offset in range(lookahead + 1):
        day = start + timedelta(days=offset)
        if WEEKDAY_CODES[day.weekday()] in allowed and used.get(day, 0) < cap:
            return day
    return None


def _company_contact_summary(campaigns, company):
    summary = {}
    for camp in campaigns:
        if _company_key(camp) != company:
            continue
        for contact in camp.get("contacts", []) if isinstance(camp.get("contacts"), list) else []:
            if isinstance(contact, dict):
                state = contact.get("state") if contact.get("state") in CONTACT_STATES else "ambiguous"
                summary[state] = summary.get(state, 0) + 1
    return summary


def _step_note(channel, role, policy, apply_by, today):
    company = role["employer"]
    alumni = policy.get("alumni_routes") if isinstance(policy.get("alumni_routes"), dict) else {}
    schools = ", ".join(alumni.get("schools") or ["Missouri S&T"])
    employers = ", ".join(alumni.get("prior_employers") or ["C2FO", "Udaan", "Zomato"])
    req = role.get("requisition_id") or role.get("job_id") or role.get("role_key")
    if channel == "fresh_apply":
        timing = (f"apply by {apply_by.isoformat()}" if apply_by and apply_by >= today
                  else "48h window passed or unknown; apply promptly only if the posting is still live")
        return (f"Confirm the official posting is live, tailor the resume from verified evidence only, {timing}. "
                "Naren submits; record the application as an event.")
    if channel == "referral_first_degree":
        return (f"Look for 1st-degree connections at {company} (company page 'People you can reach out to', "
                f"connections filter). If none, use alumni: {schools} or ex-{employers} now at {company}. "
                "A referral ask to an existing connection needs Naren's approval; alumni first touch asks a routing question only.")
    if channel == "recruiter_linkedin":
        return (f"Find the recruiter who owns requisition {req} (posting hiring-team field, company recruiting "
                "team page, recent recruiter posts about this team). Reference the requisition in a 280-character note.")
    if channel == "proof_of_work":
        return ("Build one small artifact tied to this team's stated problem (teardown, SQL/data-quality check, "
                "tiny demo). Label independent work as independent; Naren reviews before it is linked.")
    if channel == "hiring_manager":
        return ("Identify a verified or clearly labeled likely manager from public sources (posting hiring team, "
                "engineering blog, conference talk, GitHub org, team page). Note links the proof-of-work artifact.")
    if channel == "community":
        return (f"Check community job channels and public threads for {company} or this team (see MULTICHANNEL_STRATEGY.md). "
                "Research only; posting or DMs need Naren's approval.")
    if channel == "public_email":
        emails = ", ".join(role.get("public_recruiting_emails") or [])
        return (f"Posting lists a recruiting inbox ({emails}). Use only if the LinkedIn routes are blocked, never in "
                "parallel with them, and only with Naren's approval. Never guess or permute addresses.")
    return ""


def _play_eligibility(role, policy, today, campaigns):
    max_age = _int_policy(policy, "fresh_role_max_age_days", 3)
    flags = set(role.get("flags") or [])
    if role.get("exclude_reason") or flags & ROLE_EXCLUSION_FLAGS:
        return None, "explicit role-level exclusion in posting text; skip this requisition only"
    posted = _day(role.get("posted_at"))
    freshness = role.get("freshness") or ("posted" if posted else None)
    if freshness != "posted" and not policy.get("plays_include_updated_only", False):
        return None, "update-only listing, not a new posting"
    anchor = posted if freshness == "posted" else (_day(role.get("updated_at")) or posted)
    if anchor is None or anchor > today or (today - anchor).days > max_age:
        return None, f"not posted within {max_age} days"
    url = str(role.get("url") or "").strip().casefold()
    key = str(role.get("role_key") or "").strip().casefold()
    company = role["employer"].strip().casefold()
    matched = None
    for camp in campaigns:
        same_role = (url and str(camp.get("role_url") or "").strip().casefold() == url) or \
                    (key and _role_key(camp) == key)
        if same_role:
            matched = camp
            if camp.get("role_status") == "closed" or camp.get("fit") == "unsupported" or \
                    camp.get("mandatory_blocker") is True:
                return None, "snapshot marks this role closed, unsupported or blocked"
            review = _day(camp.get("next_review_at"))
            if review and review > today:
                return None, f"snapshot campaign waiting until {review.isoformat()}"
    return {"matched_campaign": matched, "company": company,
            "qualify_first": bool(flags & QUALIFY_FIRST_FLAGS)}, None


def build_plays(snapshot, policy, today, scan=None):
    """Ordered multi-touch plays for fresh roles. Research/preparation proposals only."""
    today = _day(today) if not isinstance(today, date) else today
    if today is None:
        raise ValueError("today must be YYYY-MM-DD")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    campaigns = [c for c in snapshot.get("campaigns", []) if isinstance(c, dict)] \
        if isinstance(snapshot.get("campaigns"), list) else []
    cooldowns = snapshot.get("company_cooldowns") if isinstance(snapshot.get("company_cooldowns"), dict) else {}
    channels = _channel_config(policy)
    template = _play_template(policy)
    company_cap = _int_policy(policy, "max_new_contacts_per_company_per_7_days", 3)
    daily_cap = _int_policy(policy, "max_new_requests_per_day", _int_policy(policy, "max_new_requests_per_run", 6))
    max_plays = _int_policy(policy, "max_plays_per_run", 5)
    lookahead = _int_policy(policy, "play_lookahead_days", 14)
    window_hours = channels["fresh_apply"].get("window_hours") or _int_policy(policy, "fresh_apply_window_hours", 48)
    allowed_days = set(policy.get("new_outreach_days") or ("MO", "TU", "WE", "TH"))
    pacing = snapshot.get("pacing") if isinstance(snapshot.get("pacing"), dict) else {}
    used = {}
    if isinstance(pacing.get("new_requests_today"), int):
        used[today] = max(0, pacing["new_requests_today"])

    unions, recent = _contact_unions(campaigns, today)
    touched = {}
    for profile, row in unions.items():
        if profile in recent:
            for company in row["companies"]:
                touched.setdefault(company, set()).add(profile)
    slots = {}

    candidates, skipped, seen = [], [], set()
    for role in _fresh_role_rows(scan, snapshot):
        ident = (role["employer"].strip().casefold(),
                 str(role.get("role_key") or role.get("url") or role.get("title")).strip().casefold())
        if ident in seen:
            continue
        seen.add(ident)
        info, reason = _play_eligibility(role, policy, today, campaigns)
        company = role["employer"].strip().casefold()
        until = _day(cooldowns.get(role["employer"])) or _day(cooldowns.get(company))
        if info and until and until > today:
            info, reason = None, f"company review cooldown until {until.isoformat()}"
        if info is None:
            skipped.append({"company": role["employer"], "role_key": role.get("role_key"),
                            "role_title": role.get("title"), "reason": reason})
            continue
        candidates.append((role, info))
    candidates.sort(key=lambda pair: (pair[1]["qualify_first"], -int(pair[0].get("fit_score") or 0),
                                      float(pair[0].get("age_days") or 0), pair[1]["company"],
                                      str(pair[0].get("role_key") or "")))
    not_selected = max(0, len(candidates) - max_plays)
    plays = []
    for role, info in candidates[:max_plays]:
        company = info["company"]
        slots.setdefault(company, max(0, company_cap - len(touched.get(company, set()))))
        posted = _day(role.get("posted_at"))
        apply_by = (posted + timedelta(days=max(1, (int(window_hours) + 23) // 24))) if posted else None
        steps, prev_contact = [], None
        for item in template:
            name = item["channel"]
            config = channels[name]
            if not config.get("enabled", True):
                continue
            if item.get("only_if") == "public_email_listed" and not role.get("public_recruiting_emails"):
                continue
            target = today + timedelta(days=item["day_offset"])
            step = {"order": len(steps) + 1, "channel": name, "action": config["action"],
                    "counts_toward_contact_caps": bool(config["counts_toward_contact_caps"]),
                    "requires_naren": bool(config["requires_naren"]), "authorization": config["authorization"],
                    "optional": bool(item.get("optional")), "status": "proposed", "target_date": target.isoformat(),
                    "note": _step_note(name, role, policy, apply_by, today)}
            if name == "referral_first_degree" and config.get("fallback") == "alumni" and channels["alumni"]["enabled"]:
                step["fallback_channel"] = "alumni"
                step["fallback_authorization"] = channels["alumni"]["authorization"]
            if name == "fresh_apply" and apply_by:
                step["apply_by"] = apply_by.isoformat()
            if info["qualify_first"] and name != "fresh_apply":
                step["status"] = "held_qualification"
                step["target_date"] = None
            elif info["qualify_first"] and name == "fresh_apply":
                step["status"] = "qualify_first"
                step["note"] = ("Seniority or sponsorship signal needs qualification first (staff/principal, 8+ "
                                "years or recheck flag). Decide credible stretch versus skip before any other step. "
                                + step["note"])
            elif config["counts_toward_contact_caps"]:
                if slots[company] <= 0:
                    step["status"] = "held_company_cap"
                    step["target_date"] = None
                    step["note"] += " Company has no new-contact slot in the rolling 7 days; reuse known routes."
                else:
                    start = max(target, prev_contact + timedelta(days=1)) if prev_contact else target
                    day = _next_outreach_day(start, allowed_days, used, daily_cap, lookahead)
                    if day is None:
                        step["status"] = "deferred_daily_cap"
                        step["target_date"] = None
                    else:
                        used[day] = used.get(day, 0) + 1
                        slots[company] -= 1
                        prev_contact = day
                        step["target_date"] = day.isoformat()
            steps.append(step)
        fit = role.get("fit") if isinstance(role.get("fit"), dict) else {}
        matched = info["matched_campaign"]
        plays.append({
            "company": role["employer"], "role_key": role.get("role_key"), "role_title": role.get("title"),
            "role_url": role.get("url"), "posted_at": role.get("posted_at"), "fit_score": role.get("fit_score"),
            "planner_role_family": role.get("planner_role_family"),
            "status": "qualify_first" if info["qualify_first"] else "proposed",
            "why": {"production_evidence": list(fit.get("production_evidence") or [])[:5],
                    "independent_evidence": list(fit.get("independent_evidence") or [])[:3],
                    "gaps": list(fit.get("gaps") or [])[:5],
                    "flags": [f for f in role.get("flags") or [] if f != "comp_not_listed"]},
            "company_contacts_known": _company_contact_summary(campaigns, company),
            "company_new_contact_slots_left": slots[company],
            "existing_campaign": (matched or {}).get("role_key"),
            "campaign_seed": None if matched else {
                "company": role["employer"], "role_key": role.get("role_key"), "role_title": role.get("title"),
                "role_url": role.get("url"), "role_verified_at": str(role.get("first_seen") or today.isoformat())[:10],
                "role_status": "open", "role_family": role.get("planner_role_family"), "fit": None,
                "mandatory_blocker": None, "contacts": [], "next_review_at": today.isoformat(),
                "source_note": (f"Employer ATS feed ({role.get('ats')}/{role.get('board')}) read by ats_scan.py; "
                                f"heuristic fit score {role.get('fit_score')}; fit and blockers not yet qualified.")},
            "steps": steps,
        })
    return plays, skipped, not_selected


def plan(snapshot, policy, today, scan=None):
    """Version 4 research plan plus optional multi-channel plays; never authorizes contact."""
    result = _research_plan(snapshot, policy, today)
    plays, skipped, not_selected = build_plays(snapshot, policy, result["as_of"], scan)
    result["plays"] = plays
    if plays:
        result["play_gates"] = list(PLAY_GATES)
    if skipped:
        counts = {}
        for row in skipped:
            counts[row["reason"]] = counts.get(row["reason"], 0) + 1
        result["plays_skipped_counts"] = counts
        bulk = ("update-only listing", "not posted within")
        detail = [row for row in skipped if not row["reason"].startswith(bulk)]
        if detail:
            result["plays_skipped"] = detail
    if not_selected:
        result["plays_not_selected"] = not_selected
    result["sending_authorized"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--today", required=True)
    parser.add_argument("--scan", type=Path, help="optional ats_scan.py report; adds multi-channel plays")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(args.input.read_text(encoding="utf-8"))
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        scan = json.loads(args.scan.read_text(encoding="utf-8")) if args.scan else None
        print(json.dumps(plan(snapshot, policy, args.today, scan), sort_keys=True, separators=(",", ":")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
