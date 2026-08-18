"""Daily check for new for-sale listings via the RentCast API, emailed as alerts.

Compares the current set of matching listings against the IDs saved from the
previous run (state/previous_ids.json) and only alerts on listings that are new.

A hard monthly request cap (state/usage.json) guarantees the script can never
exceed the free RentCast quota, so no overage charges are ever possible: before
every API request it checks the counter, and if the cap is reached it stops
instead of calling the API.
"""

import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

RENTCAST_API_KEY = os.environ["RENTCAST_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAIL_TO = os.environ["ALERT_EMAIL_TO"]

CITY = os.environ.get("SEARCH_CITY", "Carmel")
STATE = os.environ.get("SEARCH_STATE", "IN")
MAX_PRICE = int(os.environ.get("MAX_PRICE", "300000"))

# Hard ceiling on RentCast API requests per calendar month. Kept safely below
# the free tier's 50 so that even a double-run or an extra pagination page can
# never push us over the free quota into paid overage territory.
MONTHLY_REQUEST_CAP = int(os.environ.get("MONTHLY_REQUEST_CAP") or "45")

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_FILE = STATE_DIR / "previous_ids.json"
USAGE_FILE = STATE_DIR / "usage.json"

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
SITE_DATA_FILE = DOCS_DIR / "listings.json"

# Fields we watch for changes between runs. A change in any of these appends a
# history entry to the listing on the site, so duplicates that differ in price
# or another criterion are shown with their full detail.
TRACKED_FIELDS = ["price", "bedrooms", "bathrooms", "squareFootage", "status"]

RENTCAST_URL = "https://api.rentcast.io/v1/listings/sale"


def current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_usage():
    """Return the request count used so far in the current calendar month."""
    if USAGE_FILE.exists():
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        if data.get("month") == current_month():
            return data.get("count", 0)
    return 0  # new month (or first run) -> counter resets to 0


def save_usage(count):
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(
        json.dumps({"month": current_month(), "count": count}, indent=2),
        encoding="utf-8",
    )


class QuotaExceeded(Exception):
    """Raised when making another request would exceed the monthly cap."""


def fetch_active_listings(usage_count):
    """Fetch active listings, enforcing the monthly request cap.

    Returns (listings, new_usage_count). Raises QuotaExceeded if the very first
    request would already breach the cap, so we never call the API in that case.
    """
    listings = []
    offset = 0
    limit = 500
    while True:
        if usage_count >= MONTHLY_REQUEST_CAP:
            if not listings:
                raise QuotaExceeded(
                    f"Monthly cap of {MONTHLY_REQUEST_CAP} requests reached "
                    f"for {current_month()} — no API request made."
                )
            # Cap hit mid-pagination: stop with the partial data we have.
            print(
                f"Reached monthly cap of {MONTHLY_REQUEST_CAP} during pagination; "
                f"returning {len(listings)} listings fetched so far."
            )
            break
        response = requests.get(
            RENTCAST_URL,
            headers={"X-Api-Key": RENTCAST_API_KEY, "Accept": "application/json"},
            params={
                "city": CITY,
                "state": STATE,
                "status": "Active",
                "limit": limit,
                "offset": offset,
            },
            timeout=30,
        )
        usage_count += 1  # count every request that actually leaves the machine
        response.raise_for_status()
        page = response.json()
        listings.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return listings, usage_count


def load_previous_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_current_ids(ids):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


def listing_snapshot(listing):
    """Extract the fields we store on the site for a single listing."""
    return {
        "id": listing["id"],
        "address": listing.get("formattedAddress", str(listing["id"]).replace("-", " ")),
        "price": listing.get("price"),
        "bedrooms": listing.get("bedrooms"),
        "bathrooms": listing.get("bathrooms"),
        "squareFootage": listing.get("squareFootage"),
        "propertyType": listing.get("propertyType"),
        "listedDate": listing.get("listedDate"),
        "mlsNumber": listing.get("mlsNumber"),
        "latitude": listing.get("latitude"),
        "longitude": listing.get("longitude"),
    }


def load_site_data():
    """Return existing site listings keyed by id (empty dict on first run)."""
    if SITE_DATA_FILE.exists():
        try:
            data = json.loads(SITE_DATA_FILE.read_text(encoding="utf-8"))
            return {entry["id"]: entry for entry in data.get("listings", [])}
        except (json.JSONDecodeError, KeyError):
            return {}
    return {}


def update_site_data(matching):
    """Merge the current matching listings into the accumulated site dataset.

    Keeps every listing ever seen. For a returning listing, any change in a
    tracked field is appended to its history so the site can show the detail of
    how the same offer differs over time (e.g. a price drop). Listings that are
    no longer active are kept and flagged as removed.
    """
    existing = load_site_data()
    stamp = now_iso()
    current_ids = set()

    for listing in matching:
        lid = listing["id"]
        current_ids.add(lid)
        snap = listing_snapshot(listing)

        if lid not in existing:
            entry = dict(snap)
            entry["first_seen"] = stamp
            entry["last_seen"] = stamp
            entry["active"] = True
            entry["history"] = [{"date": stamp, "event": "listed", "changes": []}]
            existing[lid] = entry
            continue

        entry = existing[lid]
        changes = []
        for field in TRACKED_FIELDS:
            old, new = entry.get(field), snap.get(field)
            if new is not None and old != new:
                changes.append({"field": field, "from": old, "to": new})
        for key, value in snap.items():
            if value is not None:
                entry[key] = value
        entry["last_seen"] = stamp
        was_inactive = not entry.get("active", True)
        entry["active"] = True
        if was_inactive:
            entry.setdefault("history", []).append(
                {"date": stamp, "event": "relisted", "changes": changes}
            )
        elif changes:
            entry.setdefault("history", []).append(
                {"date": stamp, "event": "updated", "changes": changes}
            )

    # Anything not in this run's active set is kept but flagged as removed once.
    for lid, entry in existing.items():
        if lid not in current_ids and entry.get("active", True):
            entry["active"] = False
            entry.setdefault("history", []).append(
                {"date": stamp, "event": "removed", "changes": []}
            )

    listings_out = sorted(
        existing.values(), key=lambda e: e.get("first_seen", ""), reverse=True
    )
    payload = {
        "generated_at": stamp,
        "city": CITY,
        "state": STATE,
        "max_price": MAX_PRICE,
        "count": len(listings_out),
        "active_count": sum(1 for e in listings_out if e.get("active")),
        "listings": listings_out,
    }
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    SITE_DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Site data updated: {payload['count']} total listings "
        f"({payload['active_count']} active)."
    )


def build_email_body(new_listings):
    lines_html = []
    lines_text = []
    for listing in new_listings:
        address = listing.get("formattedAddress", "כתובת לא ידועה")
        price = listing.get("price")
        price_str = f"${price:,.0f}" if price else "מחיר לא צוין"
        beds = listing.get("bedrooms", "-")
        baths = listing.get("bathrooms", "-")
        sqft = listing.get("squareFootage", "-")
        listed_date = listing.get("listedDate", "-")
        mls_number = listing.get("mlsNumber", "-")
        maps_url = "https://www.google.com/maps/search/" + address.replace(" ", "+")

        lines_text.append(
            f"{address}\n"
            f"  מחיר: {price_str} | חדרי שינה: {beds} | אמבטיות: {baths} | רגל רבועה: {sqft}\n"
            f"  פורסם: {listed_date} | MLS#: {mls_number}\n"
            f"  {maps_url}\n"
        )
        lines_html.append(
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>"
            f"<a href='{maps_url}'>{address}</a></td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{price_str}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{beds}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{baths}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{sqft}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #ddd'>{listed_date}</td>"
            f"</tr>"
        )

    html = f"""
    <html><body>
    <h2>{len(new_listings)} בתים חדשים למכירה ב-{CITY}, {STATE} מתחת ל-${MAX_PRICE:,.0f}</h2>
    <table style='border-collapse:collapse;width:100%;font-family:sans-serif'>
      <tr style='background:#f0f0f0;text-align:left'>
        <th style='padding:8px'>כתובת</th><th style='padding:8px'>מחיר</th>
        <th style='padding:8px'>חד"ש</th><th style='padding:8px'>אמבטיות</th>
        <th style='padding:8px'>רגל רבועה</th><th style='padding:8px'>פורסם</th>
      </tr>
      {''.join(lines_html)}
    </table>
    </body></html>
    """
    text = f"{len(new_listings)} בתים חדשים למכירה ב-{CITY}, {STATE} מתחת ל-${MAX_PRICE:,.0f}\n\n" + "\n".join(lines_text)
    return text, html


def send_email(subject, text_body, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_EMAIL_TO
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [ALERT_EMAIL_TO], msg.as_string())


def main():
    usage_count = load_usage()
    print(f"RentCast requests used this month ({current_month()}): {usage_count}/{MONTHLY_REQUEST_CAP}.")

    try:
        all_listings, usage_count = fetch_active_listings(usage_count)
    except QuotaExceeded as exc:
        # Save the (unchanged) counter and exit cleanly — no failure, no charge.
        save_usage(usage_count)
        print(f"SKIPPED: {exc}")
        return

    # Persist the updated counter immediately, before any further work.
    save_usage(usage_count)

    matching = [l for l in all_listings if l.get("price") and l["price"] <= MAX_PRICE]
    current_ids = {l["id"] for l in matching}

    previous_ids = load_previous_ids()
    new_ids = current_ids - previous_ids
    new_listings = [l for l in matching if l["id"] in new_ids]

    print(f"Fetched {len(all_listings)} active listings, {len(matching)} under ${MAX_PRICE:,.0f}, {len(new_listings)} new.")

    if new_listings:
        subject = f"🏠 {len(new_listings)} בתים חדשים ב-{CITY}, {STATE} מתחת ל-${MAX_PRICE:,.0f}"
        text_body, html_body = build_email_body(new_listings)
        send_email(subject, text_body, html_body)
        print("Email sent.")
    else:
        print("No new listings, no email sent.")

    # Update the website dataset every run so price changes on existing
    # listings are captured even when there is no brand-new listing to email.
    update_site_data(matching)

    save_current_ids(current_ids)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        print(f"RentCast API error: {exc}", file=sys.stderr)
        print(exc.response.text, file=sys.stderr)
        raise
