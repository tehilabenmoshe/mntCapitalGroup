"""Daily hi-tech job finder, emailed as a ranked digest.

Pulls open roles from:
  * Israeli companies' official ATS boards (Greenhouse / Lever public JSON) —
    listed in config/companies.json.
  * Free remote-job aggregators (Remotive, Arbeitnow) for remote roles that
    accept candidates from Israel / worldwide.

It keeps only entry / junior Full-Stack, Frontend and AI roles that match the
candidate's profile, scores each by fit against her stack, de-duplicates
against jobs already seen in a previous run (state/previous_job_ids.json), and
emails ONLY the new matches. It never applies to anything — a human reviews the
digest and applies. All data sources are free and have no request quota, so
(unlike the listings agent) there is no monthly cap here.
"""

import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# --- Email config (reuses the same Gmail secrets as the listings agent) -------
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
# Where the job digest is delivered. Defaults to Tehila's inbox; override with
# the JOBS_EMAIL_TO (or ALERT_EMAIL_TO) secret if you ever want it elsewhere.
ALERT_EMAIL_TO = (
    os.environ.get("JOBS_EMAIL_TO")
    or os.environ.get("ALERT_EMAIL_TO")
    or "bmtehila@gmail.com"
)

# --- Tunables (all overridable via env / GitHub Variables) --------------------
INCLUDE_ISRAEL = os.environ.get("INCLUDE_ISRAEL", "true").lower() != "false"
INCLUDE_REMOTE = os.environ.get("INCLUDE_REMOTE", "true").lower() != "false"
MIN_SCORE = int(os.environ.get("MIN_SCORE") or "2")
MAX_EMAIL_JOBS = int(os.environ.get("MAX_EMAIL_JOBS") or "40")
REQUEST_TIMEOUT = 25

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "previous_job_ids.json"
COMPANIES_FILE = ROOT / "config" / "companies.json"
RESUME_PATH = ROOT / "assets" / "Tehila_Michaeli_CV.pdf"

# Attach the CV PDF to the digest email (so it's ready to forward/upload).
ATTACH_RESUME = os.environ.get("ATTACH_RESUME", "true").lower() != "false"

# Cover letters: if ANTHROPIC_API_KEY is set, Claude writes a tailored letter per
# job (for the top N by score). Without a key, we fall back to a static template.
# Haiku is chosen over Opus deliberately: letters are short and run daily at
# volume, so cost matters — override with COVER_LETTER_MODEL if you prefer.
COVER_LETTER_MODEL = os.environ.get("COVER_LETTER_MODEL", "claude-haiku-4-5")
COVER_LETTER_MAX = int(os.environ.get("COVER_LETTER_MAX") or "10")

# A compact profile Claude uses to tailor each cover letter.
RESUME_SUMMARY = (
    "Tehila Michaeli (Ben-Moshe) — Full-Stack Developer, B.Sc. Software "
    "Engineering (specialization in Cyber & AI), GPA 87. Stack: React, Node.js, "
    "Express, ASP.NET Core (C#), REST APIs, SQL, TypeScript/JavaScript/Python. "
    "Modern AI experience: RAG, MCP, AI agents, tool calling, prompt engineering. "
    "Experience: CRM developer/implementer at Digitize (Zoho CRM, Deluge, "
    "integrations), an independent client CRM + website project (React/Node/AI), "
    "and academic projects (a client-server flight management system in "
    "Python/ASP.NET Core, and cyberattack detection with NLP/ML). Entry-level / "
    "junior candidate based in Israel."
)

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (job-finder-agent)", "Accept": "application/json"}

# --- Matching vocabulary ------------------------------------------------------
# Role keywords: a job's TITLE must hit at least one of these to be considered.
ROLE_KEYWORDS = [
    "full stack", "full-stack", "fullstack",
    "frontend", "front-end", "front end", "front end developer",
    "react", "angular", "vue", "web developer", "ui engineer", "client-side",
    "software engineer", "software developer", "software development engineer",
    "backend", "back-end", "node", "node.js", ".net", "c#",
    "ai engineer", "ai developer", "ml engineer", "machine learning",
    "llm", "genai", "generative ai", "nlp", "applied ai", "prompt",
    "junior developer", "associate developer", "graduate",
]

# Words that mark a role as too senior — exclude if any appears in the TITLE.
SENIORITY_EXCLUDE = [
    "senior", "sr.", "sr ", "staff", "principal", "lead", "team lead",
    "architect", "manager", "head of", "director", "vp ", "expert", "iii",
]

# Words that strongly signal the role is junior/entry friendly.
JUNIOR_SIGNALS = [
    "junior", "entry level", "entry-level", "graduate", "new grad",
    "associate", "student", "0-2 years", "1-2 years", "1-3 years",
    "up to 2 years", "up to 3 years", "no experience", "early career",
]

# The candidate's stack — used to compute "why it fits" and part of the score.
HER_STACK = [
    "react", "node", "node.js", "express", "typescript", "javascript",
    "python", "c#", "asp.net", ".net", "sql", "rest", "api",
    "rag", "mcp", "llm", "ai agent", "prompt", "cyber", "security", "zoho",
]

# Locations that count as "in Israel" on a global company's board.
ISRAEL_LOCATIONS = [
    "israel", "tel aviv", "tel-aviv", "herzliya", "herzeliya", "jerusalem",
    "haifa", "ra'anana", "raanana", "netanya", "petah tikva", "petach tikva",
    "beer sheva", "be'er sheva", "ramat gan", "yokneam", "yoqneam", "caesarea",
    "rehovot", "or yehuda", "airport city", "kiryat", "givatayim",
]

# Remote-location strings that we accept (candidate can work from Israel).
REMOTE_OK_LOCATIONS = [
    "worldwide", "anywhere", "global", "emea", "europe", "israel",
    "middle east", "remote",
]


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def contains_any(haystack, needles):
    h = haystack.lower()
    return any(n in h for n in needles)


def min_years_required(text):
    """Best-effort: smallest 'N+ years' figure mentioned. None if not found."""
    years = [int(m) for m in re.findall(r"(\d{1,2})\+?\s*(?:years|yrs)", text.lower())]
    return min(years) if years else None


# =============================================================================
# Source fetchers — each returns a list of normalized job dicts, or [] on error.
# Normalized job: {id, source, title, company, location, url, text, remote}
# =============================================================================

def _safe_get(url):
    r = requests.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_greenhouse(token):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    jobs = []
    for j in _safe_get(url).get("jobs", []):
        jobs.append({
            "id": f"gh:{token}:{j.get('id')}",
            "source": f"Greenhouse/{token}",
            "title": j.get("title", ""),
            "company": (j.get("company_name") or token).strip(),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "text": strip_html(j.get("content", "")),
            "remote": False,
        })
    return jobs


def fetch_lever(token):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    jobs = []
    for j in _safe_get(url):
        cats = j.get("categories") or {}
        jobs.append({
            "id": f"lever:{token}:{j.get('id')}",
            "source": f"Lever/{token}",
            "title": j.get("text", ""),
            "company": token,
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "text": strip_html(j.get("descriptionPlain") or j.get("description", "")),
            "remote": (cats.get("commitment", "").lower() == "remote"),
        })
    return jobs


def fetch_remotive():
    url = "https://remotive.com/api/remote-jobs?category=software-dev"
    jobs = []
    for j in _safe_get(url).get("jobs", []):
        jobs.append({
            "id": f"remotive:{j.get('id')}",
            "source": "Remotive (remote)",
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("candidate_required_location", ""),
            "url": j.get("url", ""),
            "text": strip_html(j.get("description", "")),
            "remote": True,
        })
    return jobs


def fetch_arbeitnow():
    url = "https://www.arbeitnow.com/api/job-board-api"
    jobs = []
    for j in _safe_get(url).get("data", []):
        jobs.append({
            "id": f"arbeitnow:{j.get('slug')}",
            "source": "Arbeitnow (remote)",
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "text": strip_html(j.get("description", "")),
            "remote": bool(j.get("remote")),
        })
    return jobs


def collect_all_jobs():
    """Fetch from every source; a failing source is logged and skipped."""
    companies = json.loads(COMPANIES_FILE.read_text(encoding="utf-8"))
    all_jobs = []

    if INCLUDE_ISRAEL:
        for token in companies.get("greenhouse", []):
            try:
                got = fetch_greenhouse(token)
                all_jobs += got
                print(f"  Greenhouse/{token}: {len(got)} jobs")
            except Exception as exc:  # noqa: BLE001 - one bad board must not kill the run
                print(f"  Greenhouse/{token}: SKIPPED ({exc})", file=sys.stderr)
            time.sleep(0.3)
        for token in companies.get("lever", []):
            try:
                got = fetch_lever(token)
                all_jobs += got
                print(f"  Lever/{token}: {len(got)} jobs")
            except Exception as exc:  # noqa: BLE001
                print(f"  Lever/{token}: SKIPPED ({exc})", file=sys.stderr)
            time.sleep(0.3)

    if INCLUDE_REMOTE:
        for name, fn in (("Remotive", fetch_remotive), ("Arbeitnow", fetch_arbeitnow)):
            try:
                got = fn()
                all_jobs += got
                print(f"  {name}: {len(got)} jobs")
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}: SKIPPED ({exc})", file=sys.stderr)

    return all_jobs


# =============================================================================
# Filtering, scoring, and "why it fits"
# =============================================================================

def location_ok(job):
    loc = job.get("location", "") or ""
    if job.get("remote") and contains_any(loc or "remote", REMOTE_OK_LOCATIONS):
        return INCLUDE_REMOTE
    if INCLUDE_ISRAEL and contains_any(loc, ISRAEL_LOCATIONS):
        return True
    if INCLUDE_REMOTE and job.get("remote"):
        return True
    # Aggregator jobs without a clear location but flagged remote already handled.
    return False


def matched_stack(job):
    blob = (job["title"] + " " + job["text"]).lower()
    seen, out = set(), []
    for kw in HER_STACK:
        if kw in blob and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def evaluate(job):
    """Return (keep: bool, score: int, why: list[str])."""
    title = job["title"].lower()

    # Must look like a relevant role by title.
    if not contains_any(title, ROLE_KEYWORDS):
        return False, 0, []

    # Drop clearly-senior roles.
    if contains_any(title, SENIORITY_EXCLUDE):
        return False, 0, []

    blob = title + " " + job["text"].lower()
    junior = contains_any(blob, JUNIOR_SIGNALS)
    years = min_years_required(job["text"])
    # Too much required experience for an entry-level candidate.
    if years is not None and years >= 4 and not junior:
        return False, 0, []

    if not location_ok(job):
        return False, 0, []

    stack = matched_stack(job)
    score = 2 * sum(1 for k in ROLE_KEYWORDS if k in title)
    score += len(stack)
    if junior:
        score += 4
    if years is not None and years <= 2:
        score += 2

    why = []
    if junior:
        why.append("מיועד ל-Junior/Entry")
    if stack:
        why.append("סטאק תואם: " + ", ".join(stack[:6]))
    if years is not None:
        why.append(f"דורש ~{years}+ שנות ניסיון")
    return score >= MIN_SCORE, score, why


# =============================================================================
# State
# =============================================================================

def load_seen_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen_ids(ids):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


# =============================================================================
# Email
# =============================================================================

COVER_LETTER_TEMPLATE = (
    "Hi {company} team,\n\n"
    "I'm a Full-Stack Developer with a B.Sc. in Software Engineering "
    "(specializing in Cyber & AI) and hands-on experience with React, Node.js, "
    "ASP.NET Core, REST APIs and SQL, plus modern AI work (RAG, AI agents, "
    "prompt engineering). The {role} role is a great fit for my background and "
    "I'd love to contribute. My CV is attached.\n\n"
    "Best,\nTehila Michaeli"
)


def generate_cover_letters(jobs):
    """Return {job_id: cover_letter_text} for the top jobs.

    Uses Claude when ANTHROPIC_API_KEY is available; otherwise fills the static
    template. Any per-job failure falls back to the template so the digest is
    never blocked by the LLM.
    """
    top = jobs[:COVER_LETTER_MAX]
    letters = {}

    def fallback(job):
        return COVER_LETTER_TEMPLATE.format(company=job["company"], role=job["title"])

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {j["id"]: fallback(j) for j in top}

    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - SDK missing / bad key -> use template
        print(f"Claude unavailable ({exc}); using template cover letters.", file=sys.stderr)
        return {j["id"]: fallback(j) for j in top}

    system = (
        "You write short, specific, professional cover letters for a junior "
        "software developer applying to tech jobs. 4-6 sentences, first person, "
        "no clichés, concrete about the stack overlap. Sign off as 'Tehila "
        "Michaeli'. Output only the letter text."
    )
    for job in top:
        try:
            msg = client.messages.create(
                model=COVER_LETTER_MODEL,
                max_tokens=400,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Candidate profile:\n{RESUME_SUMMARY}\n\n"
                        f"Job title: {job['title']}\n"
                        f"Company: {job['company']}\n"
                        f"Location: {job['location'] or 'Remote'}\n"
                        f"Job description (excerpt):\n{job['text'][:1500]}\n\n"
                        "Write the tailored cover letter."
                    ),
                }],
            )
            letters[job["id"]] = next(
                (b.text for b in msg.content if b.type == "text"), fallback(job)
            ).strip()
        except Exception as exc:  # noqa: BLE001 - never let one job break the run
            print(f"Cover letter failed for {job['id']} ({exc}); using template.", file=sys.stderr)
            letters[job["id"]] = fallback(job)
    return letters


def build_email(new_jobs, letters):
    rows_html, lines_text = [], []
    for job in new_jobs:
        why = " · ".join(job["_why"]) if job["_why"] else "התאמה לפי תפקיד"
        rows_html.append(
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'><a href='{html.escape(job['url'])}'>{html.escape(job['title'])}</a></td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{html.escape(job['company'])}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{html.escape(job['location'] or ('Remote' if job['remote'] else '-'))}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{job['_score']}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{html.escape(why)}</td>"
            "</tr>"
        )
        lines_text.append(
            f"[{job['_score']}] {job['title']} — {job['company']} "
            f"({job['location'] or ('Remote' if job['remote'] else '-')})\n"
            f"    {why}\n    {job['url']}\n"
        )

    # Per-job tailored cover letters (top jobs only).
    letters_html, letters_text = [], []
    for job in new_jobs:
        letter = letters.get(job["id"])
        if not letter:
            continue
        header = f"{job['title']} — {job['company']}"
        letters_html.append(
            f"<h4 style='margin:16px 0 4px' dir='ltr'>{html.escape(header)}</h4>"
            f"<pre dir='ltr' style='white-space:pre-wrap;background:#fafafa;"
            f"padding:12px;border:1px solid #eee;font-family:inherit'>"
            f"{html.escape(letter)}</pre>"
        )
        letters_text.append(f"=== {header} ===\n{letter}\n")

    letters_section_html = ""
    if letters_html:
        letters_section_html = (
            "<hr><h3>✍️ טיוטות Cover Letter מותאמות (למשרות המובילות)</h3>"
            + "".join(letters_html)
        )

    html_body = f"""
    <html><body dir="rtl" style="font-family:sans-serif">
    <h2>🚀 {len(new_jobs)} משרות חדשות שמתאימות לך</h2>
    <p>מדורגות לפי ציון התאמה (גבוה = מתאים יותר). לחצי על שם המשרה כדי להגיש בעצמך.
    קובץ קורות החיים מצורף למייל.</p>
    <table style="border-collapse:collapse;width:100%">
      <tr style="background:#f0f0f0;text-align:right">
        <th style="padding:8px">משרה</th><th style="padding:8px">חברה</th>
        <th style="padding:8px">מיקום</th><th style="padding:8px">ציון</th>
        <th style="padding:8px">למה מתאים</th>
      </tr>
      {''.join(rows_html)}
    </table>
    {letters_section_html}
    </body></html>
    """
    text_body = (
        f"{len(new_jobs)} משרות חדשות שמתאימות לך (מדורגות לפי ציון):\n\n"
        + "\n".join(lines_text)
        + ("\n\n--- Cover letters ---\n" + "\n".join(letters_text) if letters_text else "")
    )
    return text_body, html_body


def send_email(subject, text_body, html_body):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_EMAIL_TO
    # Body must be a nested 'alternative' part so the attachment sits beside it.
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(text_body, "plain", "utf-8"))
    body.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(body)

    if ATTACH_RESUME and RESUME_PATH.exists():
        attachment = MIMEApplication(RESUME_PATH.read_bytes(), _subtype="pdf")
        attachment.add_header(
            "Content-Disposition", "attachment", filename=RESUME_PATH.name
        )
        msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [ALERT_EMAIL_TO], msg.as_string())


# =============================================================================
# Main
# =============================================================================

def select_matches(all_jobs):
    matches = []
    for job in all_jobs:
        keep, score, why = evaluate(job)
        if keep:
            job["_score"], job["_why"] = score, why
            matches.append(job)
    # Dedupe by id (aggregators can repeat), keep highest score.
    best = {}
    for job in matches:
        if job["id"] not in best or job["_score"] > best[job["id"]]["_score"]:
            best[job["id"]] = job
    return sorted(best.values(), key=lambda j: j["_score"], reverse=True)


def main():
    dry_run = "--dry-run" in sys.argv
    print("Collecting jobs from all sources...")
    all_jobs = collect_all_jobs()
    print(f"Fetched {len(all_jobs)} raw postings.")

    matches = select_matches(all_jobs)
    print(f"{len(matches)} match the junior FS/FE/AI + location filters.")

    seen = load_seen_ids()
    new_jobs = [j for j in matches if j["id"] not in seen]
    print(f"{len(new_jobs)} are NEW since the last run.")

    if dry_run:
        for j in new_jobs[:MAX_EMAIL_JOBS]:
            print(f"  [{j['_score']}] {j['title']} — {j['company']} ({j['location'] or 'Remote'})")
        print("(dry-run: no email sent, state not updated)")
        return

    if new_jobs:
        to_send = new_jobs[:MAX_EMAIL_JOBS]
        letters = generate_cover_letters(to_send)
        subject = f"🚀 {len(new_jobs)} משרות Junior חדשות שמתאימות לך"
        text_body, html_body = build_email(to_send, letters)
        send_email(subject, text_body, html_body)
        print(f"Email sent with {len(to_send)} jobs (of {len(new_jobs)} new), "
              f"{len(letters)} cover letters.")
    else:
        print("No new matching jobs, no email sent.")

    # Persist every id we saw as a current match, so next run only alerts on new.
    save_seen_ids(seen | {j["id"] for j in matches})


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        raise
