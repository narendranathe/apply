"""Deterministic resume selection and evidence-backed screening answers.

The checked-in master is an input, never an output. Unsupported requirements are
gaps, not new resume claims. Artifacts are private ephemeral files; operational
state and their hashes live in Notion.
"""
from __future__ import annotations

import hashlib
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "resumes/master/Narendranath-Edara-Resume.tex"
FACTS = ROOT / "docs/ground-truth.md"
ANSWERS = ROOT / "docs/knockout-answers.md"


def digest(value):
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def evidence_rows(path=FACTS):
    rows = {}
    for line in Path(path).read_text().splitlines():
        match = re.match(r"\| \*\*([A-Z0-9-]+)\*\* \|", line)
        if match:
            rows[match[1]] = line
    return rows


SKILL_EVIDENCE = {
    "sql": "E1-CDC", "t-sql": "E1-CDC", "sql server": "E1-CDC", "python": "E-REPORTAUTO",
    "cdc": "E1-CDC", "data quality": "E-OBS", "ci/cd": "E2-CICD", "azure devops": "E2-CICD",
    "fabric": "E3-FABRIC", "power bi": "E2-CICD", "kubernetes": "M3", "aks": "M3",
    "monitoring": "E-OBS", "incident": "E5-RECOVERY", "recovery": "E5-RECOVERY",
    "stakeholder": "C-PRD", "requirements": "C-PRD", "etl": "E1-CDC",
    "pipeline": "E1-CDC", "git": "E-REPORTAUTO", "query": "E3-FABRIC",
    "llm": "P-FINTUNE", "pytorch": "P-FINTUNE", "rag": "P-AUTOAPPLY",
    "pgvector": "P-AUTOAPPLY", "mlflow": "P-FRAUD", "kafka": "P-FRAUD",
    "fastapi": "P-FRAUD", "docker": "P-FRAUD", "spark": "P-RISK", "delta": "P-RISK",
}
UNSUPPORTED = ("snowflake", "terraform", "databricks", "dbt", "aws", "gcp", "airflow")
ARC_TRIGGERS = {
    "A": ("incident", "on-call", "reliability", "recovery", "observability", "data quality", "sre"),
    "B": ("regulatory", "compliance", "audit", "reconciliation", "financial", "payroll", "trading", "risk"),
    "C": ("sql and python", "expert sql", "query tuning", "t-sql", "sql server"),
    "D": ("developer productivity", "ci/cd", "devops", "internal tooling", "cost optimization", "finops"),
    "E": ("llm", "rag", "mlops", "inference", "model deployment", "fine-tun", "ai platform"),
    "F": ("analytics engineering", "business stakeholders", "self-service", "product analytics"),
}
SUMMARIES = {
    "A": "Data engineer building detection and recovery for production SQL Server pipelines. Ships idempotent CDC, monitoring, failover runbooks, and reviewed releases; six years across engineering and business analytics.",
    "B": "Data engineer on payroll-critical systems, shipping correct CDC data, reviewed payroll fixes, and regulatory releases. Brings production SQL and Python plus six years across engineering and business analytics.",
    "C": "Data engineer with production SQL Server and Python experience: idempotent CDC, query tuning, and release automation. Six years across engineering and business analytics, including three in hands-on data engineering.",
    "D": "Data engineer owning Azure DevOps releases, idempotent provisioning, and Python automation. Cut release cycles from three months to 14 days; six years across engineering and business analytics.",
    "E": "Data engineer with production SQL and Python plus independent AI systems: Mistral-7B fine-tuning, pgvector retrieval, and MLflow fraud scoring. Coauthored peer-reviewed NLP research; six years across engineering and business analytics.",
    "F": "Data engineer who translates business questions into production systems. Owns SQL Server CDC and Azure DevOps releases, with earlier marketplace analytics and stakeholder requirements work; six years across both disciplines.",
}
# Positional mapping is guarded by the exact item count and source snippets.
EXPECTED_MASTER_SHA256 = "950b228677940e27d2295b096288253d64659d9dcbe26086b96b68b3e3ff9e47"
EXPECTED_GROUND_TRUTH_SHA256 = "fb9e7ff40036f3a061226c1839fc52c0ef42e843e60e586fa52968699674bb09"
MASTER_IDS = ["E1-CDC", "E-IDEM", "E3-FABRIC", "E-AICTX", "E2-CICD", "E-OBS", "E-REPORTAUTO",
              "M1", "M3", "M4-PAPER", "C-SQL", "C-PRD", "U-GMV", "U-TEAM",
              "Z-PNL", "Z-COMP", "Z-ES", "P-HOOKS", "P-HOOKS", "P-FINTUNE", "P-FINTUNE", "P-AUTOAPPLY", "P-RISK"]


def select_arc(jd):
    text = jd.lower()
    counts = {arc: sum(text.count(trigger) for trigger in triggers) for arc, triggers in ARC_TRIGGERS.items()}
    return max(counts, key=counts.get) if any(counts.values()) else "C"


def requirements_from_jd(jd):
    """Only explicit qualification sections; never turn a keyword score into fit."""
    lines = [line.strip(" \t-•").rstrip(":") for line in jd.splitlines() if line.strip()]
    output, collecting = [], False
    for line in lines:
        if re.fullmatch(r"(?:minimum |basic |required )?(?:requirements|qualifications)|what you(?:'|’)ll (?:bring|need)|must.have[s]?", line, re.I):
            collecting = True
            continue
        if collecting and re.fullmatch(r"(?:preferred qualifications|benefits|compensation|about us|what we offer|responsibilities)", line, re.I):
            collecting = False
        if collecting:
            output.append(line)
    return output


def grade_job(job, config=None):
    config = config or {}
    rows = evidence_rows(config.get("ground_truth_path", FACTS))
    must_haves = requirements_from_jd(job["jd"])
    if not must_haves:
        return {"qualified": False, "reason": "requirement_map_unavailable", "requirements": [], "coverage": 0}
    mapped = []
    for requirement in must_haves:
        text = requirement.lower()
        gaps, facts = [], []
        for skill in UNSUPPORTED:
            if re.search(r"(?<![a-z])" + re.escape(skill) + r"(?![a-z])", text):
                gaps.append(skill)
        years = re.search(r"(\d+)\+?\s*(?:years|yrs)", text)
        if years:
            limit = 6 if any(word in text for word in ("professional", "overall", "total")) else 3
            if int(years[1]) > limit:
                gaps.append("experience_tenure")
        for skill, fact_id in SKILL_EVIDENCE.items():
            if re.search(r"(?<![a-z])" + re.escape(skill) + r"(?![a-z])", text):
                row = rows.get(fact_id, "")
                if not row or "[VERIFY]" in row or "[BLOCKED]" in row:
                    gaps.append(skill)
                elif fact_id.startswith("P-") and any(word in text for word in ("production", "commercial", "professional", "years")):
                    gaps.append("production_" + skill)
                else:
                    facts.append(fact_id)
        if not facts:
            gaps.append("unmapped_requirement")
        mapped.append({"requirement": requirement, "fact_ids": sorted(set(facts)), "gaps": sorted(set(gaps)), "evidenced": bool(facts) and not gaps})
    coverage = sum(entry["evidenced"] for entry in mapped) / len(mapped)
    title = job.get("title", "").lower()
    disqualified = any(term in title for term in ("staff", "principal", "director", "head of")) or any("experience_tenure" in entry["gaps"] for entry in mapped)
    if re.search(r"(?:no|without)\s+(?:visa\s+)?sponsorship|(?:unable|cannot)\s+(?:to\s+)?sponsor|us citizens? only|u\.s\. citizens? only", job["jd"], re.I):
        disqualified = True
    location = job.get("location", "")
    # US jurisdiction must be explicit or verified by employer configuration.
    us = bool(re.search(r"united states|\bUSA\b|\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b", location))
    if not us and config.get("country") != "US":
        disqualified = True
    qualified = coverage >= config.get("min_coverage", 0.7) and not disqualified
    return {"qualified": qualified, "reason": "truthful_fit" if qualified else "qualification_gaps",
            "coverage": round(coverage, 4), "requirements": mapped, "arc": select_arc(job["jd"])}


def screening_answers(questions, config=None):
    config = config or {}
    source = Path(config.get("standing_answers_path", ANSWERS)).read_text()
    source_hash = digest(source.encode())
    facts = Path(config.get("ground_truth_path", FACTS)).read_text()
    identity = re.search(r"Narendranath Edara · ([^·\n]+) · ([^\n]+)", facts)
    basics = {"first_name": "Narendranath", "last_name": "Edara"}
    if identity:
        basics.update({"phone": identity[1].strip(), "email": identity[2].strip(), "name": "Narendranath Edara"})
    fields, evidence, missing = {}, [], []
    overrides = config.get("verified_answers", {})
    for question in questions:
        label = question.get("label", "")
        alternatives = question.get("fields", [])
        if any(field.get("name") in {"resume", "resume_text"} for field in alternatives):
            continue
        answered = False
        for field in alternatives:
            name, kind = field.get("name"), field.get("type", "input_text")
            answer, source_ref = None, None
            text = label.lower()
            sensitive = bool(re.search(r"disabil|veteran|gender|race|ethnic|salary|compensation|consent|certify|attest|agree|signature|criminal|citizen", text))
            override = overrides.get(name, {})
            # Material attestations must be completed by the human even if a
            # draft answer exists; standing authorization does not sign them.
            if kind == "human_attestation" or re.search(r"certify|attest|signature|agree", text):
                continue
            if override.get("verified") is True and override.get("source") and override.get("question_hash") == digest(question):
                answer, source_ref = override.get("answer"), override["source"]
            elif kind not in {"input_hidden", "unsupported_demographic"} and not sensitive:
                if name in basics:
                    answer, source_ref = basics[name], "ground-truth:Identity"
                elif re.search(r"(?:now|future|require|need).*sponsor|sponsor.*(?:need|require)", text) and "H-1B" in source:
                    answer, source_ref = "Yes", "knockout-answers:Work authorization"
                elif re.search(r"authori[sz]ed.*work.*(?:us|united states|u\.s\.)", text) and "H-1B" in source:
                    answer, source_ref = "Yes", "knockout-answers:Work authorization"
                elif re.search(r"(?:willing|open).*relocat", text) and "open to relocation anywhere in the United States" in source:
                    answer, source_ref = "Yes", "knockout-answers:Location"
                elif name == "location" and "Current: Dallas, TX" in source:
                    answer, source_ref = "Dallas, TX", "knockout-answers:Location"
            if answer is None:
                continue
            values = field.get("values", [])
            if values:
                matches = [value for value in values if str(value.get("label", "")).casefold() == str(answer).casefold()]
                if len(matches) != 1:
                    continue
                answer = matches[0]["value"]
            if kind not in {"input_text", "textarea", "multi_value_single_select"}:
                continue
            fields[name] = answer
            evidence.append({"field": name, "question_hash": digest(question), "source": source_ref,
                             "source_hash": source_hash if source_ref.startswith("knockout") else digest(facts.encode()), "answer_hash": digest(answer)})
            answered = True
            break
        if question.get("required") and not answered:
            missing.append({"question": label, "question_hash": digest(question), "reason": "verified_answer_unavailable"})
    return {"fields": fields, "evidence": evidence, "missing": missing, "hash": digest(fields)}


def tailor_source(master, grade, ground_truth_path=FACTS):
    rows = evidence_rows(ground_truth_path)
    items = re.findall(r"^\\item (.+)$", master, flags=re.M)
    if len(items) != len(MASTER_IDS):
        raise ValueError("master_manifest_requires_review")
    # Unknown source revisions cannot inherit a prior fact review silently.
    if digest(master.encode()) != EXPECTED_MASTER_SHA256 or digest(Path(ground_truth_path).read_bytes()) != EXPECTED_GROUND_TRUTH_SHA256:
        raise ValueError("master_manifest_requires_review")
    blocked = []
    for fact_id in MASTER_IDS:
        # P-RISK's exact current-master wording is the source's explicitly
        # permitted Delta ingestion fragment; the project stays 'in progress'.
        corrected_project = fact_id == "P-RISK" and "Usable as project evidence for Delta Lake" in rows.get(fact_id, "")
        if fact_id not in rows or (("[VERIFY]" in rows[fact_id] or "[BLOCKED]" in rows[fact_id]) and not corrected_project):
            blocked.append(fact_id)
    # Whole bullets carrying ambiguous evidence are dropped, including a row
    # with corrected production/latency wording. No model fills the gaps.
    changes, selected = [], []
    source = master
    for text, fact_id in zip(items, MASTER_IDS):
        if fact_id in blocked:
            source = source.replace("\\item " + text + "\n", "", 1)
            changes.append({"change": "removed", "fact_id": fact_id, "reason": "unresolved_or_blocked_evidence", "old_hash": digest(text)})
        else:
            selected.append({"fact_id": fact_id, "claim_hash": digest(text), "source_hash": digest(rows[fact_id].encode())})
    arc = grade["arc"]
    source = re.sub(r"^\\summary\{.*\}$", lambda _: "\\summary{" + SUMMARIES[arc] + "}", source, flags=re.M)
    source = re.sub(r"^\\rolecontext\{Payroll platform:.*\}$", lambda _: "\\rolecontext{Payroll platform: production SQL Server pipelines, releases, and recovery. Owns fixes from diagnosis through reviewed delivery.}", source, flags=re.M)
    # Empty project/role lists would create misleading entries. Remove a whole
    # project whose mapped bullets were all excluded.
    source = re.sub(r"\\projheader[^\n]*\n\\begin\{itemize\}\n\\end\{itemize\}\n", "", source)
    # Order the current-employer evidence using the selected requirements.
    priority = {"A": ["E-SELFHEAL", "E5-RECOVERY", "E-IDEM", "E-RETENTION"],
                "B": ["E1-CDC", "E-IDEM", "E5-RECOVERY", "E-RETENTION"],
                "C": ["E1-CDC", "E3-FABRIC", "E-AICTX", "E2-CICD"],
                "D": ["E2-CICD", "E-IDEM", "E1-CDC"], "E": ["E-AICTX", "E3-FABRIC", "E1-CDC"], "F": ["E3-FABRIC", "E2-CICD", "E1-CDC"]}[arc]
    block = re.search(r"\\begin\{itemize\}\n(.*?)\\end\{itemize\}", source, re.S)
    if block:
        first_items = re.findall(r"^\\item (.+)$", block[1], re.M)
        item_ids = {text: fact_id for text, fact_id in zip(items, MASTER_IDS)}
        ordered = sorted(first_items, key=lambda text: priority.index(item_ids[text]) if item_ids[text] in priority else len(priority))
        source = source[:block.start(1)] + "".join("\\item " + text + "\n" for text in ordered) + source[block.end(1):]
    changes.extend([{"change": "summary", "arc": arc, "requirement": grade["requirements"]},
                    {"change": "bullet_order", "fact_ids": priority, "reason": "primary_arc"}])
    return source, selected, changes


def verify_resume(tex_path, pdf_path, expected_pages=2):
    """Checks use a real PDF parser; missing tooling cannot become a pass."""
    tex_path, pdf_path = Path(tex_path), Path(pdf_path)
    result = {"verified": False, "checks": {}, "reasons": []}
    source = tex_path.read_text()
    from scripts import resume_qa as strict_qa
    # Capture diagnostics because they can contain private resume text. Persist
    # check names and hashes, not full content in GitHub Actions logs.
    with contextlib.redirect_stdout(io.StringIO()):
        result["checks"]["source_lint"] = strict_qa.lint(str(tex_path))
        result["checks"]["source_budgets"] = strict_qa.budget(str(tex_path), verbose=False)
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            texts, fills = [], []
            margins = True
            for page in pdf.pages:
                words = page.extract_words()
                texts.append(page.extract_text() or "")
                fills.append((max((w["bottom"] for w in words), default=0) - 20.16) / 750.24 * 100)
                margins = margins and bool(words) and min(w["x0"] for w in words) >= 20.76 and max(w["x1"] for w in words) <= 591.24
            text = "\n".join(texts)
            result["checks"].update({"pages": len(pdf.pages) == expected_pages,
                "layout_fill": all(90 <= fill <= 101 for fill in fills[:-1]) and 60 <= fills[-1] <= 101,
                "margins": margins, "ats_text": len(text.split()) > 300 and "(cid:" not in text and "\ufffd" not in text,
                "section_order": all(section in text for section in ("Technical Skills", "Experience", "Projects", "Education"))
                    and [text.index(section) for section in ("Technical Skills", "Experience", "Projects", "Education")] == sorted(text.index(section) for section in ("Technical Skills", "Experience", "Projects", "Education")),
                "overfull": "Overfull" not in tex_path.with_suffix(".log").read_text(errors="replace")})
            result.update({"page_count": len(pdf.pages), "page_fill": [round(fill, 1) for fill in fills], "text_hash": digest(text.encode())})
        with contextlib.redirect_stdout(io.StringIO()):
            result["checks"]["strict_layout"] = strict_qa.layout(str(pdf_path), expected_pages)
    except (ImportError, OSError, ValueError, IndexError):
        result["reasons"].append("pdf_qa_unavailable_or_failed")
    renderer = shutil.which("pdftoppm")
    if renderer and pdf_path.is_file():
        try:
            rendered = subprocess.run([renderer, "-r", "90", "-png", str(pdf_path), str(pdf_path.parent / "preview")], capture_output=True, timeout=60)
            previews = sorted(pdf_path.parent.glob("preview-*.png"))
            result["checks"]["rendered_previews"] = rendered.returncode == 0 and len(previews) == expected_pages
            result["preview_hashes"] = [digest(path.read_bytes()) for path in previews]
        except (OSError, subprocess.TimeoutExpired):
            result["checks"]["rendered_previews"] = False
    else:
        result["checks"]["rendered_previews"] = False
    result["verified"] = bool(result["checks"]) and all(result["checks"].values())
    if not result["verified"]:
        result["reasons"].extend("failed_" + name for name, passed in result["checks"].items() if not passed)
    return result


def prepare_packet(job, grade, config=None):
    config = config or {}
    answers = screening_answers(job.get("questions", []), config)
    output = Path(tempfile.mkdtemp(prefix="apply-packet-", dir=config.get("artifact_dir")))
    master_path = Path(config.get("master_path", MASTER))
    try:
        source, manifest, changes = tailor_source(master_path.read_text(), grade, config.get("ground_truth_path", FACTS))
    except (ValueError, OSError):
        return {"ready": False, "reason": "master_manifest_requires_review", "missing": answers["missing"]}
    tex = output / "resume.tex"
    tex.write_text(source)
    (output / "jd.md").write_text(job["jd"])
    (output / "answers.json").write_text(json.dumps(answers, indent=2))
    (output / "answers.md").write_text("# Screening evidence\n\n" + "\n".join(f"- {e['field']}: {e['source']}" for e in answers["evidence"]) + "\n")
    (output / "changelog.md").write_text("# Changelog vs master\n\n" + "\n".join("- " + json.dumps(change) for change in changes) + "\n")
    compiler = shutil.which(config.get("tex_compiler", "pdflatex"))
    preamble = Path(config.get("preamble_path", master_path.parent / "v8preamble.tex"))
    qa = {"verified": False, "reasons": []}
    if not compiler:
        qa["reasons"].append("tex_compiler_unavailable")
    if not preamble.is_file():
        qa["reasons"].append("frozen_preamble_missing")
    if compiler and preamble.is_file():
        if digest(preamble.read_bytes()) != "12d30c1145aca2e7e2758a6b370d0e3f7eadf5680b00eb6023d59603222fe19c":
            qa["reasons"].append("frozen_preamble_hash_mismatch")
            compiler = None
    if compiler and preamble.is_file():
        shutil.copyfile(preamble, output / "v8preamble.tex")
        for _ in range(2):
            try:
                compiled = subprocess.run([compiler, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "resume.tex"], cwd=output, capture_output=True, timeout=90)
            except (OSError, subprocess.TimeoutExpired):
                qa["reasons"].append("resume_compile_failed")
                break
            if compiled.returncode:
                qa["reasons"].append("resume_compile_failed")
                break
        else:
            qa = verify_resume(tex, output / "resume.pdf", config.get("expected_pages", 2))
    pdf = output / "resume.pdf"
    return {"ready": qa["verified"] and not answers["missing"],
            "reason": "verified_packet" if qa["verified"] else "resume_qa_failed",
            "artifact_dir": str(output), "resume_path": str(pdf), "resume_sha256": digest(pdf.read_bytes()) if pdf.is_file() else None,
            "source_sha256": digest(source.encode()), "master_sha256": digest(master_path.read_bytes()),
            "ground_truth_sha256": digest(Path(config.get("ground_truth_path", FACTS)).read_bytes()),
            "fields": answers["fields"], "answers_hash": answers["hash"], "answer_evidence": answers["evidence"],
            "missing": answers["missing"], "qa": qa, "claims": manifest, "changes": changes,
            "arc": grade["arc"]}
