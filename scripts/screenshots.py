"""Dev-only: capture README screenshots of the dashboard and simulator (headless Chrome).

Playwright is NOT a project dependency; `uv run --with playwright` pulls it in for this run
only, and it drives the Google Chrome already installed on the machine (channel="chrome").

    cd backend
    uv run python ../scripts/start.py --seed        # terminal 1: fresh demo data
    uv run --with playwright python ../scripts/screenshots.py   # terminal 2

It first plays the golden path through the simulator API (severe BP, repeat 5+ min later
-> red alert, low stock -> the AI requests a refill over MCP), then captures each screen,
approves the refill as Ravi, captures the result, then a reminder with voice notes and the
red-alert call cards. Output: docs/screenshots/*.png
"""

import argparse
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
PARENT_REF = "+919800000001"
TOKEN_DEFAULT = "change-me"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}


def prepare(c: httpx.Client, token: str) -> dict[str, str]:
    """Play the golden path and return sessions for Ravi and the parent."""

    def say(text: str) -> None:
        r = c.post("/simulator/api/send", json={"sender_ref": PARENT_REF, "text": text})
        r.raise_for_status()

    say("186/121")
    say("1")  # first severe reading: the family is not alerted, the parent rests
    c.post("/simulator/api/skip", json={"minutes": 6}).raise_for_status()
    say("188/122")
    say("1")  # confirmed repeat 5-60 min later: red alert to the family
    say("sugar 112")
    say("1")

    members = c.post("/api/login/members", json={"token": token}).json()["members"]
    sessions = {}
    for m in members:
        r = c.post("/api/login", json={"token": token, "member_id": m["id"]})
        sessions[m["name"].split()[0].lower()] = r.json()["session"]
    h = {"Authorization": f"Bearer {sessions['ravi']}"}
    me = c.get("/api/me", headers=h).json()
    pid = me["parents"][0]["id"]
    meds = c.get(f"/api/parents/{pid}/medicines", headers=h).json()["medicines"]
    telma = next(m for m in meds if m["brand"] == "Telma 40")
    r = c.post(f"/api/parents/{pid}/demo/low-stock", json={"medicine_id": telma["id"]}, headers=h)
    if r.status_code != 200:
        print(f"low stock: {r.status_code} {r.text}", file=sys.stderr)
    sessions["parent_id"] = pid
    return sessions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--token", default=TOKEN_DEFAULT, help="DASHBOARD_DEMO_TOKEN")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(base_url=args.base, timeout=30)
    s = prepare(c, args.token)
    dash = f"{args.base}/dashboard/"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        def page(viewport: dict, session: str | None, scale: float = 2):
            ctx = browser.new_context(
                viewport=viewport, device_scale_factor=scale, color_scheme="light"
            )
            pg = ctx.new_page()
            pg.goto(dash)
            if session:
                pg.evaluate(f"localStorage.setItem('khayal-session', '{session}')")
            return pg

        def shot(pg, tab: str, name: str, wait: str, full: bool = True) -> None:
            pg.goto(f"{dash}#{tab}")
            pg.reload()
            pg.wait_for_selector(wait)
            pg.wait_for_timeout(600)
            pg.screenshot(path=OUT / name, full_page=full)
            print("saved", OUT / name)

        phone = page(PHONE, s["ravi"])
        shot(phone, "overview", "dashboard-overview-phone.png", ".refill-pending")

        desk = page(DESKTOP, s["ravi"], scale=1.5)
        shot(desk, "trends", "dashboard-trends.png", ".recharts-line")
        shot(desk, "alerts", "dashboard-alerts.png", ".alert-item")

        # Ravi approves on the dashboard (the only approval path).
        phone.goto(f"{dash}#overview")
        phone.reload()
        phone.get_by_role("button", name="Approve").first.click()
        phone.wait_for_timeout(1500)
        shot(phone, "refills", "dashboard-refills-phone.png", ".history li")
        shot(desk, "audit", "dashboard-audit.png", ".audit-item")

        parent = page(PHONE, s["kamla"])
        shot(parent, "settings", "dashboard-sharing-phone.png", ".switch")

        sim = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
        sp = sim.new_page()
        sp.goto(f"{args.base}/simulator")
        sp.wait_for_timeout(2500)
        sp.screenshot(path=OUT / "simulator.png")
        print("saved", OUT / "simulator.png")

        # Voice: jump to the next reminder (Indian English voice note), the parent answers by voice;
        # the red-alert "Incoming call" cards stay in the children's panes.
        c.post("/simulator/api/advance", json={"to": "next_reminder"}).raise_for_status()
        for v in ("took_medicine_en", "sugar_en"):
            c.post(
                "/simulator/api/send", json={"sender_ref": PARENT_REF, "voice_sample": v}
            ).raise_for_status()
        sp.reload()
        sp.wait_for_timeout(2500)
        sp.evaluate("document.getElementById('ai').open = false")
        # Later refill messages push the call cards up; scroll each child's feed back to it.
        sp.evaluate(
            """for (const c of document.querySelectorAll('.callcard')) {
                 const box = c.parentElement, top = box.getBoundingClientRect().top;
                 box.scrollTop += c.getBoundingClientRect().top - top - 8;
               }"""
        )
        sp.wait_for_timeout(500)
        sp.screenshot(path=OUT / "voice-and-call.png")
        print("saved", OUT / "voice-and-call.png")

        login = page(PHONE, None)
        login.evaluate("localStorage.clear()")
        login.reload()
        login.fill("#token", args.token)
        login.get_by_role("button", name="Continue").click()
        login.wait_for_selector(".member-btn")
        login.screenshot(path=OUT / "dashboard-login-phone.png")
        print("saved", OUT / "dashboard-login-phone.png")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
