#!/usr/bin/env python3
"""Read-only scanner for employer-hosted public ATS job boards.

Reads ats_registry.json, fetches public JSON boards (Greenhouse, Lever, Ashby,
SmartRecruiters), keeps fresh US / US-remote roles in the target title families,
flags seniority and explicit sponsorship/citizenship exclusions, scores keyword
fit against the verified candidate evidence and dedupes reruns with a
content-hash cache so only new or changed roles surface.

Discovery only. It never applies, logs in, contacts anyone or authorizes
outreach. Fit score is a keyword heuristic for ordering research, not
qualification. Standard library only; no credentials or paid APIs.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "ats_registry.json"
DEFAULT_CACHE = ROOT / "ats-state" / "seen_roles.json"
USER_AGENT = "naren-ats-scan/1.0 (personal job search; read-only; python-urllib)"
MAX_BYTES = 64 * 1024 * 1024
CACHE_RETENTION_DAYS = 45

ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}",
}
SR_DETAIL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}"


# ---------------------------------------------------------------------------
# Network (only used by the CLI; tests inject a fake fetcher)
# ---------------------------------------------------------------------------

class FetchError(Exception):
    pass


def _ssl_context():
    """Verified TLS context. Falls back to a system CA bundle when the
    interpreter ships without one (python.org macOS builds). Never disables
    certificate verification."""
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and os.path.isfile(candidate):
            return ssl.create_default_context()
    if paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath):
        return ssl.create_default_context()
    for bundle in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
                   "/opt/homebrew/etc/openssl@3/cert.pem", "/usr/local/etc/openssl@3/cert.pem"):
        if os.path.isfile(bundle):
            return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


_SSL = None


def http_get_json(url, timeout=30, retries=1):
    global _SSL
    if _SSL is None:
        _SSL = _ssl_context()
    last = FetchError("not attempted")
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                           "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout, context=_SSL) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise FetchError("response exceeded size bound")
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = FetchError(f"HTTP {exc.code}")
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = FetchError(f"{type(exc).__name__}: {str(exc)[:200]}")
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

class _TextParser(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
             "tr", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value, escaped=False):
    if not isinstance(value, str) or not value:
        return ""
    if escaped:
        value = html.unescape(value)
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _clean(value):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-")


def parse_time(value):
    """ISO-8601 string or epoch milliseconds -> aware UTC datetime, else None."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


# ---------------------------------------------------------------------------
# ATS normalizers -> common raw job shape
# ---------------------------------------------------------------------------

def _raw(job_id, title, url, **extra):
    base = {"job_id": str(job_id), "title": _clean(title), "url": url, "apply_url": None,
            "locations": [], "countries": [], "remote": None, "posted_at": None,
            "updated_at": None, "department": None, "employment_type": None,
            "requisition_id": None, "text": "", "comp": None, "hiring_team_hints": [],
            "needs_detail": False}
    base.update(extra)
    return base


def normalize_greenhouse(payload):
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("unexpected Greenhouse payload")
    out = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("id") is None:
            continue
        locations = []
        name = (job.get("location") or {}).get("name") if isinstance(job.get("location"), dict) else None
        if name:
            locations.append(name)
        for office in job.get("offices") or []:
            if isinstance(office, dict):
                for key in ("location", "name"):
                    if office.get(key):
                        locations.append(office[key])
        remote = None
        comp_text = []
        for meta in job.get("metadata") or []:
            if not isinstance(meta, dict):
                continue
            label = str(meta.get("name") or "").casefold()
            value = meta.get("value")
            if isinstance(value, str):
                if "remote" in value.casefold() and ("location" in label or "workplace" in label):
                    remote = True
                if re.search(r"salary|compensation|pay", label) and "$" in value:
                    comp_text.append(value)
        departments = [d.get("name") for d in job.get("departments") or [] if isinstance(d, dict) and d.get("name")]
        text = html_to_text(job.get("content"), escaped=True)
        if comp_text:
            text = text + "\n" + "\n".join(comp_text)
        out.append(_raw(job["id"], job.get("title"), job.get("absolute_url"),
                        locations=locations, remote=remote,
                        posted_at=job.get("first_published") or None,
                        updated_at=job.get("updated_at") or None,
                        department=", ".join(departments) or None,
                        requisition_id=job.get("requisition_id") or None, text=text))
    return out


def normalize_lever(payload):
    if not isinstance(payload, list):
        raise ValueError("unexpected Lever payload")
    out = []
    for job in payload:
        if not isinstance(job, dict) or not job.get("id"):
            continue
        cats = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        locations = [cats.get("location")] + list(cats.get("allLocations") or [])
        parts = [job.get("descriptionPlain") or html_to_text(job.get("description"))]
        for item in job.get("lists") or []:
            if isinstance(item, dict):
                parts.append(_clean(item.get("text")))
                parts.append(html_to_text(item.get("content")))
        parts.append(job.get("additionalPlain") or html_to_text(job.get("additional")))
        parts.append(job.get("salaryDescriptionPlain") or "")
        comp = None
        salary = job.get("salaryRange")
        if isinstance(salary, dict) and (salary.get("min") or salary.get("max")):
            comp = {"min": salary.get("min"), "max": salary.get("max"),
                    "currency": salary.get("currency"), "interval": salary.get("interval"),
                    "summary": None, "source": "ats_field"}
        workplace = str(job.get("workplaceType") or "").casefold()
        out.append(_raw(job["id"], job.get("text"), job.get("hostedUrl"),
                        apply_url=job.get("applyUrl"),
                        locations=[x for x in locations if x],
                        countries=[job.get("country")] if job.get("country") else [],
                        remote=True if workplace == "remote" else None,
                        posted_at=job.get("createdAt"), updated_at=job.get("updatedAt"),
                        department=cats.get("team") or cats.get("department"),
                        employment_type=cats.get("commitment"),
                        text="\n".join(p for p in parts if p), comp=comp))
    return out


def _ashby_country(entry):
    try:
        return entry["address"]["postalAddress"].get("addressCountry")
    except (KeyError, TypeError, AttributeError):
        return None


def normalize_ashby(payload):
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("unexpected Ashby payload")
    out = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id") or job.get("isListed") is False:
            continue
        locations = [job.get("location")]
        countries = [_ashby_country(job)]
        for secondary in job.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                locations.append(secondary.get("location"))
                countries.append(_ashby_country(secondary))
        comp = None
        compensation = job.get("compensation") if isinstance(job.get("compensation"), dict) else None
        if compensation:
            salary = next((c for c in compensation.get("summaryComponents") or []
                           if isinstance(c, dict) and c.get("compensationType") == "Salary"), None)
            summary = (compensation.get("scrapeableCompensationSalarySummary") or
                       compensation.get("compensationTierSummary"))
            if salary or summary:
                comp = {"min": (salary or {}).get("minValue"), "max": (salary or {}).get("maxValue"),
                        "currency": (salary or {}).get("currencyCode"),
                        "interval": (salary or {}).get("interval"),
                        "summary": summary, "source": "ats_field"}
        remote = True if job.get("isRemote") is True or str(job.get("workplaceType") or "").casefold() == "remote" else None
        out.append(_raw(job["id"], job.get("title"), job.get("jobUrl"), apply_url=job.get("applyUrl"),
                        locations=[x for x in locations if x], countries=[x for x in countries if x],
                        remote=remote, posted_at=job.get("publishedAt"),
                        updated_at=job.get("updatedAt"),
                        department=" / ".join(x for x in (job.get("department"), job.get("team")) if x) or None,
                        employment_type=job.get("employmentType"),
                        text=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                        comp=comp))
    return out


def normalize_smartrecruiters_list(payload, slug):
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        raise ValueError("unexpected SmartRecruiters payload")
    out = []
    for job in content:
        if not isinstance(job, dict) or not job.get("id"):
            continue
        loc = job.get("location") if isinstance(job.get("location"), dict) else {}
        full = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        hints = []
        creator = job.get("creator") if isinstance(job.get("creator"), dict) else {}
        if creator.get("name"):
            hints.append({"name": creator["name"], "source": "smartrecruiters posting creator field",
                          "confidence": "unverified; often the requisition recruiter"})
        employment = job.get("typeOfEmployment") if isinstance(job.get("typeOfEmployment"), dict) else {}
        out.append(_raw(job["id"], job.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{job['id']}",
                        locations=[full] if full else [], countries=[loc.get("country")] if loc.get("country") else [],
                        remote=True if loc.get("remote") is True else None,
                        posted_at=job.get("releasedDate"),
                        department=(job.get("department") or {}).get("label") if isinstance(job.get("department"), dict) else None,
                        employment_type=employment.get("label"), requisition_id=job.get("refNumber"),
                        hiring_team_hints=hints, needs_detail=True))
    return out


def enrich_smartrecruiters(raw, detail):
    if not isinstance(detail, dict):
        return raw
    sections = ((detail.get("jobAd") or {}).get("sections") or {}) if isinstance(detail.get("jobAd"), dict) else {}
    texts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            texts.append(html_to_text(section.get("text")))
    raw = dict(raw)
    raw["text"] = "\n".join(t for t in texts if t)
    raw["url"] = detail.get("postingUrl") or raw["url"]
    raw["apply_url"] = detail.get("applyUrl") or raw.get("apply_url")
    raw["needs_detail"] = False
    return raw


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

EXCLUDE_TITLE = re.compile(
    r"\b(intern|internship|co-?op|apprentice(ship)?|new grad(uate)?|recruit(er|ing)|sourcer|talent|"
    r"account (executive|manager)|sales development|business development|marketing|counsel|attorney|"
    r"paralegal|accountant|accounting|payroll|data ?cent(er|re)s?|technician|director|vice president|vp|"
    r"head of|chief|manager|management|designer|copywriter|writer|specialist|coordinator|analyst|"
    r"scientist|researcher|policy|advocate|evangelist|teacher|tutor|nurse|driver|electrician|mechanic|clerk)\b", re.I)

FAMILY_RULES = (
    ("ai_platform", re.compile(
        r"\b(ai|ml|rl|machine learning|llm|genai|gen ai|inference|model|training|research|evaluation)\s+"
        r"(platform|infrastructure|infra|systems|ops|serving|data|tooling|pipelines?)\b"
        r"|\b(mlops|llmops|aiops)\b"
        r"|\b(platform|infrastructure|data)\s+engineer(ing)?\b.*\b(ai|ml|machine learning|llms?|inference)\b", re.I)),
    ("applied_ai", re.compile(
        r"\bapplied (ai|ml|llm)\b"
        r"|\b(ai|llm|genai|gen ai|generative ai|agents?|agentic|ai/ml|ml/ai)\s+"
        r"(software |solutions? |product |application |platform )?(engineer|developer|architect)s?\b"
        r"|\b(engineer|developer)s?\b.*\b(ai|llms?|genai|generative ai|agents?|agentic)\b", re.I)),
    ("analytics_engineering", re.compile(
        r"\banalytics engineer(ing)?\b|\b(bi|business intelligence) (engineer|developer)\b"
        r"|\banalytics (platform|infrastructure) engineer\b", re.I)),
    ("data_engineering", re.compile(
        r"\bdata (engineer|engineering|platform|infrastructure|infra|pipelines?|integration|warehouse|"
        r"warehousing|reliability|ops|architect|systems|tooling|foundations?|movement|streaming)\b"
        r"|\b(etl|elt|dataops)\b|\bbig data\b|\bdata ?lake(house)?\b|\blakehouse\b"
        r"|\b(software|backend|platform|infrastructure)\s+engineer\b.*\bdata\b"
        r"|\bdatabase (engineer|developer|reliability)\b|\bsql (developer|engineer)\b", re.I)),
)

SOLUTIONS_TITLE = re.compile(
    r"\bforward[- ]deployed\b|\bsolutions? (engineer|architect)\b|\bcustomer engineer\b"
    r"|\bfield engineer\b|\bsales engineer\b|\bimplementation engineer\b|\bdeployment engineer\b"
    r"|\bresident (solutions )?architect\b", re.I)
ENGINEERING_NOUN = re.compile(r"\b(engineer(s|ing)?|developer|architect|programmer|swe|sre|devops|mlops|llmops)\b", re.I)
DATA_FOCUS_TITLE = re.compile(r"\b(data|analytics|ai|ml|database|sql|lakehouse|warehouse|etl)\b", re.I)
DATA_FOCUS_TEXT = ("sql", "etl", "data pipeline", "data warehouse", "data engineering", "spark",
                   "databricks", "snowflake", "python", "data model", "lakehouse", "dbt", "airflow", "kafka")

PLANNER_FAMILY = {"ai_platform": "ai_platform", "applied_ai": "applied_ai",
                  "analytics_engineering": "data", "data_engineering": "data"}


def classify_title(title, text=""):
    """Return a target family or None. Solutions/FDE roles need a data/AI focus."""
    title = _clean(title)
    if not title or EXCLUDE_TITLE.search(title) or not ENGINEERING_NOUN.search(title):
        return None
    for family, pattern in FAMILY_RULES:
        if pattern.search(title):
            return family
    if SOLUTIONS_TITLE.search(title):
        if DATA_FOCUS_TITLE.search(title):
            return "solutions_data"
        lowered = (text or "").casefold()
        if sum(1 for word in DATA_FOCUS_TEXT if re.search(r"\b" + re.escape(word) + r"\b", lowered)) >= 3:
            return "solutions_data"
    return None


def is_solutions_candidate(title):
    title = _clean(title)
    return (bool(title) and not EXCLUDE_TITLE.search(title) and bool(ENGINEERING_NOUN.search(title))
            and bool(SOLUTIONS_TITLE.search(title)))


US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia")
US_CITIES = (
    "san francisco", "new york", "nyc", "seattle", "austin", "dallas", "houston", "boston", "chicago",
    "denver", "atlanta", "los angeles", "palo alto", "mountain view", "sunnyvale", "san jose", "san mateo",
    "menlo park", "redwood city", "bellevue", "redmond", "kirkland", "plano", "irving", "frisco",
    "richardson", "fort worth", "san antonio", "miami", "washington dc", "washington, dc", "reston",
    "pittsburgh", "philadelphia", "portland", "salt lake city", "phoenix", "tempe", "raleigh", "durham",
    "nashville", "columbus", "detroit", "minneapolis", "st. louis", "kansas city", "boulder", "san diego",
    "irvine", "santa monica", "charlotte", "tampa", "orlando", "ann arbor", "brooklyn", "jersey city",
    "foster city", "santa clara", "cupertino", "south san francisco", "oakland", "berkeley", "emeryville",
    "lehi", "scottsdale", "chandler", "addison", "allen", "mckinney", "round rock", "the woodlands",
    "abilene", "stamford", "greenwich", "hoboken", "newark", "baltimore", "indianapolis", "cincinnati",
    "cleveland", "milwaukee", "omaha", "las vegas", "sacramento", "honolulu", "new orleans")
STATE_ABBR = re.compile(
    r"(?:,|\(|-)\s*(AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|"
    r"NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
US_WORDS = re.compile(r"\b(united states|usa|u\.s\.a?\.?|north america|americas)\b", re.I)
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ottawa|calgary|london|united kingdom|uk|england|scotland|ireland|"
    r"dublin|india|bengaluru|bangalore|hyderabad|pune|mumbai|delhi|gurgaon|gurugram|noida|chennai|germany|"
    r"berlin|munich|france|paris|netherlands|amsterdam|spain|madrid|barcelona|poland|warsaw|krakow|portugal|"
    r"lisbon|singapore|japan|tokyo|korea|seoul|australia|sydney|melbourne|brazil|s[aã]o paulo|mexico|"
    r"argentina|buenos aires|colombia|bogot[aá]|israel|tel aviv|switzerland|zurich|z[uü]rich|sweden|stockholm|"
    r"denmark|copenhagen|italy|milan|czech|prague|romania|bucharest|serbia|belgrade|ukraine|emea|apac|latam|"
    r"europe|costa rica|philippines|manila|china|shanghai|beijing|hong kong|taiwan|uae|dubai|south africa|"
    r"new zealand|austria|vienna|belgium|brussels|finland|helsinki|norway|oslo|estonia|hungary|budapest|"
    r"greece|athens|turkey|istanbul|egypt|nigeria|kenya|vietnam|indonesia|malaysia|thailand|chile|peru|"
    r"uruguay|croatia|bulgaria|lithuania|latvia|slovakia|luxembourg|cambridge, uk)\b", re.I)
US_COUNTRY_CODES = {"us", "usa", "united states", "united states of america", "u.s.", "u.s.a."}
TEXAS = re.compile(
    r"\btexas\b|(?:,|\()\s*TX\b|\b(dallas|austin|houston|plano|irving|frisco|richardson|fort worth|"
    r"san antonio|addison|mckinney|round rock|the woodlands|abilene|arlington, tx|allen, tx)\b", re.I)
REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\bdistributed\b|\banywhere\b", re.I)


AMBIGUOUS_CITIES = {"dublin", "london", "paris", "athens", "manchester", "birmingham", "cambridge"}


def _segment_kind(segment):
    seg = segment.strip()
    if not seg:
        return None
    lowered = seg.casefold()
    us = bool(US_WORDS.search(seg) or re.search(r"\bUS\b|\bU\.S\b", seg) or
              re.search(r"\bus[- ]remote\b|\bremote[- ]us\b", lowered))
    non_us_hits = {m.group(0).casefold() for m in NON_US.finditer(seg.replace("New Mexico", "NM state"))}
    if non_us_hits and non_us_hits <= AMBIGUOUS_CITIES and STATE_ABBR.search(seg):
        non_us_hits = set()  # e.g. Dublin, CA or Athens, GA
    non_us = bool(non_us_hits)
    if not us and not non_us:
        us = bool(STATE_ABBR.search(seg) or any(re.search(r"\b" + re.escape(n) + r"\b", lowered)
                                               for n in US_STATE_NAMES + US_CITIES))
    remote = bool(REMOTE.search(seg))
    if us and non_us and not US_WORDS.search(seg):
        us = False
    if us:
        return "us_remote" if remote else "us"
    if non_us:
        return "non_us"
    if remote:
        return "remote"
    return "unknown"


def classify_location(locations, countries=(), remote=None):
    """Return (bucket, texas, display). bucket in us, us_remote, remote_unspecified,
    non_us, unknown."""
    segments = []
    for location in locations or []:
        if isinstance(location, str):
            segments.extend(s for s in re.split(r"\s*(?:\||;|/|\n|•| or )\s*", location) if s.strip())
    kinds = [_segment_kind(s) for s in segments]
    kinds = [k for k in kinds if k]
    country_us = any(str(c).strip().casefold() in US_COUNTRY_CODES for c in countries or [] if c)
    texas = any(TEXAS.search(s) for s in segments)
    display = "; ".join(dict.fromkeys(_clean(x) for x in locations or [] if isinstance(x, str) and x.strip()))
    if "us_remote" in kinds:
        return "us_remote", texas, display
    if "us" in kinds or country_us:
        if "remote" in kinds or remote is True:
            return "us_remote", texas, display
        return "us", texas, display
    if "remote" in kinds and "non_us" not in kinds:
        return "remote_unspecified", False, display
    if "non_us" in kinds:
        return "non_us", False, display
    return "unknown", False, display


SPONSOR_NEGATIVE = (
    re.compile(r"\b(unable|not able|cannot|can ?not|can't|will not|won't|do(es)? not|are not able|aren't able|"
               r"is not able|not in a position) (to )?(offer |provide |support |consider |pursue )?"
               r"(any )?(visa |employment |immigration |work |h-?1b )?sponsor(ship)?\b", re.I),
    re.compile(r"\bno (visa |immigration |employment |h-?1b )?sponsorship\b", re.I),
    re.compile(r"\bsponsorship (is |will )?(not|n't) (be )?(available|offered|provided|possible|supported)\b", re.I),
    re.compile(r"\bwithout (the )?(need for |requiring |requirement of |a need for )?(current or future |future |"
               r"present or future |now or in the future )?(visa |employer |employment |immigration |company )?"
               r"sponsorship\b", re.I),
    re.compile(r"\bnot (be )?eligible for (visa |immigration |h-?1b )?sponsorship\b", re.I),
)
CITIZENSHIP = re.compile(
    r"\b(must|required to) (be|hold) (a )?(u\.?s\.?|united states) citizen(ship)?\b"
    r"|\b(u\.?s\.?|united states) citizenship (is )?required\b"
    r"|\b(only|solely) (u\.?s\.? citizens|green card holders)\b", re.I)
CLEARANCE = re.compile(r"\b(secret|top secret|ts/sci|security) clearance\b", re.I)
US_PERSON = re.compile(r"\bu\.?s\.? persons?\b.{0,80}\b(itar|ear|export)\b|\b(itar|export control)\b.{0,120}\bu\.?s\.? persons?\b", re.I)
EXCLUSION_FLAGS = {"no_sponsorship_language", "citizenship_required", "security_clearance",
                   "us_person_export_control"}
EXPORT_LICENSE = re.compile(r"\bexport licen[cs]e\b"
                            r"|\bexport (laws?|control(s|led)?|regulations?)\b[^.]{0,200}\bwithout sponsorship\b", re.I)
SPONSOR_POSITIVE = re.compile(
    r"\b(we|will|can|do|does|able to|happy to|open to)\s+(\w+\s+){0,2}sponsor(s|ing)?\s+(\w+\s+){0,2}visas?\b"
    r"|\bvisa sponsorship (is )?(available|offered|provided|possible)\b"
    r"|\bh-?1b (transfer|sponsorship) (is )?(available|supported|welcome)\b", re.I)


def _sentences(text):
    """Split prose into sentences without breaking U.S./e.g.-style abbreviations."""
    text = re.sub(r"\b[Uu]\.\s?[Ss]\.(?:\s?[Aa]\.)?", "US", text or "")
    text = re.sub(r"\b(e\.g|i\.e|etc|incl|approx)\.", r"\1", text, flags=re.I)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+", text) if s.strip()]


def _snippet(sentence, limit=180):
    sentence = _clean(sentence)
    return sentence if len(sentence) <= limit else sentence[:limit - 3].rstrip() + "..."


def sponsorship_assessment(text):
    """Explicit role-level exclusions only; a general statement is not clearance."""
    flags, negative, positive = [], None, None
    for sentence in _sentences(text):
        if EXPORT_LICENSE.search(sentence):
            if "export_control_notice" not in flags:
                flags.append("export_control_notice")  # export-license boilerplate, not a visa statement
        elif any(p.search(sentence) for p in SPONSOR_NEGATIVE):
            negative = negative or sentence
            if "no_sponsorship_language" not in flags:
                flags.append("no_sponsorship_language")
        elif SPONSOR_POSITIVE.search(sentence):
            positive = positive or sentence
        if CITIZENSHIP.search(sentence) and "citizenship_required" not in flags:
            flags.append("citizenship_required")
            negative = negative or sentence
        if CLEARANCE.search(sentence) and re.search(r"\b(require|must|active|obtain|eligib|hold)", sentence, re.I) \
                and "security_clearance" not in flags:
            flags.append("security_clearance")
            negative = negative or sentence
        if US_PERSON.search(sentence) and "us_person_export_control" not in flags:
            flags.append("us_person_export_control")
            negative = negative or sentence
    if set(flags) & EXCLUSION_FLAGS:
        return {"status": "explicit_exclusion", "evidence": _snippet(negative)}, flags
    if positive:
        return ({"status": "general_positive_statement", "evidence": _snippet(positive)},
                flags + ["sponsorship_positive_statement"])
    return {"status": "unstated", "evidence": None}, flags


TITLE_HIGH = re.compile(r"\b(staff|principal|distinguished|fellow|sr\.? staff|senior staff)\b", re.I)
TITLE_SENIOR = re.compile(r"\b(senior|sr\.?|lead|iii|iv|level 3|l5|l6)\b", re.I)
YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b"
                   r"(?:[^.\n]{0,80}?)\b(experience|industry|professional|engineering|building|working|hands-on|"
                   r"software|developing|designing|production)", re.I)


def seniority_assessment(title, text):
    flags = []
    level = "standard"
    if TITLE_HIGH.search(title or ""):
        flags.append("staff_or_principal_title")
        level = "staff_or_principal"
    elif TITLE_SENIOR.search(title or ""):
        flags.append("senior_title")
        level = "senior"
    mentions = sorted({int(m.group(1)) for m in YEARS.finditer(text or "") if 0 < int(m.group(1)) <= 25})
    years_min = mentions[0] if mentions else None
    if years_min is not None and years_min >= 8:
        flags.append("experience_8plus_years")
    elif years_min is not None and years_min >= 5:
        flags.append("experience_5_to_7_years")
    if mentions and mentions[-1] >= 8 and "experience_8plus_years" not in flags:
        flags.append("mentions_8plus_years")
    return {"title_level": level, "years_min": years_min, "years_mentions": mentions}, flags


MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK]|\d{4,7})"
COMP_RANGE = re.compile(MONEY + r"\s*(?:usd|USD)?\s*(?:-|–|—|to)\s*" + MONEY, re.I)


def _money(value):
    value = value.replace(",", "").replace(" ", "")
    if value.lower().endswith("k"):
        return float(value[:-1]) * 1000
    return float(value)


def extract_comp(raw):
    if raw.get("comp"):
        comp = dict(raw["comp"])
        if not comp.get("summary") and (comp.get("min") or comp.get("max")):
            comp["summary"] = f"{comp.get('min')}-{comp.get('max')} {comp.get('currency') or ''} {comp.get('interval') or ''}".strip()
        if comp.get("summary"):
            comp["summary"] = comp["summary"].replace("–", "-").replace("—", "-")
        return comp, False
    ranges = []
    for match in COMP_RANGE.finditer(raw.get("text") or ""):
        low, high = _money(match.group(1)), _money(match.group(2))
        if 20000 <= low <= high <= 2000000:
            ranges.append((low, high))
    if not ranges:
        return None, False
    low, high = ranges[0]
    comp = {"min": int(low), "max": int(high), "currency": "USD", "interval": "year (as stated in posting text)",
            "summary": f"${int(low):,}-${int(high):,}", "source": "description_text"}
    return comp, len(set(ranges)) > 1


EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
EMAIL_ALLOWED_LOCAL = re.compile(r"^(recruit|careers?|jobs|talent|hiring|apply|join|work|people)", re.I)
EMAIL_PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
                          "proton.me", "protonmail.com", "aol.com", "live.com"}


def public_recruiting_emails(text):
    """Only role-agnostic recruiting inboxes the employer printed in the posting.
    Never guessed, never personal or accommodation/privacy inboxes."""
    found = []
    for local, domain in EMAIL.findall(text or ""):
        if domain.casefold() in EMAIL_PERSONAL_DOMAINS or not EMAIL_ALLOWED_LOCAL.search(local):
            continue
        address = f"{local}@{domain}".casefold()
        if address not in found:
            found.append(address)
    return found[:3]


# ---------------------------------------------------------------------------
# Fit scoring against verified candidate evidence (AI_CANDIDATE_EVIDENCE.md)
# ---------------------------------------------------------------------------

PRODUCTION_EVIDENCE = (
    ("SQL Server/T-SQL", r"\bsql server\b|\bt-?sql\b|\bmssql\b", 7),
    ("Azure", r"\bazure\b", 6),
    ("CDC", r"\bcdc\b|\bchange data capture\b", 6),
    ("Python", r"\bpython\b", 5),
    ("SSIS", r"\bssis\b", 5),
    ("Data validation/quality", r"\bdata (validation|quality|integrity|reliability)\b", 5),
    ("SQL", r"\bsql\b", 4),
    ("ETL/ELT pipelines", r"\b(etl|elt)\b|\bdata pipelines?\b", 4),
    ("Microsoft Fabric/Power BI", r"\bmicrosoft fabric\b|\bfabric (lakehouse|warehouse|notebooks?)\b|\bonelake\b|\bpower bi\b", 4),
    ("CI/CD/release automation", r"\bci\s*/\s*cd\b|\bazure devops\b|\bcontinuous (integration|delivery|deployment)\b|\brelease automation\b", 4),
    ("Query/index tuning", r"\bquery (optimization|tuning|performance)\b|\bstored procedures?\b|\bperformance tuning\b|\bindex(es|ing)\b", 2),
    ("Data modeling", r"\bdata model(s|ing|ling)?\b|\bdimensional model", 2),
    ("Monitoring/alerting", r"\bmonitoring\b|\balerting\b|\bobservability\b", 2),
    ("AKS/Kubernetes (Missouri S&T)", r"\baks\b|\bkubernetes\b", 2),
    ("Provisioning/recovery automation", r"\bprovisioning\b|\bdisaster recovery\b|\bbackup and restore\b", 2),
)
INDEPENDENT_EVIDENCE = (
    ("Agent harness (independent project)", r"\bagent(s|ic)?\b"),
    ("MCP integration (independent project)", r"\bmcp\b|\bmodel context protocol\b"),
    ("Retrieval/RAG (independent project)", r"\brag\b|\bretrieval\b"),
    ("LLM tooling (independent project)", r"\bllms?\b|\blarge language models?\b"),
    ("Evaluation harness (independent project)", r"\bevals?\b|\bevaluation (harness|framework|pipelines?)\b"),
    ("Kafka (independent project)", r"\bkafka\b"),
    ("Spark/Delta Lake (independent project)", r"\b(py)?spark\b|\bdelta lake\b"),
    ("FastAPI/Flask/PostgreSQL (independent project)", r"\bfastapi\b|\bflask\b|\bpostgres(ql)?\b"),
    ("Docker/GitHub Actions (independent project)", r"\bdocker\b|\bgithub actions\b"),
    ("MLflow/LightGBM (independent project)", r"\bmlflow\b|\blightgbm\b"),
)
GAP_TERMS = (
    ("Airflow", r"\bairflow\b"), ("dbt", r"\bdbt\b"), ("Snowflake", r"\bsnowflake\b"),
    ("BigQuery", r"\bbigquery\b"), ("Redshift", r"\bredshift\b"), ("AWS", r"\baws\b|\bamazon web services\b"),
    ("GCP", r"\bgcp\b|\bgoogle cloud\b"), ("Scala", r"\bscala\b"), ("Java", r"\bjava\b"),
    ("Go", r"\bgolang\b|\bgo\b(?= ?(,|and|or|/))"), ("Rust", r"\brust\b"), ("Terraform", r"\bterraform\b"),
    ("Flink", r"\bflink\b"), ("Iceberg", r"\biceberg\b"), ("Trino/Presto", r"\btrino\b|\bpresto\b"),
    ("TypeScript", r"\btypescript\b"), ("C++", r"\bc\+\+"),
)
FAMILY_BASE = {"data_engineering": 30, "analytics_engineering": 26, "ai_platform": 24,
               "applied_ai": 22, "solutions_data": 18}


def fit_assessment(family, title, text, bucket, texas, freshness, age_days, flags, employer=""):
    corpus = f"{title}\n{text}".casefold()
    production, independent, gaps = [], [], []
    score = FAMILY_BASE.get(family, 0)
    evidence_points = 0
    for label, pattern, weight in PRODUCTION_EVIDENCE:
        if re.search(pattern, corpus):
            production.append(label)
            evidence_points += weight
    score += min(40, evidence_points)
    for label, pattern in INDEPENDENT_EVIDENCE:
        if re.search(pattern, corpus):
            independent.append(label)
    score += min(8, 2 * len(independent))
    own = str(employer).casefold()
    for label, pattern in GAP_TERMS:
        if re.search(pattern, corpus) and label.casefold() not in own:
            gaps.append(label)
    score -= min(6, len(gaps))
    score += 8 if texas else 6 if bucket == "us_remote" else 3 if bucket == "us" else 1
    if freshness == "posted":
        score += 8 if age_days <= 1 else 5 if age_days <= 3 else 3
    else:
        score += 1
    penalties = {"staff_or_principal_title": 20, "experience_8plus_years": 15, "experience_5_to_7_years": 5,
                 "senior_title": 3, "non_permanent": 5}
    score -= sum(points for flag, points in penalties.items() if flag in flags)
    if EXCLUSION_FLAGS & set(flags):
        score -= 30
    return max(0, min(100, int(score))), {"production_evidence": production,
                                          "independent_evidence": independent, "gaps": gaps}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def content_hash(role):
    material = json.dumps({"title": role["title"], "location": role["location"],
                           "text": role.get("_text_digest"), "comp": (role.get("comp") or {}).get("summary"),
                           "employment_type": role.get("employment_type")}, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _freshness(raw, now, days, basis):
    posted, updated = parse_time(raw.get("posted_at")), parse_time(raw.get("updated_at"))
    if posted is None and updated is None:
        return None, None, None, None

    def age(value):
        return None if value is None else max(0.0, (now - value).total_seconds() / 86400.0)

    posted_age, updated_age = age(posted), age(updated)
    if posted_age is not None and posted_age <= days:
        return "posted", posted_age, posted, updated
    if updated_age is not None and updated_age <= days and (basis == "posted_or_updated" or posted is None):
        return "updated", updated_age, posted, updated
    return None, None, posted, updated


def evaluate(raw, employer, now, options, fetch=None):
    """Return a role dict or None. ``fetch`` is used only for SmartRecruiters detail."""
    title = raw.get("title") or ""
    family = classify_title(title, raw.get("text"))
    if family is None and not (is_solutions_candidate(title) and raw.get("needs_detail")):
        return None
    bucket, texas, display = classify_location(raw.get("locations"), raw.get("countries"), raw.get("remote"))
    if bucket in {"non_us", "unknown"} and not (bucket == "unknown" and options.get("include_unknown_location")):
        return None
    freshness, age_days, posted, updated = _freshness(raw, now, options["days"], options["basis"])
    if freshness is None:
        return None
    if raw.get("needs_detail") and fetch is not None:
        try:
            raw = enrich_smartrecruiters(raw, fetch(SR_DETAIL.format(slug=employer["slug"], job_id=raw["job_id"])))
        except Exception:  # keep the role, flagged description_not_fetched
            pass
        family = classify_title(title, raw.get("text"))
    if family is None:
        return None
    text = raw.get("text") or ""
    flags = []
    if texas:
        flags.append("texas")
    if bucket == "remote_unspecified":
        flags.append("remote_region_unspecified")
    if bucket == "unknown":
        flags.append("location_unverified")
    if freshness == "updated":
        flags.append("updated_not_new")
    if raw.get("needs_detail"):
        flags.append("description_not_fetched")
    employment = str(raw.get("employment_type") or "")
    if re.search(r"contract|temporary|temp\b|part[- ]?time|fixed[- ]term|freelance|intern(ship)?\b", employment + " " + title, re.I):
        flags.append("non_permanent")
    sponsorship, sponsor_flags = sponsorship_assessment(text)
    flags.extend(sponsor_flags)
    seniority, seniority_flags = seniority_assessment(title, text)
    flags.extend(seniority_flags)
    comp, multiple = extract_comp(raw)
    if comp is None:
        flags.append("comp_not_listed")
    elif multiple:
        flags.append("multiple_pay_ranges")
    score, fit = fit_assessment(family, title, text, bucket, texas, freshness, age_days, flags,
                                employer=employer["employer"])
    exclusion = sorted(EXCLUSION_FLAGS & set(flags))
    role = {
        "employer": employer["employer"], "ats": employer["ats"], "board": employer["slug"],
        "role_key": f"{_slug(employer['employer'])}:{raw['job_id']}", "job_id": raw["job_id"],
        "requisition_id": raw.get("requisition_id"), "title": title, "family": family,
        "planner_role_family": PLANNER_FAMILY.get(family) or (
            "applied_ai" if re.search(r"\b(ai|ml|llm|genai)\b", title, re.I) else "data"),
        "url": raw.get("url"), "apply_url": raw.get("apply_url"), "location": display,
        "location_bucket": bucket, "texas": texas, "posted_at": _iso(posted), "updated_at": _iso(updated),
        "age_days": round(age_days, 2), "freshness": freshness, "department": raw.get("department"),
        "employment_type": raw.get("employment_type"), "comp": comp, "flags": flags,
        "exclude_reason": ("explicit role-level exclusion: " + ", ".join(exclusion)) if exclusion else None,
        "sponsorship": sponsorship, "seniority": seniority, "fit_score": score, "fit": fit,
        "public_recruiting_emails": public_recruiting_emails(text),
        "hiring_team_hints": raw.get("hiring_team_hints") or [],
        "_text_digest": hashlib.sha256(_clean(text).encode("utf-8")).hexdigest(),
    }
    role["content_hash"] = content_hash(role)
    del role["_text_digest"]
    return role


def fetch_board(employer, fetch, options):
    """Return (raw_jobs, request_count). Raises on failure (isolated by caller)."""
    ats, slug = employer["ats"], employer["slug"]
    if ats == "greenhouse":
        return normalize_greenhouse(fetch(ENDPOINTS[ats].format(slug=slug))), 1
    if ats == "lever":
        return normalize_lever(fetch(ENDPOINTS[ats].format(slug=slug))), 1
    if ats == "ashby":
        return normalize_ashby(fetch(ENDPOINTS[ats].format(slug=slug))), 1
    if ats == "smartrecruiters":
        jobs, requests, offset = [], 0, 0
        cutoff = options["now"] - dt.timedelta(days=options["days"])
        for _ in range(max(1, int(employer.get("max_pages", options.get("sr_max_pages", 10))))):
            payload = fetch(ENDPOINTS[ats].format(slug=slug, offset=offset))
            requests += 1
            page = normalize_smartrecruiters_list(payload, slug)
            jobs.extend(page)
            total = payload.get("totalFound") if isinstance(payload, dict) else None
            offset += 100
            dates = [parse_time(j["posted_at"]) for j in page]
            dates = [d for d in dates if d]
            descending = all(a >= b for a, b in zip(dates, dates[1:]))
            if not page or (isinstance(total, int) and offset >= total):
                break
            if dates and descending and options.get("basis") == "posted" and dates[-1] < cutoff:
                break
            if dates and descending and dates[-1] < cutoff - dt.timedelta(days=30):
                break
        return jobs, requests
    raise ValueError(f"unsupported ATS {ats!r}")


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("employers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("registry needs an employers list")
    valid = []
    for row in rows:
        if (isinstance(row, dict) and row.get("enabled", True) is not False and row.get("ats") in ENDPOINTS
                and isinstance(row.get("slug"), str) and row["slug"].strip()
                and isinstance(row.get("employer"), str) and row["employer"].strip()):
            valid.append(row)
    return valid


def load_cache(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("roles"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "roles": {}}


def save_cache(path, cache):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ats-cache-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cache, stream, indent=1, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_cache(roles, cache, now, include_seen=False):
    """Mark new/changed/seen, update cache entries in place, return surfaced roles."""
    entries = cache.setdefault("roles", {})
    surfaced = []
    stamp = _iso(now)
    for role in roles:
        previous = entries.get(role["role_key"])
        if previous is None:
            role["change"] = "new"
        elif previous.get("hash") != role["content_hash"]:
            role["change"] = "changed"
        else:
            role["change"] = "seen"
        role["first_seen"] = (previous or {}).get("first_seen") or stamp
        entries[role["role_key"]] = {"hash": role["content_hash"], "first_seen": role["first_seen"],
                                     "last_seen": stamp, "employer": role["employer"], "title": role["title"]}
        if role["change"] != "seen" or include_seen:
            surfaced.append(role)
    cutoff = now - dt.timedelta(days=CACHE_RETENTION_DAYS)
    for key in [k for k, v in entries.items() if (parse_time(v.get("last_seen")) or now) < cutoff]:
        del entries[key]
    return surfaced


def _rank_key(role):
    """Explicit exclusions last; newly posted before update-only; then fit, age."""
    return (role["exclude_reason"] is not None, role["freshness"] != "posted", -role["fit_score"],
            role["age_days"], role["employer"].casefold(), role["title"].casefold(), role["role_key"])


def finalize_roles(roles):
    """Rank and collapse same-title postings at one employer (usually one requisition
    listed per location) into the best-ranked row. Idempotent."""
    ordered = sorted(roles, key=_rank_key)
    primary, out = {}, []
    for role in ordered:
        key = (role["employer"].casefold(), re.sub(r"\s+", " ", role["title"]).strip().casefold(),
               role["exclude_reason"] is not None)
        if key not in primary:
            role = dict(role)
            role["duplicate_postings"] = list(role.get("duplicate_postings") or [])
            primary[key] = role
            out.append(role)
            continue
        lead = primary[key]
        known = {d["role_key"] for d in lead["duplicate_postings"]} | {lead["role_key"]}
        for item in [role] + list(role.get("duplicate_postings") or []):
            if item["role_key"] not in known:
                lead["duplicate_postings"].append({k: item.get(k) for k in ("role_key", "url", "location", "posted_at")})
                known.add(item["role_key"])
        lead["texas"] = lead["texas"] or role["texas"]
        if role["texas"] and "texas" not in lead["flags"]:
            lead["flags"] = lead["flags"] + ["texas"]
    return out


def rerender(report):
    """Offline re-rank of a saved report with current rules. Re-checks the stored
    sponsorship evidence sentence; if it no longer qualifies, the role is marked
    needs_recheck (other sentences were not stored), never silently cleared."""
    roles = []
    for role in report.get("roles") or []:
        role = dict(role)
        sponsorship = role.get("sponsorship") or {}
        if sponsorship.get("status") == "explicit_exclusion" and sponsorship.get("evidence"):
            status, flags = sponsorship_assessment(sponsorship["evidence"])
            if status["status"] != "explicit_exclusion":
                role["flags"] = [f for f in role["flags"] if f not in EXCLUSION_FLAGS] + \
                    [f for f in flags if f not in role["flags"]] + ["sponsorship_needs_recheck"]
                role["sponsorship"] = {"status": "needs_recheck", "evidence": sponsorship["evidence"]}
                role["exclude_reason"] = None
        own = role["employer"].casefold()
        role["fit"] = dict(role["fit"], gaps=[g for g in role["fit"].get("gaps", []) if g.casefold() not in own])
        roles.append(role)
    report = dict(report)
    report["roles"] = finalize_roles(roles)
    report["counts"] = dict(report.get("counts") or {}, roles_surfaced=len(report["roles"]),
                            roles_excluded_explicit=sum(1 for r in report["roles"] if r["exclude_reason"]))
    report["rerendered_at"] = _iso(dt.datetime.now(dt.timezone.utc))
    return report


def scan(registry, fetch, now, days=3, basis="posted_or_updated", cache=None, include_seen=False,
         include_unknown_location=False, workers=4, sr_max_pages=10, max_detail_fetches=40):
    """Scan every registry board with failure isolation. Pure apart from ``fetch``."""
    options = {"now": now, "days": days, "basis": basis, "include_unknown_location": include_unknown_location,
               "sr_max_pages": sr_max_pages}

    def one(employer):
        status = {"employer": employer["employer"], "ats": employer["ats"], "slug": employer["slug"],
                  "status": "ok", "error": None, "requests": 0, "jobs_listed": 0, "fresh_relevant": 0}
        roles = []
        try:
            raws, status["requests"] = fetch_board(employer, fetch, options)
            status["jobs_listed"] = len(raws)
            budget = [int(employer.get("max_detail_fetches", max_detail_fetches))]

            def detail_fetch(url):
                if budget[0] <= 0:
                    raise FetchError("detail fetch budget exhausted")
                budget[0] -= 1
                status["requests"] += 1
                return fetch(url)

            for raw in raws:
                try:
                    role = evaluate(raw, employer, now, options,
                                    fetch=detail_fetch if raw.get("needs_detail") else None)
                except Exception as exc:  # isolate one malformed posting
                    status.setdefault("posting_errors", []).append(f"{raw.get('job_id')}: {type(exc).__name__}")
                    continue
                if role:
                    roles.append(role)
            status["fresh_relevant"] = len(roles)
        except Exception as exc:  # isolate one failing source; never report it as zero vacancies
            status["status"] = "error"
            status["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return status, roles

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, registry))
    sources = [status for status, _ in results]
    roles = [role for _, rows in results for role in rows]
    seen = {}
    for role in roles:  # duplicate registry rows / multi-board employers
        seen.setdefault(role["role_key"], role)
    roles = list(seen.values())
    if cache is not None:
        roles = apply_cache(roles, cache, now, include_seen=include_seen)
    else:
        for role in roles:
            role["change"] = "uncached"
            role["first_seen"] = _iso(now)
    roles = finalize_roles(roles)
    failed = [s for s in sources if s["status"] != "ok"]
    return {
        "generated_at": _iso(now), "window_days": days, "freshness_basis": basis,
        "sending_authorized": False,
        "note": ("Discovery only. Fit score is a keyword heuristic for ordering research, not qualification. "
                 "Recheck the official posting, requirements and sponsorship language before any application "
                 "or outreach. A failed source is unknown, not zero vacancies."),
        "counts": {"boards": len(sources), "boards_failed": len(failed),
                   "requests": sum(s["requests"] for s in sources),
                   "roles_surfaced": len(roles),
                   "roles_excluded_explicit": sum(1 for r in roles if r["exclude_reason"])},
        "sources": sources,
        "roles": roles,
    }


def render_summary(report, top=10):
    """Short human summary: newly posted roles first, update-only roles fill any gap. No em dashes."""
    def clean(value):
        return str(value or "").replace("—", "-").replace("–", "-").replace("|", "/")

    eligible = [r for r in report["roles"] if not r["exclude_reason"]]
    posted = [r for r in eligible if r["freshness"] == "posted"]
    updated = [r for r in eligible if r["freshness"] != "posted"]
    chosen = (posted + updated)[:top]
    excluded = [r for r in report["roles"] if r["exclude_reason"]]
    failed = [s for s in report["sources"] if s["status"] != "ok"]
    counts = report["counts"]
    lines = [f"# ATS scan summary, {report['generated_at'][:10]}", "",
             f"Window: posted (or updated) within {report['window_days']:g} days. Boards scanned: {counts['boards']}; "
             f"failed: {counts['boards_failed']}; requests: {counts['requests']}. Distinct roles: {len(report['roles'])} "
             f"({len(posted)} newly posted, {len(updated)} update-only, {len(excluded)} with explicit exclusion language). "
             "Same-title postings at one employer are collapsed into one row.", "",
             "Discovery only. Score is a keyword heuristic, not qualification. Each role still needs the official "
             "posting recheck, fit/gap qualification and the normal identity/history gates before any application "
             "or outreach.", "",
             f"## Top {len(chosen)} roles ({min(len(posted), top)} newly posted)", "",
             "| # | Employer | Role | Location | Posted | Pay (as listed) | Score | Flags |",
             "|---|---|---|---|---|---|---|---|"]
    for index, role in enumerate(chosen, 1):
        when = (role["posted_at"] or role["updated_at"] or "")[:10]
        if role["freshness"] != "posted":
            when = f"updated {(role['updated_at'] or '')[:10]}"
        flags = ", ".join(f for f in role["flags"] if f not in {"comp_not_listed"}) or "none"
        pay = (role["comp"] or {}).get("summary") or "not listed"
        extra = f" (+{len(role.get('duplicate_postings') or [])} locations)" if role.get("duplicate_postings") else ""
        lines.append(f"| {index} | {clean(role['employer'])} | [{clean(role['title'])}]({role['url']}){extra} | "
                     f"{clean(role['location'])[:60]} | {when} | {clean(pay)} | {role['fit_score']} | {clean(flags)} |")
    lines.append("")
    for index, role in enumerate(chosen, 1):
        fit = role["fit"]
        years = role["seniority"].get("years_min")
        lines.append(f"{index}. {clean(role['employer'])}, {clean(role['title'])}: matched "
                     f"{', '.join(fit['production_evidence']) or 'no production keywords'}; independent "
                     f"{', '.join(fit['independent_evidence']) or 'none'}; gaps {', '.join(fit['gaps']) or 'none listed'}; "
                     f"stated years {years if years is not None else 'not stated'}; sponsorship {role['sponsorship']['status']}.")
    if excluded:
        lines += ["", "## Excluded by explicit role language (that requisition only, not the employer)", ""]
        for role in excluded[:15]:
            lines.append(f"- {clean(role['employer'])}, [{clean(role['title'])}]({role['url']}): "
                         f"{clean(role['exclude_reason'])}")
    if failed:
        lines += ["", "## Sources that failed (unknown, not zero vacancies)", ""]
        for source in failed:
            lines.append(f"- {clean(source['employer'])} ({source['ats']}/{source['slug']}): {clean(source['error'])}")
    lines.append("")
    return "\n".join(lines)


def main(argv=None, fetch=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, help="write the JSON report here (default stdout)")
    parser.add_argument("--summary", type=Path, help="also write a short Markdown summary")
    parser.add_argument("--days", type=float, default=3.0, help="freshness window in days (default 3)")
    parser.add_argument("--basis", choices=("posted", "posted_or_updated"), default="posted_or_updated")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--no-cache", action="store_true", help="neither read nor write the dedupe cache")
    parser.add_argument("--dry-run", action="store_true", help="read the cache but do not update it")
    parser.add_argument("--include-seen", action="store_true", help="also output unchanged roles seen before")
    parser.add_argument("--include-unknown-location", action="store_true")
    parser.add_argument("--only", action="append", default=[], help="limit to employer name(s)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--rerender", type=Path, metavar="REPORT",
                        help="offline: re-rank/regroup a saved report with current rules; no network, no cache")
    args = parser.parse_args(argv)
    try:
        if args.rerender:
            report = rerender(json.loads(args.rerender.read_text(encoding="utf-8")))
            text = json.dumps(report, indent=1, ensure_ascii=False)
            if args.output:
                args.output.write_text(text + "\n", encoding="utf-8")
            else:
                print(text)
            if args.summary:
                args.summary.write_text(render_summary(report, args.top), encoding="utf-8")
            return 0
        registry = load_registry(args.registry)
        if args.only:
            wanted = {x.casefold() for x in args.only}
            registry = [r for r in registry if r["employer"].casefold() in wanted]
        if args.days <= 0:
            raise ValueError("--days must be positive")
        now = now or dt.datetime.now(dt.timezone.utc)
        cache = None if args.no_cache else load_cache(args.cache)
        report = scan(registry, fetch or http_get_json, now, days=args.days, basis=args.basis, cache=cache,
                      include_seen=args.include_seen, include_unknown_location=args.include_unknown_location,
                      workers=args.workers)
        report["registry"] = str(args.registry)
        if cache is not None and not args.dry_run:
            save_cache(args.cache, cache)
        text = json.dumps(report, indent=1, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        if args.summary:
            args.summary.write_text(render_summary(report, args.top), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
