import asyncio
import os
import json
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import smtplib
from email.mime.text import MIMEText
import sys

#================= CONFIG =================

EMAIL = os.getenv("EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
RECIPIENT = os.getenv("RECIPIENT_EMAIL", EMAIL)
MONITORING_EMAIL = os.getenv("MONITORING_EMAIL")

RESERVATION_URL = "https://reservation.lesgrandsbuffets.com/contact"
GUESTS = "7"

FULLY_BOOKED_PHRASES = [
    "we regret to inform you",
    "restaurant is fully booked",
    "complet pour ce service",
    "restaurant est complet",
    "complet"
]

STATE_FILE = "run_state.json"

# ==========================================


def load_state():
    """Load run statistics from state file"""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    
    return {
        "total_runs": 0,
        "successful_finds": 0,
        "last_report_time": None,
        "reservation_found": False,
        "last_run_time": None
    }


def save_state(state):
    """Save run statistics to state file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save state: {e}")


def send_email(subject, body, recipient):
    """Send email notification"""
    try:
        if not EMAIL or not EMAIL_PASSWORD:
            print("⚠️ Email credentials not configured!")
            return False
        
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL
        msg["To"] = recipient

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL, EMAIL_PASSWORD)
            smtp.send_message(msg)

        print(f"✅ Email sent to {recipient}")
        return True
    except Exception as e:
        print(f"❌ Email failed: {e}")
        return False


def send_availability_alert(dates):
    """Send availability alert to main recipient"""
    body = (
        "🚨 REAL availability detected at Les Grands Buffets!\n\n"
        "Dates:\n"
        + "\n".join(dates)
        + f"\n\nBook immediately:\n{RESERVATION_URL}\n\n"
        + "The monitoring script will now stop running."
    )
    
    # Send to main recipient
    send_email("🍽️ Les Grands Buffets — Availability Found!", body, RECIPIENT)
    
    # Send to monitoring email
    send_email("🍽️ Les Grands Buffets — Availability Found!", body, MONITORING_EMAIL)


def send_status_report(state):
    """Send 6-hour status report to monitoring email"""
    now = datetime.now()
    
    body = (
        f"📊 Les Grands Buffets Monitoring Report\n\n"
        f"Report Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Total Runs: {state['total_runs']}\n"
        f"Successful Finds: {state['successful_finds']}\n"
        f"Last Run: {state.get('last_run_time', 'N/A')}\n"
        f"Reservation Found: {'Yes ✅' if state['reservation_found'] else 'No ❌'}\n\n"
        f"Status: {'🎉 SUCCESS - Script will stop' if state['reservation_found'] else '✅ Running normally'}\n\n"
        f"Next report in 6 hours (unless reservation found)."
    )
    
    send_email("📊 Reservation Monitor - 6 Hour Report", body, MONITORING_EMAIL)
    print("📧 Status report sent to monitoring email")


def should_send_report(state):
    """Check if it's time to send a 6-hour report"""
    if state["last_report_time"] is None:
        return True
    
    try:
        last_report = datetime.fromisoformat(state["last_report_time"])
        return (datetime.now() - last_report) >= timedelta(hours=6)
    except:
        return True


def is_friday_or_saturday(text: str) -> bool:
    """Check if text contains Friday or Saturday"""
    text = text.lower()
    return any(day in text for day in ["fri", "vendredi", "sat", "samedi"])


async def gather_candidate_buttons(page):
    """Find all enabled Friday/Saturday date buttons"""
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
                candidates.append((btn, aria or text))
        except:
            pass

    print(f"📅 Found {len(candidates)} candidate date buttons.")
    return candidates


async def is_fully_booked(page):
    """Check if page shows fully booked message"""
    try:
        content = (await page.content()).lower()
        return any(phrase in content for phrase in FULLY_BOOKED_PHRASES)
    except:
        return False


async def check_single_date(page, label):
    """Check availability for a single date"""
    try:
        print(f"➡️ Checking: {label}")
        
        # Find and click the button
        buttons = await gather_candidate_buttons(page)
        target_btn = None
        
        for btn, btn_label in buttons:
            if label in btn_label or btn_label in label:
                target_btn = btn
                break
        
        if not target_btn:
            print(f"⚠️ Could not find button for {label}")
            return False
        
        await target_btn.scroll_into_view_if_needed()
        await target_btn.click()
        await page.wait_for_load_state("networkidle", timeout=10000)
        
        # Click "Next / Continue"
        next_clicked = False
        for text in ["Suivant", "Next", "Continuer", "Continue"]:
            try:
                await page.locator(f"text={text}").first.click(timeout=3000)
                next_clicked = True
                break
            except:
                pass
        
        if not next_clicked:
            print("⚠️ Could not find next button")
            return False
        
        await page.wait_for_load_state("networkidle", timeout=10000)
        
        # Check if fully booked
        if await is_fully_booked(page):
            print("❌ Fully booked.")
            return False
        else:
            print("🔥 REAL availability found!")
            return True
            
    except Exception as e:
        print(f"⚠️ Error checking {label}: {e}")
        return False


async def check_dates(page):
    """Check all candidate dates for availability"""
    available_dates = []
    
    # Get all candidates
    candidates = await gather_candidate_buttons(page)
    
    for _, label in candidates:
        # Reset to calendar page
        await page.goto(RESERVATION_URL, wait_until="networkidle")
        await asyncio.sleep(1)
        
        # Re-select guests
        try:
            await page.select_option("select", GUESTS, timeout=3000)
            print("👥 Guests selected.")
        except:
            print("⚠️ Could not select guests")
        
        # Try clicking next
        for text in ["Suivant", "Next", "Continuer", "Continue"]:
            try:
                await page.locator(f"text={text}").first.click(timeout=3000)
                break
            except:
                pass
        
        await asyncio.sleep(1)
        
        # Check this specific date
        if await check_single_date(page, label):
            available_dates.append(label)
    
    return available_dates


async def run_check():
    """Single check run - designed for GitHub Actions"""
    # Load state
    state = load_state()
    
    # Check if reservation was already found
    if state.get("reservation_found", False):
        print("🛑 Reservation already found. Script is stopped.")
        print("To restart monitoring, delete run_state.json")
        sys.exit(0)
    
    browser = None
    try:
        print(f"🚀 Starting check at {datetime.now()}")
        
        # Update run count
        state["total_runs"] += 1
        state["last_run_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            print("🌐 Loading reservation page...")
            await page.goto(RESERVATION_URL, wait_until="networkidle")
            await asyncio.sleep(2)

            # Try selecting guests
            try:
                await page.select_option("select", GUESTS, timeout=3000)
                print("👥 Guests selected.")
            except:
                print("⚠️ Could not select guests initially")

            # Click next if present
            for text in ["Suivant", "Next", "Continuer", "Continue"]:
                try:
                    await page.locator(f"text={text}").first.click(timeout=3000)
                    break
                except:
                    pass

            await asyncio.sleep(2)

            results = await check_dates(page)
            
            if results:
                print(f"\n🎉 Found {len(results)} available dates!")
                state["successful_finds"] += 1
                state["reservation_found"] = True
                save_state(state)
                
                # Send immediate alerts
                send_availability_alert(results)
                
                await browser.close()
                
                print("\n🛑 RESERVATION FOUND - Script will now stop running")
                print("Delete run_state.json to restart monitoring")
                sys.exit(0)  # Exit successfully but stop future runs
            else:
                print("\n😔 No Friday/Saturday availability found.")
            
            await browser.close()
            
    except Exception as e:
        print(f"❌ Critical error: {e}")
        if browser:
            await browser.close()
    
    # Save state
    save_state(state)
    
    # Check if we should send 6-hour report
    if should_send_report(state):
        send_status_report(state)
        state["last_report_time"] = datetime.now().isoformat()
        save_state(state)
    
    return state.get("reservation_found", False)


if __name__ == "__main__":
    found_availability = asyncio.run(run_check())
    sys.exit(0)
