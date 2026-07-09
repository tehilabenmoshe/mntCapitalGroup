"""Hourly check for new for-sale listings via the RentCast API, emailed as alerts.

Compares the current set of matching listings against the IDs saved from the
previous run (state/previous_ids.json) and only alerts on listings that are new.
"""

import json
import os
import smtplib
import sys
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

STATE_FILE = Path(__file__).resolve().parent.parent / "state" / "previous_ids.json"

RENTCAST_URL = "https://api.rentcast.io/v1/listings/sale"


def fetch_active_listings():
    listings = []
    offset = 0
    limit = 500
    while True:
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
        response.raise_for_status()
        page = response.json()
        listings.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return listings


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
    all_listings = fetch_active_listings()
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
