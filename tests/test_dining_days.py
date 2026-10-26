"""The evenings La Table serves, from the owner's ticks to the guest's refusal.

The restaurant settings page has drawn seven "Open on these evenings" boxes
since the 2 October handover, and nothing saved them: whatever the owner ticked
was thrown away, the boxes came back empty, and the opening hours the public
pages are built to print (_dining_hours.html) never appeared, because the keys
they read were never allowed out through PUBLIC_SETTINGS. The booking page
also told itself "the server refuses it too" about a closed evening, and the
server did not.

What is checked here, with the mail caught and Stripe pinned off by _harness:

  * the boxes save to app_settings.dining_days, 0 = Monday, and come back
    ticked; none ticked is blank and closes nothing
  * the hours reach the public pages -- the Dining page's card, the booking
    page's hint and its date picker's data-open-days -- with the dinner time
    the settings page saves, not a second copy of it
  * a closed evening is refused on the server by every door a guest can book
    a table through: the booking form, the waitlist, and the dinner added from
    a stay's own page, which also stops offering dinner when every night of
    the stay is a closed one
  * all of them read restaurant_open_weekdays, the helper that also keeps a
    closed evening from confirming itself (restaurant_instant_holds)
"""
from datetime import timedelta

from flask import template_rendered

from _harness import Suite, clients, db, flashes, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZZDAYS"
DOMAIN = "dining-days-test.invalid"
GUEST_IP = "203.0.113.84"
SETTING_KEYS = ("dining_days", "dining_mode", "dining_reopens", "restaurant_instant")
NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _email(who):
    return f"{who.lower()}@{DOMAIN}"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM restaurant_waitlist WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()


def _setting(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _stored(conn):
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'dining_days'").fetchone()
    return row["value"] if row else None


def _free_evening(conn, after_days=150):
    """An evening with no table on it, so capacity never refuses first."""
    day = m.house_today() + timedelta(days=after_days)
    for _ in range(400):
        if not conn.execute(
                "SELECT 1 FROM restaurant_bookings WHERE dinner_date IN (?, ?) "
                "AND status IN ('pending', 'confirmed')",
                (day.isoformat(), (day + timedelta(days=1)).isoformat())).fetchone():
            return day
        day += timedelta(days=1)
    raise RuntimeError("no free evening in the test database")


def _all_but(*closed):
    return ",".join(str(d) for d in range(7) if d not in closed)


def _ticked(page):
    """The weekdays whose box the settings page draws ticked."""
    return {d for d in range(7)
            if f'name="dining_days" value="{d}" checked' in page}


def _book(guest, conn, who, day):
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    r = guest.post("/restaurant/book", data={
        "guest_name": TAG + " " + who, "guest_email": _email(who), "guest_phone": "",
        "dinner_date": day.isoformat(), "party_size": "2", "dietary_notes": "",
        "promo_code": ""}, follow_redirects=True)
    row = conn.execute("SELECT * FROM restaurant_bookings WHERE guest_email = ?",
                       (_email(who),)).fetchone()
    return row, r


def _waitlist(guest, conn, who, day):
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    r = guest.post("/restaurant/waitlist/join", data={
        "name": TAG + " " + who, "email": _email(who), "phone": "",
        "desired_date": day.isoformat(), "party_size": "2", "notes": ""},
        follow_redirects=True)
    row = conn.execute("SELECT * FROM restaurant_waitlist WHERE email = ?",
                       (_email(who),)).fetchone()
    return row, r


def run():
    s = Suite("La Table's opening evenings")
    oc, _ec, _owner, _emp = clients()
    guest = m.app.test_client()
    guest.environ_base["REMOTE_ADDR"] = GUEST_IP
    conn = db()
    _cleanup(conn)
    put_settings = keep_rows(conn, "restaurant_settings", "id = 1")
    marks = ",".join("?" * len(SETTING_KEYS))
    put_app = keep_rows(conn, "app_settings", f"key IN ({marks})", SETTING_KEYS)
    was_send = m.send_email
    sent = []
    m.send_email = lambda *a, **k: (sent.append(a), True)[1]
    try:
        conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, "
                     "price_per_person = 50, deposit_percent = NULL, opening_date = NULL, "
                     "dinner_time = '19:45' WHERE id = 1")
        conn.commit()
        for key in SETTING_KEYS:
            _setting(conn, key, None)

        s.section("The words, the way the opening hours say them")
        words = m.restaurant_open_days_words
        for days, said in (({3, 4, 5, 6}, "Thursday to Sunday"),
                           ({4, 5, 6, 0}, "Friday to Monday"),
                           ({4, 5, 6}, "Friday to Sunday"),
                           ({5, 6, 0}, "Saturday to Monday"),
                           ({2, 4, 5, 6, 0}, "Wednesday and Friday to Monday"),
                           ({0, 2}, "Monday and Wednesday"),
                           ({5}, "Saturday"),
                           (set(range(7)), "every evening"),
                           (set(), "")):
            s.check(f"{sorted(days)} reads {said!r}", words(days) == said,
                    detail=repr(words(days)))

        s.section("The boxes save, and come back ticked")
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("the page draws all seven", all(
            f'name="dining_days" value="{d}"' in page for d in range(7)))
        s.check("and, unset, none is ticked", _ticked(page) == set(), detail=str(_ticked(page)))
        form = {"opening_date": "", "dinner_time": "19:45", "capacity": "20",
                "price_per_person": "50", "lead_user_id": "", "profit_share_percent": "50",
                "deposit_percent": "", "enabled": "on", "dining_mode": "open",
                "dining_reopens": ""}
        r = oc.post("/admin/restaurant/settings",
                    data=dict(form, dining_days=["6", "3", "5", "4", "9", "x"]),
                    follow_redirects=True)
        s.check("saved as the weekdays, in order, nothing else", _stored(conn) == "3,4,5,6",
                r, detail=repr(_stored(conn)))
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("and those four come back ticked, the rest not",
                _ticked(page) == {3, 4, 5, 6}, detail=str(_ticked(page)))
        s.check("the helper reads what the form saved",
                m.restaurant_open_weekdays(conn) == {3, 4, 5, 6})
        s.check("the change is in the audit log, in words",
                conn.execute("SELECT 1 FROM audit_log WHERE action = 'dining_days_set' "
                             "AND target = 'Thursday to Sunday'").fetchone() is not None)
        oc.post("/admin/restaurant/settings", data=dict(form, dining_days=["3", "4", "5", "6"]))
        s.check("saving it unchanged writes no second audit line",
                conn.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = "
                             "'dining_days_set' AND target = 'Thursday to Sunday'"
                             ).fetchone()["c"] == 1)
        oc.post("/admin/restaurant/settings", data=form)
        s.check("none ticked is blank", _stored(conn) == "", detail=repr(_stored(conn)))
        s.check("and closes no evening", m.restaurant_open_weekdays(conn) is None)
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("and no box comes back ticked", _ticked(page) == set())
        oc.post("/admin/restaurant/settings", data=dict(form, dining_days=["3", "4", "5", "6"]))

        s.section("The hours reach the public pages, with the dinner time")
        with m.app.test_request_context("/"):
            site = m.LazyPublicSettings()
            s.check("dining_days is allowed out", site.get("dining_days") == "3,4,5,6",
                    detail=repr(site.get("dining_days")))
            s.check("dining_time is the time the settings page saves",
                    site.get("dining_time") == "19:45", detail=repr(site.get("dining_time")))
        book = guest.get("/restaurant/book").get_data(as_text=True)
        s.check("the date picker greys out the closed evenings",
                'data-open-days="3,4,5,6"' in book)
        s.check("and the booking page says when La Table is open",
                "La Table is open Thursday to Sunday, from 19:45." in visible_text(book),
                detail="the hint under the date")
        info = visible_text(guest.get("/restaurant").get_data(as_text=True))
        s.check("the Dining page prints the opening hours",
                "Opening Hours" in info and "Monday Closed" in info
                and "Thursday Dinner from 19:45" in info)
        home = visible_text(guest.get("/").get_data(as_text=True))
        s.check("and the homepage words dinner by them",
                "Dinner at the château, Thursday to Sunday, from 19:45." in home)
        oc.post("/admin/restaurant/settings", data=dict(form, dinner_time="20:00",
                                                        dining_days=["3", "4", "5", "6"]))
        info = visible_text(guest.get("/restaurant").get_data(as_text=True))
        s.check("a new dinner time on the settings page is the new time on the hours",
                "Thursday Dinner from 20:00" in info and "19:45" not in info)
        _setting(conn, "dining_days", "")
        info = visible_text(guest.get("/restaurant").get_data(as_text=True))
        book = guest.get("/restaurant/book").get_data(as_text=True)
        s.check("unset, the Dining page keeps its general wording",
                "Opening Hours" not in info and "on the nights the kitchen is open" in info.lower())
        s.check("and the date picker greys nothing out", 'data-open-days=""' in book)

        s.section("A closed evening is refused by the booking form")
        D = _free_evening(conn)
        C = D + timedelta(days=1)
        _setting(conn, "dining_days", _all_but(C.weekday()))
        row, r = _book(guest, conn, "Closed", C)
        s.check("no table is written for it", row is None, detail=str(row and dict(row)))
        said = flashes(r)
        s.check("and the guest is told which evening is closed, and which are open",
                any(f"closed on {NAMES[C.weekday()]}s" in f
                    and m.restaurant_open_days_words(set(range(7)) - {C.weekday()}) in f
                    for f in said), detail=str(said[:1]))
        s.check("with what they typed still in the form",
                f'value="{_email("Closed")}"' in r.get_data(as_text=True))
        row, r = _book(guest, conn, "Open", D)
        s.check("an open evening still books", row is not None, r)
        _setting(conn, "dining_days", None)
        row, r = _book(guest, conn, "Unset", C)
        s.check("with the evenings unset, the same evening books", row is not None, r)
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
        conn.commit()

        s.section("And by the waitlist")
        _setting(conn, "dining_days", _all_but(C.weekday()))
        row, r = _waitlist(guest, conn, "WaitClosed", C)
        s.check("no place is kept on an evening nobody will be served",
                row is None, detail=str(row and dict(row)))
        s.check("and the guest is told why",
                any(f"closed on {NAMES[C.weekday()]}s" in f for f in flashes(r)),
                detail=str(flashes(r)[:1]))
        row, r = _waitlist(guest, conn, "WaitOpen", D)
        s.check("an open evening still takes a place", row is not None, r)

        s.section("And by the dinner added from a stay's own page")
        room = _harness.ensure_room()
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
        arrival = _harness.free_window(room["id"], 2, after_days=220)
        first, second = arrival, arrival + timedelta(days=1)
        with m.app.test_request_context("/"):
            ref, token = m.create_booking(conn, room, TAG + " Stay", _email("Stay"), "",
                                          arrival, arrival + timedelta(days=2), 2, "", [],
                                          payment_status="unpaid")
        conn.execute("UPDATE bookings SET status = 'confirmed' WHERE reference_code = ?", (ref,))
        conn.commit()

        seen = []

        def record(sender, template, context, **extra):
            if template.name == "manage_booking.html":
                seen.append(context.get("dinner_available"))
        template_rendered.connect(record, m.app)
        try:
            _setting(conn, "dining_days", _all_but(first.weekday(), second.weekday()))
            page = guest.get(f"/book/manage/{token}").get_data(as_text=True)
            s.check("a stay on closed evenings only is offered no dinner",
                    seen[-1:] == [False] and 'value="book_dinner"' not in page,
                    detail=str(seen[-1:]))
            _setting(conn, "dining_days", _all_but(first.weekday()))
            page = guest.get(f"/book/manage/{token}").get_data(as_text=True)
            s.check("one open evening in the stay and it is offered",
                    seen[-1:] == [True] and 'value="book_dinner"' in page,
                    detail=str(seen[-1:]))
        finally:
            template_rendered.disconnect(record, m.app)

        def dinners():
            return [r["dinner_date"] for r in conn.execute(
                "SELECT dinner_date FROM restaurant_bookings WHERE guest_email = ? "
                "ORDER BY dinner_date", (_email("Stay"),))]
        r = guest.post(f"/book/manage/{token}", data={
            "action": "book_dinner", "dinner_date": first.isoformat(),
            "dinner_party_size": "2", "dinner_dietary_notes": ""}, follow_redirects=True)
        s.check("a closed night of the stay is refused", dinners() == [], detail=str(dinners()))
        s.check("and the guest is told why",
                any(f"closed on {NAMES[first.weekday()]}s" in f for f in flashes(r)),
                detail=str(flashes(r)[:1]))
        guest.post(f"/book/manage/{token}", data={
            "action": "book_dinner", "dinner_date": second.isoformat(),
            "dinner_party_size": "2", "dinner_dietary_notes": ""})
        s.check("an open night of the same stay books", dinners() == [second.isoformat()],
                detail=str(dinners()))

        s.section("One reading of the setting, shared with the automatic confirmation")
        held = {"id": None, "dinner_date": C.isoformat(), "party_size": 2,
                "guest_email": _email("Held"), "guest_name": TAG + " Held"}
        _setting(conn, "dining_days", _all_but(C.weekday()))
        with m.app.test_request_context("/"):
            why = m.restaurant_instant_holds(conn, held)
            refused = m.restaurant_closed_evening(conn, C)
        s.check("an evening the booking form refuses is one that would never confirm itself",
                bool(refused) and any(f"closed on {NAMES[C.weekday()]}s" in w for w in why),
                detail=str(why))
        _setting(conn, "dining_days", " 3, 4 ,x,12,")
        s.check("a hand-edited value reads the same through the helper",
                m.restaurant_open_weekdays(conn) == {3, 4})
    finally:
        m.send_email = was_send
        put_settings()
        put_app()
        _cleanup(conn)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
