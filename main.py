import os
import sys
import time
import html
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import requests

# --------------------
# Config & constants
# --------------------
load_dotenv()

PETFINDER_CLIENT_ID = os.getenv("PETFINDER_CLIENT_ID", "").strip()
PETFINDER_CLIENT_SECRET = os.getenv("PETFINDER_CLIENT_SECRET", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER).strip()
SENDER_NAME = os.getenv("SENDER_NAME", "Dog Digest").strip()
RECIPIENTS = [r.strip() for r in os.getenv("RECIPIENTS", "adriennedanaross@gmail.com,hi@samshap.com").split(",") if r.strip()]

DEFAULT_ZIPS = ["08401", "11211", "19003"]
ZIP_CODES = [z.strip() for z in os.getenv("ZIP_CODES", "").split(",") if z.strip()] or DEFAULT_ZIPS
DISTANCE_MILES = int(os.getenv("DISTANCE_MILES", "100"))

# Breed exclusions (case-insensitive substring match)
EXCLUDED_BREEDS = {
    "Husky",
    "Coonhound",
    "Pit Bull",
    "Jack Russell Terrier",
    "German Shepherd",
    "Carolina Dog Mix",
    "Bull Terrier",
    "Chihuahua",
    "Rhodesian Ridgeback",
    "Rottweiler",
    "English Bulldog",
    "American Staffordshire Terrier",
}

PETFINDER_TOKEN_URL = "https://api.petfinder.com/v2/oauth2/token"
PETFINDER_ANIMALS_URL = "https://api.petfinder.com/v2/animals"

# Only consider last 24h
NOW_UTC = datetime.now(timezone.utc)
CUTOFF_UTC = NOW_UTC - timedelta(hours=24)

# --------------------
# Utilities
# --------------------
def get_token() -> str:
    resp = requests.post(
        PETFINDER_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": PETFINDER_CLIENT_ID,
            "client_secret": PETFINDER_CLIENT_SECRET,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def safe_lower(s):
    return s.lower() if isinstance(s, str) else ""

def breed_excluded(breeds_obj: dict) -> bool:
    # Check primary, secondary, mixed/unknown strings for excluded substrings
    names = []
    if isinstance(breeds_obj, dict):
        for key in ("primary", "secondary"):
            val = breeds_obj.get(key)
            if isinstance(val, str) and val.strip():
                names.append(val.strip())
        # Some entries may have "mixed" flags without explicit names; nothing to check there.
    text = " ".join(names)
    low = text.lower()
    for banned in EXCLUDED_BREEDS:
        if banned.lower() in low:
            return True
    return False

def parse_dt(dt_str: str):
    # Petfinder returns ISO8601 with timezone, e.g. "2025-09-18T04:25:04+00:00"
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None

def within_24_hours(published_at_str: str) -> bool:
    dt = parse_dt(published_at_str)
    return bool(dt and dt >= CUTOFF_UTC)

def collect_animals_for_zip(session, token: str, zip_code: str):
    results = []
    page = 1
    headers = {"Authorization": f"Bearer {token}"}
    # We’ll paginate until no results or oldest page is beyond cutoff
    while True:
        params = {
            "type": "dog",
            "status": "adoptable",
            "location": zip_code,
            "distance": DISTANCE_MILES,
            "age": "young,puppy",
            "sort": "recent",       # most recent published first
            "limit": "100",
            "page": str(page),
        }
        r = session.get(PETFINDER_ANIMALS_URL, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        animals = payload.get("animals", []) or []
        if not animals:
            break

        # If the last animal on the page is older than cutoff, we still need to scan current page fully,
        # but can break after this page.
        last_published = parse_dt(animals[-1].get("published_at", "")) if animals else None
        results.extend(animals)

        pagination = payload.get("pagination") or {}
        total_pages = pagination.get("total_pages") or page
        if page >= total_pages:
            break
        if last_published and last_published < CUTOFF_UTC:
            # Older than 24h; next pages will be even older
            break

        page += 1
        time.sleep(0.3)  # be polite to API
    return results

def fetch_all_animals():
    token = get_token()
    all_animals = {}
    with requests.Session() as session:
        for z in ZIP_CODES:
            animals = collect_animals_for_zip(session, token, z)
            for a in animals:
                # Filter by published within 24h immediately
                if not within_24_hours(a.get("published_at", "")):
                    continue
                # Exclude breeds per rules
                if breed_excluded(a.get("breeds", {}) or {}):
                    continue
                # De-duplicate by id
                aid = a.get("id")
                if aid is not None and aid not in all_animals:
                    all_animals[aid] = a
    # Sort by published_at desc
    sorted_animals = sorted(
        all_animals.values(),
        key=lambda x: parse_dt(x.get("published_at", "")) or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )
    return sorted_animals

def build_html_table(animals):
    # Headers required by spec
    headers = [
        "Name",
        "Size",
        "Breeds",
        "Age",
        "Gender",
        "Description",
        "Videos",
        "Contact Email",
        "Contact Phone",
        "Published At",
        "URL",
    ]

    def join_breeds(b):
        parts = []
        if isinstance(b, dict):
            for k in ("primary", "secondary"):
                v = b.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            # Mixed breeds might be indicated by boolean flags; names already captured above.
        return ", ".join(parts) if parts else ""

    rows_html = []
    for a in animals:
        name = a.get("name", "")
        size = a.get("size", "")
        breeds = join_breeds(a.get("breeds", {}) or {})
        age = a.get("age", "")
        gender = a.get("gender", "")
        desc = a.get("description", "") or ""
        # Tidy description to a reasonable size; HTML-escape
        desc = html.escape(" ".join(desc.split()))[:600]

        # Videos: Petfinder uses array; elements may have "embed" or "url"
        vids = a.get("videos", []) or []
        video_links = []
        for v in vids:
            url = None
            if isinstance(v, dict):
                url = v.get("url") or v.get("embed")
            elif isinstance(v, str):
                url = v
            if url:
                esc = html.escape(url)
                video_links.append(f'<a href="{esc}">video</a>')
        videos_cell = ", ".join(video_links)

        contact = a.get("contact", {}) or {}
        contact_email = contact.get("email", "") or ""
        contact_phone = contact.get("phone", "") or ""

        pub = parse_dt(a.get("published_at", "")) or None
        # Display in US Eastern (New York); keep explicit timezone in string
        try:
            # Python 3.9+ zoneinfo alternative without external deps:
            # GitHub runners default to UTC; just show ISO string w/ offset from API (already TZ-aware).
            published_at_str = pub.astimezone(timezone.utc).isoformat() if pub else ""
        except Exception:
            published_at_str = a.get("published_at", "")

        url = a.get("url", "") or ""

        cells = [
            html.escape(name),
            html.escape(size or ""),
            html.escape(breeds or ""),
            html.escape(age or ""),
            html.escape(gender or ""),
            desc,
            videos_cell,
            html.escape(contact_email),
            html.escape(contact_phone),
            html.escape(published_at_str),
            f'<a href="{html.escape(url)}">Link</a>' if url else "",
        ]
        row = "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        rows_html.append(row)

    table = f"""
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.3; width:100%;">
      <thead style="background:#f5f5f5;">
        <tr>{"".join(f"<th style='text-align:left;'>{h}</th>" for h in headers)}</tr>
      </thead>
      <tbody>
        {''.join(rows_html) if rows_html else '<tr><td colspan="11">No matching dogs in the last 24 hours.</td></tr>'}
      </tbody>
    </table>
    """
    return table

def send_email(subject: str, html_body: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = ", ".join(RECIPIENTS)

    # Plain text fallback
    msg.set_content("Your email client does not support HTML. Please open in an HTML-capable email client.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def main():
    # Basic validations
    missing = []
    for k, v in [
        ("PETFINDER_CLIENT_ID", PETFINDER_CLIENT_ID),
        ("PETFINDER_CLIENT_SECRET", PETFINDER_CLIENT_SECRET),
        ("SMTP_HOST", SMTP_HOST),
        ("SMTP_PORT", SMTP_PORT),
        ("SMTP_USER", SMTP_USER),
        ("SMTP_PASS", SMTP_PASS),
        ("SENDER_EMAIL", SENDER_EMAIL),
    ]:
        if not v:
            missing.append(k)
    if missing:
        print(f"Missing required configuration: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    animals = fetch_all_animals()
    html_table = build_html_table(animals)
    subject = f"Dog Digest: {len(animals)} matches in last 24h (run @ {NOW_UTC.isoformat()})"
    send_email(subject, f"<div>{html_table}</div>")
    print(f"Sent digest with {len(animals)} dogs to: {', '.join(RECIPIENTS)}")

if __name__ == "__main__":
    main()
.github/workflows/dog-digest.yml
yaml
Copy code
name: Dog Digest (every 6 hours)

on:
  schedule:
    - cron: "0 */6 * * *"   # every 6 hours, UTC
  workflow_dispatch:       # manual run if needed

jobs:
  run-digest:
    runs-on: ubuntu-latest
    steps:
      - name: Check out
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run script
        env:
          PETFINDER_CLIENT_ID: ${{ secrets.PETFINDER_CLIENT_ID }}
          PETFINDER_CLIENT_SECRET: ${{ secrets.PETFINDER_CLIENT_SECRET }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          SENDER_EMAIL: ${{ secrets.SENDER_EMAIL }}
          SENDER_NAME: ${{ secrets.SENDER_NAME }}
          RECIPIENTS: ${{ secrets.RECIPIENTS }}
          ZIP_CODES: ${{ secrets.ZIP_CODES }}
          DISTANCE_MILES: ${{ secrets.DISTANCE_MILES }}
        run: |
          python main.py
