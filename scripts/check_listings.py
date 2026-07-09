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

RENTCAST_URL = "https://api.rentcast.io/v1/listings/sale"


def current_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


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

    save_current_ids(current_ids)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        print(f"RentCast API error: {exc}", file=sys.stderr)
        print(exc.response.text, file=sys.stderr)
        raise
