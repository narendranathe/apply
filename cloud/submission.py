"""Official ATS reads and employer-authorized writes. No browser/session automation.

Submission methods intentionally make exactly one request. A timeout, malformed
response, or unrecognized success is an unknown effect, never a retry signal.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass


class SubmissionBlocked(Exception):
    pass


@dataclass
class Response:
    status: int
    data: dict


class HTTPTransport:
    def request(self, method, url, *, headers=None, body=None):
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        # Never follow redirects with an applicant payload or credential.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, response_headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read(8 * 1024 * 1024)
                return Response(response.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as error:
            return Response(error.code, {})


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def plain_html(value):
    value = html.unescape(html.unescape(value or ""))
    value = re.sub(r"</(?:p|li|h[1-6]|div)>|<br\s*/?>", "\n", value, flags=re.I)
    return re.sub(r"<[^>]*>", "", value).strip()


def object_array(body, key, *, required=False):
    value = body.get(key)
    if value is None and not required:
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SubmissionBlocked("official_application_schema_malformed")
    return value


def validated_questions(questions):
    for question in questions:
        if not isinstance(question.get("required"), bool):
            raise SubmissionBlocked("official_application_requirement_ambiguous")
        fields = question.get("fields")
        if not isinstance(fields, list) or any(not isinstance(field, dict) or not isinstance(field.get("name"), str) or not isinstance(field.get("type"), str) for field in fields):
            raise SubmissionBlocked("official_application_schema_malformed")
        if question["required"] and not fields:
            raise SubmissionBlocked("official_application_schema_malformed")
        for field in fields:
            values = field.get("values")
            if values is not None and (not isinstance(values, list) or any(not isinstance(option, dict) for option in values)):
                raise SubmissionBlocked("official_application_schema_malformed")
    return questions


def official_read(transport, url, headers=None):
    try:
        response = transport.request("GET", url, headers=headers)
    except Exception as error:
        raise SubmissionBlocked("official_post_unavailable") from error
    if not isinstance(response.data, dict):
        raise SubmissionBlocked("official_post_malformed")
    return response


def permitted(employer, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    permission = employer.get("permission", {})
    checked, expires = timestamp(permission.get("verified_at")), timestamp(permission.get("expires_at"))
    return bool(permission.get("permitted") is True and permission.get("evidence_url", "").startswith("https://")
                and checked and checked <= now and expires and expires > now
                and permission.get("scope") == "application_submission")


def multipart(fields, resume_path):
    boundary = "apply-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        if not re.fullmatch(r"[a-zA-Z0-9_\[\]-]+", name):
            raise SubmissionBlocked("unsupported_field_name")
        values = value if isinstance(value, list) else [value]
        for item in values:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{item}\r\n'.encode())
    with open(resume_path, "rb") as handle:
        resume = handle.read()
    if not resume.startswith(b"%PDF-"):
        raise SubmissionBlocked("resume_not_pdf")
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="resume"; filename="resume.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode() + resume + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class GreenhouseAdapter:
    ats = "greenhouse"

    def __init__(self, employer, transport=None):
        self.employer = employer
        self.transport = transport or HTTPTransport()
        self.board = str(employer.get("board_token", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.board):
            raise SubmissionBlocked("missing_verified_board")

    def verify(self, lead, *, require_schema=True):
        url = urllib.parse.urlsplit(lead.get("url") or "")
        job_id = None
        if url.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            match = re.fullmatch(r"/([^/]+)/jobs/(\d+)/?", url.path)
            if match and match[1] == self.board:
                job_id = match[2]
        elif url.hostname in self.employer.get("official_hosts", []):
            job_id = urllib.parse.parse_qs(url.query).get("gh_jid", [None])[0]
        if not job_id or not str(job_id).isdigit():
            raise SubmissionBlocked("official_post_identity_missing")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{self.board}/jobs/{job_id}"
        response = official_read(self.transport, endpoint + "?questions=true&pay_transparency=true")
        if response.status in {404, 410}:
            return {"open": False}
        body = response.data
        if not isinstance(body, dict):
            raise SubmissionBlocked("official_post_malformed")
        if response.status != 200 or str(body.get("id")) != job_id or not isinstance(body.get("content"), str) or not isinstance(body.get("title"), str):
            raise SubmissionBlocked("official_post_unverified")
        if normalize(body.get("company_name")) != normalize(lead.get("company")):
            raise SubmissionBlocked("official_employer_mismatch")
        deadline = timestamp(body.get("application_deadline"))
        if deadline and deadline <= dt.datetime.now(dt.timezone.utc):
            return {"open": False}
        job = {"open": True, "company": body["company_name"], "title": body["title"],
               "requisition_id": str(body.get("requisition_id") or body.get("internal_job_id") or job_id),
               "posting_id": job_id, "url": body.get("absolute_url") or lead["url"],
               "aliases": [lead["url"], body.get("absolute_url") or lead["url"]],
               "location": (body.get("location") or {}).get("name", "") if isinstance(body.get("location"), (dict, type(None))) else "",
               "jd": plain_html(body["content"]), "questions": [], "schema_verified": False,
               "ats": self.ats, "endpoint": endpoint, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "evidence_url": endpoint}
        if not require_schema:
            return job
        questions = (object_array(body, "questions", required=True) + object_array(body, "location_questions") + object_array(body, "compliance"))
        demographics = body.get("demographic_questions")
        if demographics is None:
            demographics = {}
        if not isinstance(demographics, dict):
            raise SubmissionBlocked("official_application_schema_malformed")
        demographics = object_array(demographics, "questions")
        for question in demographics:
            if not isinstance(question.get("required"), bool):
                raise SubmissionBlocked("official_demographic_requirement_ambiguous")
            if question.get("required"):
                questions.append({"label": question.get("label"), "required": True,
                                  "fields": [{"name": f"demographic_{question['id']}", "type": "unsupported_demographic"}]})
        for index, consent in enumerate(object_array(body, "data_compliance")):
            for key in ("requires_consent", "requires_processing_consent", "requires_retention_consent"):
                if key in consent and not isinstance(consent[key], bool):
                    raise SubmissionBlocked("official_consent_requirement_ambiguous")
            if any(consent.get(key) for key in ("requires_consent", "requires_processing_consent", "requires_retention_consent")):
                questions.append({"label": "Legal data-processing/retention consent", "required": True,
                                  "fields": [{"name": f"consent_{index}", "type": "human_attestation"}]})
        return {**job, "questions": validated_questions(questions), "schema_verified": True,
                "salary_ranges": object_array(body, "pay_input_ranges")}

    def submit(self, job, packet):
        if not permitted(self.employer):
            raise SubmissionBlocked("employer_submission_permission_missing")
        credential = os.environ.get(self.employer.get("credential_env", ""), "")
        if not credential:
            raise SubmissionBlocked("employer_scoped_credential_missing")
        payload, content_type = multipart(packet["fields"], packet["resume_path"])
        auth = base64.b64encode((credential + ":").encode()).decode()
        try:
            result = self.transport.request("POST", job["endpoint"], headers={
                "Authorization": "Basic " + auth, "Content-Type": content_type}, body=payload)
        except Exception:
            return {"status": "submission_unknown", "reason": "submission_response_uncertain"}
        # Greenhouse documents no stable applicant receipt schema. A generic 2xx
        # cannot establish a portal record. Reconciliation must supply evidence.
        return {"status": "submission_unknown", "reason": "receipt_requires_reconciliation", "http_status": result.status}


class LeverAdapter:
    ats = "lever"

    def __init__(self, employer, transport=None):
        self.employer, self.transport = employer, transport or HTTPTransport()
        self.board = str(employer.get("board_token", ""))
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.board):
            raise SubmissionBlocked("missing_verified_board")

    def verify(self, lead, *, require_schema=True):
        url = urllib.parse.urlsplit(lead.get("url") or "")
        match = re.fullmatch(r"/([^/]+)/([a-zA-Z0-9-]+)(?:/apply)?/?", url.path)
        if url.hostname != "jobs.lever.co" or not match or match[1] != self.board:
            raise SubmissionBlocked("official_post_identity_missing")
        binding = self.employer.get("employer_verification", {})
        employer_bound = bool(binding.get("evidence_url", "").startswith("https://") and timestamp(binding.get("verified_at")) and normalize(binding.get("company")) == normalize(lead.get("company")))
        if require_schema and (not binding.get("evidence_url", "").startswith("https://") or
                not timestamp(binding.get("verified_at")) or normalize(binding.get("company")) != normalize(lead.get("company"))):
            raise SubmissionBlocked("official_employer_binding_missing")
        endpoint = f"https://api.lever.co/v0/postings/{self.board}/{match[2]}"
        response = official_read(self.transport, endpoint)
        body = response.data
        if not isinstance(body, dict):
            raise SubmissionBlocked("official_post_malformed")
        if response.status in {404, 410}:
            return {"open": False}
        if response.status != 200 or body.get("id") != match[2] or not body.get("text"):
            raise SubmissionBlocked("official_post_unverified")
        sections = object_array(body, "lists")
        jd = plain_html(body.get("description", "")) + "\n" + "\n".join(plain_html(s.get("text", "")) + "\n" + plain_html(s.get("content", "")) for s in sections)
        job = {"open": True, "company": lead["company"], "employer_identity_verified": employer_bound,
               "title": body["text"], "requisition_id": body["id"], "posting_id": body["id"], "jd": jd,
               "url": body.get("hostedUrl") or lead["url"], "aliases": [lead["url"], body.get("hostedUrl") or lead["url"]],
               "location": (body.get("categories") or {}).get("location", "") if isinstance(body.get("categories"), (dict, type(None))) else "",
               "questions": [], "schema_verified": False, "ats": self.ats, "endpoint": endpoint,
               "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "evidence_url": endpoint}
        if not require_schema:
            return job
        # Public v0 postings omit custom questions. Fetch the complete official
        # authenticated form at action time; static config is insufficient.
        credential = os.environ.get(self.employer.get("schema_credential_env", ""), "")
        if not credential or not permitted(self.employer):
            raise SubmissionBlocked("current_application_form_schema_missing")
        auth = base64.b64encode((credential + ":").encode()).decode()
        form = official_read(self.transport, f"https://api.lever.co/v1/postings/{match[2]}/apply", headers={"Authorization": "Basic " + auth})
        schema = form.data.get("data") or {}
        if not isinstance(schema, dict):
            raise SubmissionBlocked("official_application_form_unverified")
        if form.status != 200 or str(schema.get("id")) != match[2] or "personalInformation" not in schema:
            raise SubmissionBlocked("official_application_form_unverified")
        # The v0 writer supports basic fields only. Any required custom form,
        # EEO or URL field stays blocked rather than being omitted silently.
        custom = [field for group in object_array(schema, "customQuestions") for field in object_array(group, "fields", required=True)]
        eeo = schema.get("eeoQuestions") or {}
        if not isinstance(eeo, dict) or any(not isinstance(value, dict) for value in eeo.values()):
            raise SubmissionBlocked("official_application_schema_malformed")
        extra_fields = custom + list(eeo.values()) + object_array(schema, "urls")
        if any(not isinstance(field.get("required"), bool) for field in extra_fields):
            raise SubmissionBlocked("official_application_requirement_ambiguous")
        if any(field.get("required") for field in extra_fields):
            raise SubmissionBlocked("lever_custom_form_requires_supported_writer")
        questions = []
        for field in object_array(schema, "personalInformation", required=True):
            if not isinstance(field.get("required"), bool):
                raise SubmissionBlocked("official_application_requirement_ambiguous")
            name = field.get("name") or field.get("type")
            if name not in {"name", "email", "phone", "resume", "location"}:
                if field.get("required"):
                    raise SubmissionBlocked("unsupported_required_personal_field")
                continue
            questions.append({"label": field.get("text") or name, "required": field.get("required", False),
                              "fields": [{"name": name, "type": "input_file" if name == "resume" else "input_text"}]})
        for name in ("name", "email"):
            if not any(q["fields"][0]["name"] == name for q in questions):
                questions.append({"label": name, "required": True, "fields": [{"name": name, "type": "input_text"}]})
        sections = object_array(body, "lists")
        jd = plain_html(body.get("description", "")) + "\n" + "\n".join(plain_html(s.get("text", "")) + "\n" + plain_html(s.get("content", "")) for s in sections)
        return {**job, "schema_verified": True, "open": True, "company": lead["company"], "title": body["text"],
                "requisition_id": body["id"], "posting_id": body["id"], "jd": jd,
                "url": body.get("hostedUrl") or lead["url"], "aliases": [lead["url"], body.get("hostedUrl") or lead["url"]],
                "location": (body.get("categories") or {}).get("location", "") if isinstance(body.get("categories"), (dict, type(None))) else "",
                "questions": questions, "form_schema_hash": __import__("hashlib").sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(), "ats": self.ats, "endpoint": endpoint,
                "salary_ranges": [{"min_cents": body["salaryRange"].get("min", 0) * 100, "max_cents": body["salaryRange"].get("max", 0) * 100, "currency_type": "USD"}]
                    if isinstance(body.get("salaryRange"), dict) and body["salaryRange"].get("currency") == "USD" and body["salaryRange"].get("interval") == "per-year-salary" else [],
                "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "evidence_url": endpoint}

    def submit(self, job, packet):
        if not permitted(self.employer):
            raise SubmissionBlocked("employer_submission_permission_missing")
        credential = os.environ.get(self.employer.get("credential_env", ""), "")
        if not credential:
            raise SubmissionBlocked("employer_scoped_credential_missing")
        body, content_type = multipart(packet["fields"], packet["resume_path"])
        endpoint = job["endpoint"] + "?" + urllib.parse.urlencode({"key": credential})
        try:
            response = self.transport.request("POST", endpoint, body=body, headers={"Content-Type": content_type})
        except Exception:
            return {"status": "submission_unknown", "reason": "submission_response_uncertain"}
        if response.status == 200 and response.data.get("ok") is True and response.data.get("applicationId"):
            return {"status": "applied_verified", "receipt_id": str(response.data["applicationId"]),
                    "evidence_url": "https://hire.lever.co/search?applicationId=" + urllib.parse.quote(str(response.data["applicationId"])),
                    "confirmed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "http_status": 200}
        # Even a rejection is not retried automatically. It may be accompanied
        # by side effects at an intermediary; preserve attempt evidence.
        return {"status": "submission_unknown", "reason": "submission_response_uncertain", "http_status": response.status}


def adapter_for(employer, transport=None):
    cls = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter}.get(employer.get("ats"))
    if not cls:
        raise SubmissionBlocked("permitted_cloud_adapter_unavailable")
    return cls(employer, transport)
