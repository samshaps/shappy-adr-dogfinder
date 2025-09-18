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

def getenv_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default

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
DISTANCE_MILES = getenv_int("DISTANCE_MILES", 100)

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
    names = []
    if isinstance(breeds_obj, dict):
        for key in ("primary", "secondary"):
            val = breeds_obj.get(key)
            if isinstance(val, str) and val.strip():
                names.append(val.strip())
    text = " ".join(names)
    low = text.lower()
    for banned in EXCLUDED_BREEDS:
        if banned.lower() in low:
            return True
    return False

def parse_dt(dt_str: str):
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
    while True:
        params = {
        "type": "dog",
        "status": "adoptable",
        "location": zip_code,
        "distance": DISTANCE_MILES,
        "age": "young,baby",  # Petfinder valid values: baby, young, adult, senior
        "sort": "recent",
        "limit": "100",
        "page": str(page),
}
        r = session.get(PETFINDER_ANIMALS_URL, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        animals = payload.get("animals", []) or []
        if not animals:
            break

        last_published = parse_dt(animals[-1].get("published_at", "")) if animals else None
        results.extend(animals)

        pagination = payload.get("pagination") or {}
        total_pages = pagination.get("total_pages") or page
        if page >= total_pages:
            break
        if last_published and last_published < CUTOFF_UTC:
            break

        page += 1
        time.sleep(0.3)
    return results

def fetch_all_animals():
    token = get_token()
    all_animals = {}
    with requests.Session() as session:
        for z in ZIP_CODES:
            animals = collect_animals_for_zip(session, token, z)
            for a in animals:
                if not within_24_hours(a.get("published_at", "")):
                    continue
                if breed_excluded(a.get("breeds", {}) or {}):
                    continue
                aid = a.get("id")
                if aid is not None and aid not in all_animals:
                    all_animals[aid] = a
    sorted_animals = sorted(
        all_animals.values(),
        key=lambda x: parse_dt(x.get("published_at", "")) or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )
    return sorted_animals

def build_html_table(animals):
    headers = [
        "Name","Size","Breeds","Age","Gender","Description","Videos",
        "Contact Email","Contact Phone","Published At","URL",
    ]

    def join_breeds(b):
        parts = []
        if isinstance(b, dict):
            for k in ("primary", "secondary"):
                v = b.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
        return ", ".join(parts) if parts else ""

    rows_html = []
    for a in animals:
        name = a.get("name", "")
        size = a.get("size", "")
        breeds = join_breeds(a.get("breeds", {}) or {})
        age = a.get("age", "")
        gender = a.get("gender", "")
        desc = a.get("description", "") or ""
        desc = html.escape(" ".join(desc.split()))[:600]

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
        try:
            published_at_str = pub.astimezone(timezone.utc).isoformat() if pub else ""
        except Exception:
            published_at_str = a.get("published_at", "")

        url = a.get("url", "") or ""

        cells = [
            html.escape(name),html.escape(size or ""),html.escape(breeds or ""),
            html.escape(age or ""),html.escape(gender or ""),desc,videos_cell,
            html.escape(contact_email),html.escape(contact_phone),
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
    msg.set_content("Your email client does not support HTML. Please open in an HTML-capable email client.")
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def main():
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
