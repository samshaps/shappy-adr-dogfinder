import os
import sys
import time
import html
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import requests
from zoneinfo import ZoneInfo
from openai import OpenAI
EASTERN = ZoneInfo("America/New_York")


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
RECIPIENTS = [r.strip() for r in os.getenv("RECIPIENTS", "hi@samshap.com").split(",") if r.strip()]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

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
    "Doberman Pinscher",
    "Great Pyrenees",
    "Boxer",
    "Hound",
    "American Bulldog"
    
}

PETFINDER_TOKEN_URL = "https://api.petfinder.com/v2/oauth2/token"
PETFINDER_ANIMALS_URL = "https://api.petfinder.com/v2/animals"

# Only consider last 24h
NOW_UTC = datetime.now(timezone.utc)
CUTOFF_UTC = NOW_UTC - timedelta(hours=6)

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

def to_eastern_str(dt: datetime) -> str:
    """Format a timezone-aware datetime into Eastern like 'Thu, Sep 18, 2025 12:35 PM EDT'."""
    if not dt:
        return ""
    local = dt.astimezone(EASTERN)
    return local.strftime("%a, %b %d, %Y %I:%M %p %Z")


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

def get_dog_preferences() -> str:
    """Define your dog preferences criteria for OpenAI analysis."""
    return """
    I'm looking for a dog with the following preferences:
    - Size: Medium-sized dog (25-75 lbs)
    - Age: Young adult (2 years old maximum) or puppy
    - Energy level: Moderate to high energy
    - Temperament: Friendly, social, good with families
    - Special considerations: Good with other dogs, house-trained preferred
    - Breed preferences: Mixed breeds welcome, avoid very high-maintenance breeds, avoid breeds that tend to be smaller
    - Personality: Playful, affectionate, trainable
    """

def analyze_dogs_with_openai(animals: list) -> str:
    """Use OpenAI to analyze dogs and generate top recommendations."""
    if not OPENAI_API_KEY:
        return "OpenAI API key not configured. Please add OPENAI_API_KEY to your environment variables."
    
    if not animals:
        return "No dogs available for analysis."
    
    try:
        # Initialize OpenAI client with explicit parameters to avoid proxy issues
        import httpx
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=30.0,
            http_client=httpx.Client(proxies=None)
        )
        
        # Prepare dog data for analysis
        dog_data = []
        for animal in animals[:20]:  # Limit to first 20 dogs to avoid token limits
            if not animal or not isinstance(animal, dict):
                continue
            dog_info = {
                "name": animal.get("name", "Unknown"),
                "breeds": f"{animal.get('breeds', {}).get('primary', '')} {animal.get('breeds', {}).get('secondary', '')}".strip(),
                "size": animal.get("size", "Unknown"),
                "age": animal.get("age", "Unknown"),
                "gender": animal.get("gender", "Unknown"),
                "description": str(animal.get("description", ""))[:500],  # Ensure string and limit length
                "url": animal.get("url", "")
            }
            dog_data.append(dog_info)
        
        if not dog_data:
            return "No valid dog data available for analysis."
        
        # Create prompt for OpenAI
        preferences = get_dog_preferences()
        prompt = f"""
        Based on the following dog preferences:
        {preferences}
        
        Analyze these {len(dog_data)} dogs and select the top 5 that best match the criteria. 
        For each selected dog, provide:
        1. Dog's name and breed
        2. Brief reason why this dog is a good match
        3. Any concerns or considerations
        
        Dog data:
        {str(dog_data)}
        
        Format your response as a clean HTML list with the top 5 dogs.
        """
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that analyzes dog adoption listings and provides recommendations based on specific criteria."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        # Robust error handling for OpenAI response
        if not response or not hasattr(response, 'choices') or not response.choices:
            return "OpenAI API returned an empty or invalid response."
        
        if not response.choices[0] or not hasattr(response.choices[0], 'message'):
            return "OpenAI API response missing message content."
        
        content = response.choices[0].message.content
        if not content:
            return "OpenAI API returned empty content."
        
        return content
        
    except Exception as e:
        return f"Error analyzing dogs with OpenAI: {str(e)}"

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
        try:
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
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 502:
                print(f"Petfinder API temporarily unavailable (502 error) for zip {zip_code}. Skipping this zip code.")
                break
            else:
                print(f"HTTP error {e.response.status_code} for zip {zip_code}. Skipping this zip code.")
                break
        except Exception as e:
            print(f"Error fetching data for zip {zip_code}: {str(e)}. Skipping this zip code.")
            break
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

def pick_photo(animal: dict):
    """Return (thumb_url, full_url) if available, else (None, None)."""
    p = animal.get("primary_photo_cropped") or {}
    thumb = p.get("small") or p.get("medium")
    full = p.get("full") or p.get("large") or thumb

    if not thumb or not full:
        # Fall back to the first item in photos[]
        photos = animal.get("photos") or []
        if photos:
            first = photos[0] or {}
            thumb = thumb or first.get("small") or first.get("medium")
            full = full or first.get("full") or first.get("large") or thumb

    return (thumb, full)


def build_html_table(animals, top_dogs_html=""):
    # Final column order:
    # Published At | Name | Image | Breeds | Size | Age | Gender | Description | URL
    headers = [
        "Published At", "Name", "Image", "Breeds", "Size", "Age", "Gender", "Description", "URL"
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
        # Core fields
        name = a.get("name", "") or ""
        size = a.get("size", "") or ""
        breeds = join_breeds(a.get("breeds", {}) or {})
        age = a.get("age", "") or ""
        gender = a.get("gender", "") or ""
        desc_raw = a.get("description", "") or ""
        desc = html.escape(" ".join(desc_raw.split()))[:600]

        # Image (thumbnail linking to full-size)
        thumb, full_img = pick_photo(a)
        image_cell = (
            f'<a href="{html.escape(full_img)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(thumb)}" alt="photo" style="height:64px;width:auto;border-radius:6px;"/></a>'
            if thumb else ""
        )

        # Published At (Eastern), now first column
        pub = parse_dt(a.get("published_at", "")) or None
        published_at_str = to_eastern_str(pub)

        # Listing URL
        url = a.get("url", "") or ""
        url_cell = f'<a href="{html.escape(url)}">Link</a>' if url else ""

        # Cells order MUST match headers order
        cells = [
            html.escape(published_at_str),   # Published At first
            html.escape(name),
            image_cell,
            html.escape(breeds),
            html.escape(size),
            html.escape(age),
            html.escape(gender),
            desc,
            url_cell,
        ]
        rows_html.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    table_body = (
        "".join(rows_html)
        if rows_html
        else f"<tr><td colspan='{len(headers)}'>No matching dogs in the last 6 hours.</td></tr>"
    )

    table = f"""
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.3;width:100%;">
      <thead style="background:#f5f5f5;">
        <tr>{"".join(f"<th style='text-align:left;'>{h}</th>" for h in headers)}</tr>
      </thead>
      <tbody>
        {table_body}
      </tbody>
    </table>
    """
    
    # Add top dogs section at the beginning if available
    if top_dogs_html:
        full_html = f"""
        <div style="margin-bottom: 30px;">
          <h2 style="color: #2c3e50; font-family: Arial, Helvetica, sans-serif; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
            🐕 Top Dogs to Consider
          </h2>
          <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 20px;">
            {top_dogs_html}
          </div>
        </div>
        <div>
          <h2 style="color: #2c3e50; font-family: Arial, Helvetica, sans-serif; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
            📋 All Available Dogs
          </h2>
          {table}
        </div>
        """
        return full_html
    
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
    
    # Generate top dogs recommendations using OpenAI
    print("Analyzing dogs with OpenAI...")
    top_dogs_html = analyze_dogs_with_openai(animals)
    
    html_table = build_html_table(animals, top_dogs_html)
    subject = f"ADR+Shappy Dog Search: {len(animals)} matches in last 6h (run @ {NOW_UTC.isoformat()})"
    send_email(subject, f"<div>{html_table}</div>")
    print(f"Sent digest with {len(animals)} dogs to: {', '.join(RECIPIENTS)}")

if __name__ == "__main__":
    main()
