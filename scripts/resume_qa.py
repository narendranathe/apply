#!/usr/bin/env python3
"""resume_qa.py - the whole verification chain in one file. Paste once per session.

  python3 resume_qa.py lint   resume.tex          # live % $ _ & # -> silent LaTeX killers
  python3 resume_qa.py budget resume.tex          # character budgets, pre-compile
  python3 resume_qa.py order  resume.tex          # Experience reverse-chronological (most recent first)
  python3 resume_qa.py layout resume.pdf [--pages N]
  python3 resume_qa.py all    resume.tex [--pages N]   # lint -> budget -> order -> pdflatex x2 -> layout

Thresholds are CALIBRATED against v8preamble.tex geometry (10pt, 0.33in sides, itemize
leftmargin 0.14in). Do not substitute numbers from any other spec."""
import os, re, subprocess, sys

ONE_LO, ONE_HI, TWO_LO, TWO_HI, TAIL_MIN = 105, 129, 200, 245, 55
SUM_2_HI, SUM_3_LO, SUM_3_HI = 268, 375, 400
PAGE_H, PAGE_W = 792.0, 612.0
M_TOP, M_BOT, M_SIDE = 0.28 * 72, 0.30 * 72, 0.33 * 72
TEXT_H = PAGE_H - M_TOP - M_BOT
ONEPAGE_LO, ONEPAGE_HI, FULLPAGE_LO, LAST_LO = 92.0, 99.0, 90.0, 60.0
BULLET = "\u2022"
SECTIONS = ("Technical Skills", "Experience", "Projects", "Education", "Selected")

# ---------- lint ----------
LIVE = {"%": "truncates the rest of this line out of the PDF, silently",
        "_": r"subscript operator - use \_",
        "&": r"alignment tab - use \&",
        "#": r"macro parameter - use \#"}

def _live(s, ch):
    hits = []
    for i, c in enumerate(s):
        if c != ch:
            continue
        b, j = 0, i - 1
        while j >= 0 and s[j] == "\\":
            b += 1; j -= 1
        if b % 2 == 0:
            hits.append(i)
    return hits

def lint(path):
    bad = []
    for n, raw in enumerate(open(path), 1):
        s = raw.rstrip("\n")
        if s.lstrip().startswith("%"):
            continue
        for ch, why in LIVE.items():
            if _live(s, ch):
                bad.append((n, f"live {ch!r} - {why}", s.strip()[:70]))
        if len(_live(s, "$")) % 2:
            bad.append((n, "odd number of live '$' - math mode leak", s.strip()[:70]))
        if "\\\\$" in s:
            bad.append((n, r"'\\$' is a line break then math mode, not an escaped dollar",
                        s.strip()[:70]))
    for n, why, txt in bad:
        print(f"  line {n}: {why}\n    {txt}")
    print("LINT PASS" if not bad else f"LINT FAIL: {len(bad)} issue(s)")
    return not bad

# ---------- budget ----------
def plain(s):
    s = re.sub(r'\\(textbf|emph|textit)\\{([^{}]*)\\}', r'\2', s)
    s = re.sub(r'\\[a-zA-Z]+', '', s)
    s = s.replace('$|$', '|').replace('\\&', '&').replace('\\$', '$').replace('\\%', '%')
    return re.sub(r'[{}]', '', s).strip()

def budget(path, verbose=True):
    src = open(path).read()
    bad = 0
    for m in re.finditer(r'\\summary\{(.*)\}', src):
        c = len(plain(m.group(1)))
        ok = c <= SUM_2_HI or SUM_3_LO <= c <= SUM_3_HI
        bad += not ok
        if verbose or not ok:
            print(f"SUMMARY {c:4d}  {'OK' if ok else f'BAD -> <={SUM_2_HI} or {SUM_3_LO}-{SUM_3_HI}'}")
    for m in re.finditer(r'\\rolecontext\{(.*)\}', src):
        c = len(plain(m.group(1)))
        ok = ONE_LO <= c <= ONE_HI
        bad += not ok
        if verbose or not ok:
            print(f"CONTEXT {c:4d}  {'OK' if ok else f'BAD -> {ONE_LO}-{ONE_HI}, one line'}")
    for line in src.splitlines():
        if line.strip().startswith(r'\item'):
            c = len(plain(line.strip()[5:]))
            ok = ONE_LO <= c <= ONE_HI or TWO_LO <= c <= TWO_HI
            bad += not ok
            if verbose or not ok:
                v = 'OK 1L' if c <= ONE_HI else 'OK 2L' if ok else f'BAD -> <={ONE_HI} or {TWO_LO}-{TWO_HI}'
                print(f"ITEM    {c:4d}  {v:28s} {plain(line)[:44]}")
    print("BUDGET PASS" if not bad else f"BUDGET FAIL: {bad} out of budget")
    return not bad

# ---------- layout ----------
def _lines(page, tol=4.0):
    # Bold and regular glyphs on one visual baseline can differ by over 2.2 pt.
    ws = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    ws.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
    out = []
    for w in ws:
        if out and abs(w["top"] - out[-1]["top"]) <= tol:
            out[-1]["w"].append(w)
        else:
            out.append({"top": w["top"], "w": [w]})
    for l in out:
        l["text"] = " ".join(x["text"] for x in l["w"])
        l["x0"] = min(x["x0"] for x in l["w"])
        l["x1"] = max(x["x1"] for x in l["w"])
        l["bottom"] = max(x["bottom"] for x in l["w"])
    return out

def layout(path, want_pages=None):
    import pdfplumber
    fails, warns = [], []
    txt = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout
    if "(cid:" in txt:
        fails.append("BROKEN GLYPH MAP: '(cid:' in extraction. ATS reads garbage. "
                     "Fix: apt-get install -y lmodern, recompile.")
    with pdfplumber.open(path) as pdf:
        npages = len(pdf.pages)
        if want_pages and npages != want_pages:
            fails.append(f"PAGES={npages}, wanted {want_pages}")
        fills, nb = [], 0
        for pi, page in enumerate(pdf.pages, 1):
            L = _lines(page)
            if not L:
                fails.append(f"page {pi}: no text"); fills.append(0.0); continue
            fills.append((max(l["bottom"] for l in L) - M_TOP) / TEXT_H * 100)
            left, right = min(l["x0"] for l in L), PAGE_W - max(l["x1"] for l in L)
            for nm, v in (("left", left), ("right", right)):
                if v < M_SIDE - 3.0:      # microtype protrusion hangs glyphs ~2pt
                    fails.append(f"page {pi}: {nm} margin {v:.1f}pt breaches {M_SIDE:.1f}pt")
            if pi == 1:
                first = next((i for i, l in enumerate(L)
                              if any(l["text"].startswith(s) for s in SECTIONS)), None)
                if first and first > 2:
                    # The header may wrap its contact details onto a third line.
                    # Summary text starts at the left text margin; header text is centered.
                    summary_start = next((i for i, line in enumerate(L[1:first], 1)
                                          if line["x0"] <= M_SIDE + 3), 2)
                    s = L[summary_start:first]
                    sc = len(" ".join(l["text"] for l in s))
                    if len(s) > 3:
                        fails.append(f"SUMMARY {len(s)} lines ({sc}c) -> max 3")
                    elif len(s) == 3 and sc < SUM_3_LO:
                        fails.append(f"SUMMARY stub 3rd line ({sc}c) -> grow to "
                                     f"{SUM_3_LO}-{SUM_3_HI}c or cut to <={SUM_2_HI}c")
            blocks, cur = [], None
            for l in L:
                if l["w"][0]["text"].startswith(BULLET):
                    if cur: blocks.append(cur)
                    cur = [l]
                elif cur is not None and l["x0"] > M_SIDE + 6 and (l["top"] - cur[-1]["bottom"]) < 6:
                    cur.append(l)
                else:
                    if cur: blocks.append(cur); cur = None
            if cur: blocks.append(cur)
            nb += len(blocks)
            for b in blocks:
                t = " ".join(x["text"] for x in b).lstrip(BULLET).strip()
                c, nl = len(t), len(b)
                if nl >= 3:
                    fails.append(f"p{pi} 3-LINE BULLET ({c}c): {t[:50]}...")
                elif nl == 2 and len(b[1]["text"]) < TAIL_MIN:
                    fails.append(f"p{pi} DEAD ZONE, tail {len(b[1]['text'])}c ({c}c) -> grow to "
                                 f"{TWO_LO}-{TWO_HI}c or cut to <={SUM_2_HI}c: {t[:40]}...")
                elif nl == 1 and c < ONE_HI:
                    warns.append(f"p{pi} SHORT single line ({c}c): {t[:40]}...")
    log = os.path.splitext(path)[0] + ".log"
    over = 0
    if os.path.exists(log):
        over = len(re.findall(r"Overfull \\[hv]box", open(log, errors="ignore").read()))
        if over: fails.append(f"{over} OVERFULL box(es)")
    if fills:
        if len(fills) == 1:
            if not (ONEPAGE_LO <= fills[0] <= ONEPAGE_HI):
                fails.append(f"fill {fills[0]:.1f}% outside {ONEPAGE_LO}-{ONEPAGE_HI}%")
        else:
            for i, f in enumerate(fills[:-1], 1):
                if f < FULLPAGE_LO:
                    fails.append(f"PAGE {i} fill {f:.1f}% < {FULLPAGE_LO}% - forced break, or "
                                 f"content that should be pulled forward")
            if fills[-1] < LAST_LO:
                fails.append(f"LAST PAGE fill {fills[-1]:.1f}% < {LAST_LO}% -> Fill Order to earn "
                             f"it, or Cut Order back a page")
    print(f"pages={npages} fill={'/'.join(f'{f:.1f}%' for f in fills)} bullets={nb} "
          f"overfull={over} words={len(txt.split())}")
    for w in warns: print("  WARN:", w)
    for f in fails: print("  FAIL:", f)
    print("LAYOUT PASS" if not fails else "LAYOUT FAIL")
    return not fails

# ---------- driver ----------
# ---------- order ----------
ORDER_MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def _start_key(datestr):
    m = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{4})", datestr)
    if not m:
        return None
    mon = ORDER_MONTHS.get(m.group(1)[:3].lower())
    return (int(m.group(2)), mon) if mon else None

def order(path):
    # Naren's rule (2026-10-12): Experience roles must run strict reverse-chronological
    # (most recent first). Catches the 2026-10-12 defect where variants shipped
    # Zomato -> Udaan -> C2FO (oldest first) on page 2.
    src = open(path).read()
    roles = re.findall(r"\\roleheader\{[^}]*\}\{([^}]*)\}\{([^}]*)\}", src)
    if not roles:
        print("ORDER FAIL: no \\roleheader entries found"); return False
    keys = [(emp, dates.strip(), _start_key(dates.split("-")[0])) for emp, dates in roles]
    ok = True
    for (e1, d1, k1), (e2, d2, k2) in zip(keys, keys[1:]):
        if k1 is None or k2 is None or not (k1 > k2):
            print(f"  OUT OF ORDER: {e1} ({d1}) listed before {e2} ({d2})")
            ok = False
    seq = " > ".join(e for e, _, _ in keys)
    print(("ORDER PASS: " if ok else "ORDER FAIL: ") + seq)
    return ok

def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return 2
    cmd, path = a[0], a[1]
    pages = int(a[a.index("--pages") + 1]) if "--pages" in a else None
    if cmd == "lint":   return 0 if lint(path) else 1
    if cmd == "budget": return 0 if budget(path) else 1
    if cmd == "order":  return 0 if order(path) else 1
    if cmd == "layout": return 0 if layout(path, pages) else 1
    if cmd == "all":
        if not lint(path): return 1
        if not budget(path, verbose=False): return 1
        if not order(path): return 1
        for _ in range(2):
            subprocess.run(["pdflatex", "-interaction=nonstopmode", path],
                           capture_output=True)
        pdf = os.path.splitext(path)[0] + ".pdf"
        if not os.path.exists(pdf):
            print("COMPILE FAIL: no PDF produced. Read the .log."); return 1
        return 0 if layout(pdf, pages) else 1
    print(f"unknown command {cmd!r}"); return 2

if __name__ == "__main__":
    sys.exit(main())
