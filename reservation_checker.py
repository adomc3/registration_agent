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
SERVICE_TYPE = "dinner"  # "lunch" or "dinner"
MONTHS_AHEAD = 4  # How many months to check in advance

FULLY_BOOKED_PHRASES = [
    "we regret to inform you",
    "restaurant is fully booked",
    "complet pour ce service",
    "restaurant est complet",
    "complet"
]

DINNER_KEYWORDS = ["dinner", "dîner", "diner", "soir", "evening", "19:", "20:", "21:"]
LUNCH_KEYWORDS = ["lunch", "déjeuner", "dejeuner", "midi", "12:", "13:", "14:"]

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
        + "\n".join(f"  • {date}" for date in dates)
        + f"\n\n🔗 Book immediately:\n{RESERVATION_URL}\n\n"
        + "⚠️ The monitoring script will now stop running.\n"
        + f"Checked for: {GUESTS} guests, Friday/Saturday dinners, next {MONTHS_AHEAD} months"
    )
    
    # Send to main recipient
    success1 = send_email("🍽️ Les Grands Buffets — Availability Found!", body, RECIPIENT)
    
    # Send to monitoring email
    success2 = send_email("🍽️ Les Grands Buffets — Availability Found!", body, MONITORING_EMAIL)
    
    return success1 or success2


def send_status_report(state):
    """Send 6-hour status report to monitoring email"""
    now = datetime.now()
    
    # Calculate uptime
    uptime = "N/A"
    if state.get('last_report_time'):
        try:
            last = datetime.fromisoformat(state['last_report_time'])
            hours = int((now - last).total_seconds() / 3600)
            uptime = f"{hours} hours"
        except:
            pass
    
    body = (
        f"📊 Les Grands Buffets Monitoring Report\n"
        f"{'='*50}\n\n"
        f"⏰ Report Time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📈 Total Runs: {state['total_runs']}\n"
        f"✅ Successful Finds: {state['successful_finds']}\n"
        f"🕐 Last Run: {state.get('last_run_time', 'N/A')}\n"
        f"⏳ Uptime Since Last Report: {uptime}\n"
        f"🎯 Reservation Found: {'Yes ✅' if state['reservation_found'] else 'No ❌'}\n\n"
        f"🔍 Search Criteria:\n"
        f"  • Days: Friday & Saturday only\n"
        f"  • Service: {SERVICE_TYPE.title()}\n"
        f"  • Guests: {GUESTS}\n"
        f"  • Time Range: Next {MONTHS_AHEAD} months\n\n"
        f"{'='*50}\n"
        f"Status: {'🎉 SUCCESS - Script will stop' if state['reservation_found'] else '✅ Running normally'}\n\n"
        f"Next report in 6 hours (unless reservation found)."
    )
    
    success = send_email("📊 Reservation Monitor - 6 Hour Report", body, MONITORING_EMAIL)
    if success:
        print("📧 Status report sent to monitoring email")
    return success


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


def is_dinner_service(text: str) -> bool:
    """Check if text indicates dinner service"""
    text = text.lower()
    
    if SERVICE_TYPE == "dinner":
        # Check for dinner keywords
        return any(keyword in text for keyword in DINNER_KEYWORDS)
    elif SERVICE_TYPE == "lunch":
        # Check for lunch keywords
        return any(keyword in text for keyword in LUNCH_KEYWORDS)
    else:
        # If no service type specified, accept all
        return True


def is_within_date_range(text: str) -> bool:
    """Check if date is within the next 4 months (INCLUDE all dates in this range)"""
    from datetime import datetime, timedelta
    import re
    
    # Try to extract date from text
    # Common formats: "Friday 25 April", "Vendredi 25 avril 2025", etc.
    months_en = ["january", "february", "march", "april", "may", "june", 
                 "july", "august", "september", "october", "november", "december"]
    months_fr = ["janvier", "février", "mars", "avril", "mai", "juin",
                 "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    
    text_lower = text.lower()
    
    # Find month in text
    month_num = None
    for i, month in enumerate(months_en + months_fr, 1):
        if month in text_lower:
            month_num = (i % 12) if i > 12 else i
            break
    
    if not month_num:
        # Can't determine date, include it to be safe
        return True
    
    # Extract day number
    day_match = re.search(r'\b(\d{1,2})\b', text)
    if not day_match:
        return True
    
    day_num = int(day_match.group(1))
    
    # Extract or assume year
    year_match = re.search(r'\b(20\d{2})\b', text)
    current_year = datetime.now().year
    year_num = int(year_match.group(1)) if year_match else current_year
    
    try:
        date_found = datetime(year_num, month_num, day_num)
        today = datetime.now()
        max_date = today + timedelta(days=MONTHS_AHEAD * 30)
        
        # INCLUDE dates from today up to 4 months ahead
        return today <= date_found <= max_date
    except:
        # Invalid date, include it to be safe
        return True


async def gather_candidate_buttons(page):
    """Find all enabled Friday/Saturday DINNER date buttons within 4 months"""
    print(f"🔍 Scanning for Friday/Saturday {SERVICE_TYPE} buttons (next {MONTHS_AHEAD} months)...")

    buttons = await page.query_selector_all("button")
    candidates = []
    
    # DEBUG: Log ALL buttons found
    print(f"📊 DEBUG: Found {len(buttons)} total buttons on page")
    
    friday_saturday_buttons = []
    enabled_buttons = []
    in_range_buttons = []

    for btn in buttons:
        try:
            disabled = await btn.get_attribute("disabled")
            text = (await btn.inner_text()).strip()
            aria = (await btn.get_attribute("aria-label") or "").strip()

            combined = f"{text} {aria}"

            if not combined.strip():
                continue
            
            # DEBUG: Check each filter separately
            is_fri_sat = is_friday_or_saturday(combined)
            is_enabled = disabled is None
            is_in_range = is_within_date_range(combined)
            
            if is_fri_sat:
                friday_saturday_buttons.append(combined)
                
            if is_enabled and combined.strip():
                enabled_buttons.append(combined)
            
            if is_in_range:
                in_range_buttons.append(combined)

            # Must be Friday/Saturday, enabled, and within date range
            if is_fri_sat and is_enabled and is_in_range:
                candidates.append((btn, aria or text))
                print(f"  ✅ Candidate found: {aria or text}")
        except Exception as e:
            print(f"  ⚠️ Error processing button: {e}")
            pass

    # DEBUG: Show filtering results
    print(f"\n📊 DIAGNOSTIC INFO:")
    print(f"  • Total buttons: {len(buttons)}")
    print(f"  • Friday/Saturday buttons: {len(friday_saturday_buttons)}")
    print(f"  • Enabled buttons: {len(enabled_buttons)}")
    print(f"  • In date range buttons: {len(in_range_buttons)}")
    print(f"  • Final candidates (all filters): {len(candidates)}")
    
    if len(friday_saturday_buttons) > 0:
        print(f"\n  Sample Fri/Sat buttons found:")
        for i, btn_text in enumerate(friday_saturday_buttons[:5], 1):
            print(f"    {i}. {btn_text}")
    
    if len(candidates) == 0 and len(friday_saturday_buttons) > 0:
        print(f"\n  ⚠️ Found Fri/Sat buttons but they were filtered out!")
        print(f"     Likely reasons: disabled or outside date range")

    print(f"\n📅 Final result: {len(candidates)} candidate date buttons.")
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
        
        # Look for time slot selection (dinner slots)
        await asyncio.sleep(2)
        
        # DEBUG: Check what's on the page after clicking date
        page_content_sample = await page.content()
        print(f"  📄 Page loaded, checking for time slots...")
        
        # Check if there are time slot buttons to select dinner
        time_buttons = await page.query_selector_all("button")
        print(f"  🔍 Found {len(time_buttons)} buttons for time selection")
        
        dinner_slot_found = False
        all_time_slots = []
        
        for time_btn in time_buttons:
            try:
                time_text = (await time_btn.inner_text()).strip()
                time_aria = (await time_btn.get_attribute("aria-label") or "").strip()
                time_combined = f"{time_text} {time_aria}".lower()
                
                if time_text or time_aria:
                    all_time_slots.append(f"{time_text or time_aria}")
                
                # Check if it's a dinner time slot
                if is_dinner_service(time_combined):
                    disabled = await time_btn.get_attribute("disabled")
                    if disabled is None:
                        print(f"   ✅ Found available dinner slot: {time_text or time_aria}")
                        await time_btn.click()
                        await page.wait_for_load_state("networkidle", timeout=5000)
                        dinner_slot_found = True
                        break
                    else:
                        print(f"   ❌ Dinner slot disabled: {time_text or time_aria}")
            except:
                pass
        
        # DEBUG: Show what time slots we found
        if len(all_time_slots) > 0:
            print(f"   📋 Time slots found: {', '.join(all_time_slots[:10])}")
        
        if not dinner_slot_found:
            print(f"   ⚠️ No available dinner time slots found")
            # Maybe we need to just click "Next" without selecting a time?
            print(f"   💡 Attempting to proceed without time selection...")
        
        # Click "Next / Continue"
        next_clicked = False
        for text in ["Suivant", "Next", "Continuer", "Continue"]:
            try:
                await page.locator(f"text={text}").first.click(timeout=3000)
                next_clicked = True
                print(f"   ✅ Clicked '{text}' button")
                break
            except:
                pass
        
        if not next_clicked:
            print("   ⚠️ Could not find next button")
            return False
        
        await page.wait_for_load_state("networkidle", timeout=10000)
        
        # Check if fully booked
        content_sample = (await page.content())[:500].lower()
        print(f"   📄 Checking for 'fully booked' message...")
        
        if await is_fully_booked(page):
            print("   ❌ Fully booked.")
            return False
        else:
            print("   🔥 REAL availability found!")
            # DEBUG: Save a screenshot if possible
            try:
                await page.screenshot(path=f"availability_found_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                print("   📸 Screenshot saved!")
            except:
                pass
            return True
            
    except Exception as e:
        print(f"⚠️ Error checking {label}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def check_dates(page):
    """Check all candidate dates for availability"""
    available_dates = []
    
    # Get all candidates
    candidates = await gather_candidate_buttons(page)
    
    if not candidates:
        print(f"⚠️ No Friday/Saturday {SERVICE_TYPE} dates found in the next {MONTHS_AHEAD} months")
        return []
    
    print(f"📋 Will check {len(candidates)} dates")
    
    for idx, (_, label) in enumerate(candidates, 1):
        print(f"\n--- Checking {idx}/{len(candidates)} ---")
        
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
        print("💡 To restart monitoring, delete run_state.json from GitHub artifacts")
        sys.exit(0)
    
    browser = None
    page = None
    try:
        print(f"🚀 Starting check #{state['total_runs'] + 1} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🔍 Looking for: {GUESTS} guests, Friday/Saturday {SERVICE_TYPE}, next {MONTHS_AHEAD} months\n")
        
        # Update run count
        state["total_runs"] += 1
        state["last_run_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Set a reasonable timeout
            page.set_default_timeout(15000)

            print("🌐 Loading reservation page...")
            await page.goto(RESERVATION_URL, wait_until="networkidle")
            await asyncio.sleep(3)
            
            # DEBUG: Take screenshot of initial page
            try:
                await page.screenshot(path="step1_initial.png")
                print("📸 Screenshot saved: step1_initial.png")
            except:
                pass

            # Try multiple selectors for guest selection
            guest_selected = False
            selectors_to_try = [
                'select[name="guests"]',
                'select#guests', 
                'select#numberOfGuests',
                'select',
                'input[name="guests"]',
                '[data-testid="guest-selector"]',
            ]
            
            for selector in selectors_to_try:
                try:
                    print(f"  Trying selector: {selector}")
                    
                    # Check if it's a select element
                    if 'select' in selector:
                        await page.select_option(selector, GUESTS, timeout=3000)
                        print(f"👥 Guests selected via {selector}")
                        guest_selected = True
                        break
                    else:
                        # Try as input field
                        await page.fill(selector, GUESTS, timeout=3000)
                        print(f"👥 Guests entered via {selector}")
                        guest_selected = True
                        break
                except Exception as e:
                    print(f"  ❌ {selector} failed: {str(e)[:50]}")
                    continue

            if not guest_selected:
                print("⚠️ Could not select guests - will try to proceed anyway")
            
            await asyncio.sleep(2)
            
            # DEBUG: Take screenshot after guest selection
            try:
                await page.screenshot(path="step2_after_guests.png")
                print("📸 Screenshot saved: step2_after_guests.png")
            except:
                pass

            # Try to find and click "Next/Continue" button with multiple strategies
            next_clicked = False
            
            # Strategy 1: Text-based search (multiple languages)
            next_texts = ["Next", "Suivant", "Continue", "Continuer", "Submit", "Soumettre"]
            for text in next_texts:
                try:
                    # Try case-insensitive
                    await page.get_by_text(text, exact=False).first.click(timeout=3000)
                    print(f"✅ Clicked button with text: {text}")
                    next_clicked = True
                    break
                except:
                    pass
            
            # Strategy 2: Try common button selectors
            if not next_clicked:
                button_selectors = [
                    'button[type="submit"]',
                    'button:has-text("Next")',
                    'button:has-text("Suivant")',
                    'input[type="submit"]',
                    'a.btn',
                    'button.btn-primary',
                ]
                
                for selector in button_selectors:
                    try:
                        await page.locator(selector).first.click(timeout=3000)
                        print(f"✅ Clicked button with selector: {selector}")
                        next_clicked = True
                        break
                    except:
                        pass
            
            if next_clicked:
                await asyncio.sleep(3)
                await page.wait_for_load_state("networkidle", timeout=10000)
            else:
                print("⚠️ Could not find Next button - page might auto-advance")

            # DEBUG: Take screenshot of calendar page
            try:
                await page.screenshot(path="step3_calendar.png")
                print("📸 Screenshot saved: step3_calendar.png")
            except:
                pass
            
            # DEBUG: Print page URL and title
            print(f"📍 Current URL: {page.url}")
            print(f"📄 Page title: {await page.title()}")
            
            results = await check_dates(page)
            
            if results:
                print(f"\n🎉🎉🎉 FOUND {len(results)} AVAILABLE DATES! 🎉🎉🎉")
                for date in results:
                    print(f"  ✅ {date}")
                
                state["successful_finds"] += 1
                state["reservation_found"] = True
                save_state(state)
                
                # Send immediate alerts
                if send_availability_alert(results):
                    print("\n✅ Alert emails sent successfully!")
                else:
                    print("\n⚠️ Failed to send alert emails")
                
                await browser.close()
                
                print("\n🛑 RESERVATION FOUND - Script will now stop running")
                print("💡 To restart: delete run_state.json from GitHub artifacts")
                sys.exit(0)  # Exit successfully but stop future runs
            else:
                print(f"\n😔 No {SERVICE_TYPE} availability found on Friday/Saturday in next {MONTHS_AHEAD} months.")
                print(f"✅ Checked {state['total_runs']} times so far. Will keep trying...")
            
            await browser.close()
            
    except Exception as e:
        print(f"❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
        if browser:
            try:
                await browser.close()
            except:
                pass
    
    # Save state
    save_state(state)
    
    # Check if we should send 6-hour report
    if should_send_report(state):
        print("\n📧 Sending 6-hour status report...")
        if send_status_report(state):
            state["last_report_time"] = datetime.now().isoformat()
            save_state(state)
    
    return state.get("reservation_found", False)


if __name__ == "__main__":
    found_availability = asyncio.run(run_check())
    sys.exit(0)
