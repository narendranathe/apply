#!/usr/bin/env python3
"""Pull the JobScout feed and print Gate-ready candidates.

JobScout (narendranathe/job-scout) scrapes 130+ company career pages across
6 ATS APIs and exports frontend/public/api-data.json ~9x/day. This script
fetches that public export, filters for fresh + relevant roles, and prints
them in a form the daily pipeline cron can Gate and claim.

Usage:
    python3 scripts/jobscout_feed.py [--min-score 0.55] [--max-age-days 3] [--json]

Filters (defaults):
    - is_active jobs only
    - posted within --max-age-days (posted_at; falls back to first_seen_at)
    - relevance_score >= --min-score
Sorted by relevance_score descending.

Notes:
    - first_seen_at is currently unreliable (all jobs show <=1d, likely a
      state-restore artifact in the Actions fallback), so posted_at drives
      freshness. Revisit if JobScout fixes first_seen persistence.
    - sponsorship is 0/1 in the export (1 = positive sponsorship signal).
    - Scores top out ~0.62; the high_match threshold in the export is 0.511.
      If the resume loaded in JobScout's vault is stale, scores skew low.
"""
import argparse
import datetime
import json
import sys
import urllib.request

FEED_URL = "https://raw.githubusercontent.com/narendranathe/job-scout/main/frontend/public/api-data.json"


def parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def fetch_feed(url=FEED_URL, timeout=30, retries=3):
    import time
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url or FEED_URL, headers={"User-Agent": "job-application-pipeline/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def candidates(data, min_score=0.55, max_age_days=3, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for job in data.get("jobs", []):
        if not job.get("is_active", True):
            continue
        score = job.get("relevance_score") or 0
        if score < min_score:
            continue
        # first_seen is a feed observation, not evidence of the posting date.
        posted = parse_dt(job.get("posted_at"))
        age_days = (now - posted).total_seconds() / 86400 if posted else None
        if age_days is None or age_days < -0.01 or age_days > max_age_days:
            continue
        out.append({
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "url": job.get("url"),
            "ats": job.get("ats"),
            "score": round(score, 3),
            "posted_at": job.get("posted_at"),
            "age_days": round(age_days, 1),
            "sponsorship": job.get("sponsorship"),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "matched_skills": job.get("matched_skills") or [],
            "is_remote": job.get("is_remote"),
            "requisition_id": job.get("requisition_id") or job.get("job_id"),
            "application_status": job.get("application_status"),
            "application_applied_at": job.get("application_applied_at"),
        })
    out.sort(key=lambda j: j["score"], reverse=True)
    return out


def feed_metadata(data, now=None, max_age_hours=24):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    exported = parse_dt(data.get("exported_at"))
    age = (now - exported).total_seconds() / 3600 if exported else None
    return {"exported_at": data.get("exported_at"), "export_age_hours": round(age, 1) if age is not None else None,
            "stale": age is None or age < -0.05 or age > max_age_hours,
            "total_jobs": data.get("stats", {}).get("total_jobs"),
            "companies_tracked": data.get("stats", {}).get("companies_tracked")}


def main():
    ap = argparse.ArgumentParser(description="Print fresh, high-relevance JobScout roles.")
    ap.add_argument("--min-score", type=float, default=0.51)
    ap.add_argument("--max-age-days", type=float, default=7)
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    try:
        data = fetch_feed()
    except Exception as e:
        print(f"FEED ERROR: {e}", file=sys.stderr)
        return 3

    cands = candidates(data, args.min_score, args.max_age_days)[:args.limit]
    stats = data.get("stats", {})
    meta = feed_metadata(data)
    meta["filters"] = {"min_score": args.min_score, "max_age_days": args.max_age_days}

    if args.json:
        print(json.dumps({"meta": meta, "candidates": cands}, indent=1))
        return 2 if meta["stale"] else 0

    stale_note = ""
    if meta["stale"]:
        stale_note = (f"  !! FEED STALE — export is {meta['export_age_hours']}h old "
                      f"(>24h). Treat scores as dated; lean on direct employer searches.\n")
        print(stale_note, file=sys.stderr)

    print(f"JobScout feed exported {meta['exported_at']} | "
          f"{meta['total_jobs']} jobs across {meta['companies_tracked']} companies | "
          f"{len(cands)} candidates (score>={args.min_score}, <={args.max_age_days}d)\n")
    for c in cands:
        sal = ""
        if c["salary_min"] or c["salary_max"]:
            lo = f"${c['salary_min']:,}" if c["salary_min"] else "?"
            hi = f"${c['salary_max']:,}" if c["salary_max"] else "?"
            sal = f" | {lo}-{hi}"
        spon = " | sponsors" if c["sponsorship"] else ""
        print(f"[{c['score']:.3f}] {c['title']} @ {c['company']} | {c['location']} "
              f"| {c['age_days']}d old{spon}{sal}\n      {c['url']}")
        if c["matched_skills"]:
            print(f"      skills: {', '.join(c['matched_skills'][:8])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
