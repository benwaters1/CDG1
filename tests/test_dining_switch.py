"""The dining switch: open, closed for the season, or switched off.

The public pages draw themselves from it -- the design side built that half --
and the house set it from nowhere, because nothing wrote it. This holds the
other half: the owner can set it, the pages can read it (it has to be on the
public allowlist, and read from `site`: on the dining pages `settings` is the
restaurant's own row, and reading the switch there took both pages down with
a 500), and the routes REFUSE while it is not open. Hiding a form is not
closing it: a page saved while dining was open, or a direct POST, would
otherwise still book a table in a restaurant that is not serving.
"""
from datetime import datetime, timedelta, timezone

from flask import template_rendered

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZDINE"
EMAIL = "zzdine@example.invalid"


def _mode(conn, mode, reopens=""):
    conn.execute("DELETE FROM app_settings WHERE key IN ('dining_mode', 'dining_reopens')")
    if mode is not None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_mode', ?)", (mode,))
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_reopens', ?)",
                     (reopens,))
    conn.commit()


def _dinners(conn, email):
    return conn.execute("SELECT COUNT(*) AS c FROM restaurant_bookings WHERE guest_email = ?",
                        (email,)).fetchone()["c"]


def run():
    s = Suite("The dining switch")
    oc, ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    was_enabled = conn.execute("SELECT enabled FROM restaurant_settings WHERE id = 1").fetchone()
    conn.execute("UPDATE restaurant_settings SET enabled = 1 WHERE id = 1")
    conn.commit()
    sent = []
    was_send = m.send_email
    m.send_email = lambda *a, **k: (sent.append(a), True)[1]
    try:
        s.section("Unset, it is open, and the dining pages answer")
        _mode(conn, None)
        with m.app.test_request_context("/"):
            s.check("an unset switch reads as open", m.dining_mode(conn) == "open")
        for path in ("/restaurant", "/restaurant/book", "/"):
            r = anon.get(path)
            s.check(f"{path} answers", r.status_code == 200, r)

        s.section("The owner sets it, on the restaurant's own settings page")
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("the page offers all three", all(
            f'name="dining_mode" value="{v}"' in page for v in m.DINING_MODES))
        settings_row = conn.execute("SELECT * FROM restaurant_settings WHERE id = 1").fetchone()
        form = {"capacity": str(settings_row["capacity"] or 20),
                "dinner_time": settings_row["dinner_time"] or "19:30",
                "enabled": "on", "dining_mode": "closed",
                "dining_reopens": "  April   2027 "}
        oc.post("/admin/restaurant/settings", data=form, follow_redirects=True)
        stored = dict(conn.execute("SELECT key, value FROM app_settings WHERE key IN "
                                   "('dining_mode', 'dining_reopens')").fetchall())
        s.check("closed is saved", stored.get("dining_mode") == "closed", detail=str(stored))
        s.check("with when it reopens, tidied", stored.get("dining_reopens") == "April 2027",
                detail=repr(stored.get("dining_reopens")))
        s.check("the page shows what is set",
                'value="closed" checked' in oc.get("/admin/restaurant/settings").get_data(as_text=True))
        oc.post("/admin/restaurant/settings", data=dict(form, dining_mode="wide open"),
                follow_redirects=True)
        s.check("a position that is not one is ignored, not saved",
                conn.execute("SELECT value FROM app_settings WHERE key = 'dining_mode'")
                .fetchone()["value"] == "closed")
        r = ec.post("/admin/restaurant/settings", data=dict(form, dining_mode="hidden"))
        s.check("an employee cannot change it",
                conn.execute("SELECT value FROM app_settings WHERE key = 'dining_mode'")
                .fetchone()["value"] == "closed" and r.status_code in (302, 403))

        s.section("Every public page reads it -- through site, not settings")
        text = visible_text(anon.get("/restaurant").get_data(as_text=True))
        s.check("the dining page answers while closed", "April 2027" in text,
                detail="it says when it reopens, which only a page reading the "
                       "switch can know")

        for mode in ("closed", "hidden"):
            s.section(f"While {mode}, nothing can be booked")
            _mode(conn, mode)
            before = _dinners(conn, EMAIL)
            r = anon.post("/restaurant/book", data={
                "guest_name": TAG + " Diner", "guest_email": EMAIL,
                "dinner_date": (m.house_today() + timedelta(days=40)).isoformat(),
                "party_size": "2", "agree_terms": "on"}, follow_redirects=True)
            s.check("a POST from a saved or direct form books nothing",
                    _dinners(conn, EMAIL) == before, detail=f"{mode}")
            s.check("and the guest is told so, in words",
                    any("not taking reservations" in f for f in flashes(r)),
                    detail=str(flashes(r)[:1]))

        s.section("The dinner added from a stay's own page is refused too")
        room = _harness.ensure_room()
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
        arrival = _harness.free_window(room["id"], 3, after_days=200)
        with m.app.test_request_context("/"):
            ref, token = m.create_booking(conn, room, TAG + " Stay", EMAIL, "",
                                          arrival, arrival + timedelta(days=3), 2, "", [],
                                          payment_status="unpaid")
        conn.execute("UPDATE bookings SET status = 'confirmed' WHERE reference_code = ?", (ref,))
        conn.commit()
        _mode(conn, "closed")
        seen = []

        def record(sender, template, context, **extra):
            seen.append((template.name, dict(context)))
        template_rendered.connect(record, m.app)
        try:
            page = anon.get(f"/book/manage/{token}").get_data(as_text=True)
            account = f"tok{TAG.lower()}acct"
            now = datetime.now(timezone.utc)
            conn.execute("DELETE FROM guest_sessions WHERE token = ?", (account,))
            conn.execute("INSERT INTO guest_sessions (email, token, created_at, expires_at) "
                         "VALUES (?, ?, ?, ?)", (EMAIL, account, now.isoformat(),
                                                 (now + timedelta(hours=2)).isoformat()))
            conn.commit()
            anon.get(f"/my-account/{account}")
        finally:
            template_rendered.disconnect(record, m.app)
        s.check("the stay's page offers no dinner while closed",
                'value="book_dinner"' not in page)
        # THE ROUTES' OWN FLAGS, not only what the page draws. The templates
        # check the switch too, so the page alone cannot tell a route that says
        # "no dinner" from one that says "dinner" and is overruled -- and the
        # flag is what anything else built on these routes would read.
        flags = {name: ctx for name, ctx in seen}
        s.check("the stay's route says dinner is not available",
                flags.get("manage_booking.html", {}).get("dinner_available") is False,
                detail=str(flags.get("manage_booking.html", {}).get("dinner_available")))
        s.check("and the guest's account says the restaurant is not open",
                flags.get("guest_account.html", {}).get("restaurant_open") is False,
                detail=str(flags.get("guest_account.html", {}).get("restaurant_open")))
        before = _dinners(conn, EMAIL)
        anon.post(f"/book/manage/{token}", data={
            "action": "book_dinner", "dinner_date": (arrival + timedelta(days=1)).isoformat(),
            "dinner_party_size": "2"}, follow_redirects=True)
        s.check("and a direct POST adds none", _dinners(conn, EMAIL) == before)
        _mode(conn, None)
        page = anon.get(f"/book/manage/{token}").get_data(as_text=True)
        s.check("open again, the stay's page offers it",
                'value="book_dinner"' in page,
                detail="the refusal must not outlive the switch")
    finally:
        m.send_email = was_send
        _mode(conn, None)
        conn.execute("UPDATE restaurant_settings SET enabled = ? WHERE id = 1",
                     (was_enabled["enabled"] if was_enabled else 0,))
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email = ?", (EMAIL,))
        conn.execute("DELETE FROM bookings WHERE guest_email = ?", (EMAIL,))
        conn.execute("DELETE FROM guest_sessions WHERE email = ?", (EMAIL,))
        conn.commit()
        conn.close()
    return s
