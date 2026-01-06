import asyncio
import os
from datetime import datetime
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText

#================= CONFIG =================

EMAIL = os.getenv("EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
RECIPIENT = os.getenv("RECIPIENT_EMAIL", EMAIL)

RESERVATION_URL = "https://reservation.lesgrandsbuffets.com/contact"
CHECK_INTERVAL = 300  # 5 minutes
GUESTS = "7"

FULLY_BOOKED_PHRASES = [
    "we regret to inform you",
    "restaurant is fully booked",
    "complet pour ce service",
    "restaurant est complet",
    "complet"
]

# ==========================================


def send_email(dates):
    body = (
        "🚨 REAL availability detected at Les Grands Buffets!\n\n"
        "Dates:\n"
        + "\n".join(dates)
        + f"\n\nBook immediately:\n{RESERVATION_URL}"
    )

    msg = MIMEText(body)
    msg["Subject"] = "🍽️ Les Grands Buffets — Availability Found!"
    msg["From"] = EMAIL
    msg["To"] = RECIPIENT

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL, EMAIL_PASSWORD)
        smtp.send_message(msg)

    print("✅ Email sent!")


def is_friday_or_saturday(text: str) -> bool:
    text = text.lower()
    return any(day in text for day in ["fri", "vendredi", "sat", "samedi"])


async def gather_candidate_buttons(page):
    print("🔍 Scanning for Friday/Saturday date buttons...")

    buttons = await page.query_selector_all("button")
    candidates = []

    for btn in buttons:
        try:
            disabled = await btn.get_attribute("disabled")
            text = (await btn.inner_text()).strip()
            aria = (await btn.get_attribute("aria-label") or "").strip()

            combined = f"{text} {aria}"

            if not combined.strip():
                continue

            if is_friday_or_saturday(combined) and disabled is None:
                candidates.append(btn)
        except:
            pass

    print(f"📅 Found {len(candidates)} candidate date buttons.")
    return candidates


async def is_fully_booked(page):
    content = (await page.content()).lower()
    return any(phrase in content for phrase in FULLY_BOOKED_PHRASES)


async def check_dates(page):
    available_dates = []

    buttons = await gather_candidate_buttons(page)

    for btn in buttons:
        try:
            label = (
                await btn.get_attribute("aria-label")
                or (await btn.inner_text())
                or "unknown date"
            )

            print("➡️ Checking:", label)
            await btn.click()
            await asyncio.sleep(1)

            # Click "Next / Continue"
            for text in ["Suivant", "Next", "Continuer", "Continue"]:
                try:
                    await page.locator(f"text={text}").click(timeout=3000)
                    break
                except:
                    pass

            await asyncio.sleep(2)

            if await is_fully_booked(page):
                print("❌ Fully booked.")
            else:
                print("🔥 REAL availability found!")
                available_dates.append(label)

            # Go back twice to return to calendar
            try:
                await page.go_back()
                await asyncio.sleep(0.5)
                await page.go_back()
                await asyncio.sleep(1)
            except:
                pass

        except Exception as e:
            print("⚠️ Error checking date:", e)

    return available_dates


async def run_once():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("🌐 Loading reservation page...")
        await page.goto(RESERVATION_URL, wait_until="networkidle")

        await asyncio.sleep(2)

        # Try selecting guests if visible
        for sel in ["select", "select#guests", "select[name='guests']"]:
            try:
                await page.select_option(sel, GUESTS)
                print("👥 Guests selected.")
                break
            except:
                pass

        # Click next if present
        for text in ["Suivant", "Next", "Continuer", "Continue"]:
            try:
                await page.locator(f"text={text}").click(timeout=3000)
                break
            except:
                pass

        await asyncio.sleep(2)

        results = await check_dates(page)
        await browser.close()
        return results


async def monitor():
    print("🔍 Reservation agent started.")
    notified = set()

    while True:
        try:
            results = await run_once()

            new = [r for r in results if r not in notified]
            if new:
                send_email(new)
                notified.update(new)
            else:
                print(f"[{datetime.now()}] No real Friday/Saturday availability yet.")

        except Exception as e:
            print("❌ Error during check:", e)

        print("⏳ Waiting 5 minutes before next check...\n")
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(monitor())





