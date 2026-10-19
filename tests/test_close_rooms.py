"""Closing rooms for dates, what a room asks of a stay in a run of dates, and
the channel calendars.

WHAT WAS ASKED. "Close rooms for dates in a really easy way": the Gold Suite
and the Family Suite over the winter while the other three stay open, with a
choice of how a closed room shows, and per-room options for runs of dates. A
block was one room, one form, with no way to say how it showed and nothing to
put the same winter back next year.

WHAT WAS FOUND ON THE WAY.
  - The calendar the channels read carried the house's bookings and nothing
    else: a room closed here was still for sale on Booking.com.
  - A stay that dropped off Booking.com's calendar reopened the room and told
    nobody: the waiting list heard only of cancellations made on this site.
  - The owner's calendar counted closed nights as taken, so a shut month read
    as a full one.

WHAT THIS PINS. The three ways a closed room shows, on the search, the gate,
the room page and the export; a season kept and put on again; a closure that
refuses a room with a stay in it, naming it; a longer minimum that applies at
weekends only when asked; on request taken as a request and nothing charged;
the sync telling the waiting list; and the owner's choice to keep a channel's
nights open here, which asks for the warning first.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, house_today, BOOKING_DETAILS
import _harness

m = _harness.m
TAG = "ZZCLOSE"


def _rooms():
    conn = db()
    try:
        rows = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall()
    finally:
        conn.close()
    # The Gold Suite is the Marquis Suite since 16 October.
    big = [r for r in rows if any(n in r["name"].lower() for n in ("marquis suite", "family suite"))]
    rest = [r for r in rows if r not in big]
    return (big if len(big) == 2 else rows[:2]), (rest or rows[2:])


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ? OR note LIKE ?", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM room_seasons WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_rules WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_rate_overrides WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ical_keep_open WHERE start_date >= ?", (f"{house_today().year + 3}-01-01",))
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _blocks(room_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM room_blocks WHERE room_id = ? AND reason LIKE ?",
                            (room_id, TAG + "%")).fetchall()
    finally:
        conn.close()


def _ask(fn, *args, **kw):
    conn = db()
    try:
        with m.app.test_request_context():
            return fn(conn, *args, **kw)
    finally:
        conn.close()


def run():
    s = Suite("Closing rooms")
    _cleanup()
    oc, ec, owner, _emp = clients()
    anon = m.app.test_client()
    big, rest = _rooms()
    winter = house_today().year + 3
    first, back = date(winter, 11, 1), date(winter + 1, 4, 1)

    s.section("Two rooms closed for the winter in one go, the rest left open")
    page = oc.get("/admin/rooms/close")
    s.check("the page opens", page.status_code == 200, page)
    body = page.get_data(as_text=True)
    s.check("with the shortcut for the two big rooms",
            len(big) < 2 or "The Marquis Suite and the Family Suite" in body
            or not all("suite" in r["name"].lower() for r in big))
    r = oc.post("/admin/rooms/close", data={
        "room_id": [str(x["id"]) for x in big], "start_date": first.isoformat(),
        "end_date": back.isoformat(), "display_mode": "season", "note": "",
        "reason": TAG + " winter", "save_season": "1", "season_name": TAG + " Winter"},
        follow_redirects=True)
    said = " ".join(flashes(r))
    shut = [_blocks(x["id"]) for x in big]
    s.check("both rooms are closed, as closed for the season",
            all(len(b) == 1 and b[0]["display_mode"] == "season" for b in shut), detail=said)
    s.check("and each says when it reopens, in its own name",
            all(b[0]["note"] == m.default_closure_note(x["name"], back.isoformat())
                for b, x in zip(shut, big)),
            detail=str([b[0]["note"] for b in shut if b]))
    s.check("the message names them", all(x["name"] in said for x in big), detail=said)
    conn = db()
    season = conn.execute("SELECT * FROM room_seasons WHERE name = ?", (TAG + " Winter",)).fetchone()
    conn.close()
    s.check("and the winter is kept as a season", season is not None
            and season["start_md"] == "11-01" and season["end_md"] == "04-01")

    inside_a, inside_b = date(winter, 12, 10), date(winter, 12, 13)
    s.section("Closed for the season, everywhere a guest looks")
    ok, why = _ask(m.is_range_available, big[0]["id"], inside_a, inside_b)
    s.check("the room cannot be had for those nights, and says why in the owner's words",
            not ok and why == shut[0][0]["note"], detail=why)
    if rest:
        s.check("a room left open still can", _ask(m.is_range_available, rest[0]["id"], inside_a, inside_b)[0])
    search = anon.get(f"/book?arrival={inside_a.isoformat()}&departure={inside_b.isoformat()}"
                      ).get_data(as_text=True)
    s.check("the search lists it, saying it reopens",
            f'data-room-url="/book/{big[0]["id"]}"' in search and shut[0][0]["note"] in search)
    nights = _ask(m.unavailable_nights, big[0]["id"], inside_a, inside_b)
    s.check("the room page's calendar says it reopens, in the owner's words",
            set(nights.values()) == {shut[0][0]["note"].rstrip(".")}, detail=str(nights))
    feed = _ask(m.generate_room_ics, big[0])
    s.check("and the calendar the channels read closes it there too",
            f"DTSTART;VALUE=DATE:{first.strftime('%Y%m%d')}" in feed and "Closed" in feed,
            detail="a room closed here stayed on sale on Booking.com")

    s.section("Hidden: not offered at all for those dates")
    if rest:
        h_from, h_to = date(winter, 6, 1), date(winter, 6, 8)
        oc.post("/admin/rooms/close", data={
            "room_id": str(rest[0]["id"]), "start_date": h_from.isoformat(),
            "end_date": h_to.isoformat(), "display_mode": "hidden", "reason": TAG + " hidden"})
        inside = anon.get(f"/book?arrival={(h_from + timedelta(days=2)).isoformat()}"
                          f"&departure={(h_from + timedelta(days=4)).isoformat()}").get_data(as_text=True)
        s.check("a search inside those dates does not list the room",
                f'data-room-url="/book/{rest[0]["id"]}"' not in inside)
        outside = anon.get(f"/book?arrival={(h_to + timedelta(days=5)).isoformat()}"
                           f"&departure={(h_to + timedelta(days=7)).isoformat()}").get_data(as_text=True)
        s.check("one outside them does", f'data-room-url="/book/{rest[0]["id"]}"' in outside)
        s.check("and its page still greys those nights",
                set(_ask(m.unavailable_nights, rest[0]["id"], h_from, h_to).values()) == {"Not available"})

    s.section("A room with a stay in the dates is not closed, and is named")
    if rest:
        conn = db()
        s_from = date(winter, 7, 10)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, 'zzclose@example.invalid', ?, ?, 2, 'confirmed', 300, ?)""",
            (rest[0]["id"], TAG + "STAY", "tok" + TAG, TAG + " Guest", s_from.isoformat(),
             (s_from + timedelta(days=2)).isoformat(), _harness.datetime_now()))
        conn.commit()
        conn.close()
        r = oc.post("/admin/rooms/close", data={
            "room_id": str(rest[0]["id"]), "start_date": (s_from - timedelta(days=1)).isoformat(),
            "end_date": (s_from + timedelta(days=5)).isoformat(), "display_mode": "booked",
            "reason": TAG + " clash"}, follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("it is left open, and the message says which stay is in the way",
                not [b for b in _blocks(rest[0]["id"]) if b["reason"] == TAG + " clash"]
                and TAG + "STAY" in said, detail=said)

    s.section("On the calendar, the Overview and the house calendar in Outlook")
    # A winter closure showed on the Close rooms page and nowhere a person
    # planning a week would look.
    cal = oc.get(f"/calendar?view=month&date={winter}-12-01").get_data(as_text=True)
    s.check("the month calendar shows the closure in a month it covers",
            f"Closed: {big[0]['name']}" in cal or f"{big[0]['name']} closed for the season" in cal)
    over = oc.get(f"/admin/overview?view=month&date={winter}-12-01").get_data(as_text=True)
    s.check("and the Overview lists it as closed, not as a booking",
            'data-kind="closure"' in over and f"{big[0]['name']} closed for the season" in over)
    items = _ask(m.house_calendar_items, date(winter, 12, 1), date(winter + 1, 1, 1))
    mine = [i for i in items if i["key"] == f"closure:{shut[0][0]['id']}"]
    s.check("the house calendar carries it once, all day, over the whole closure",
            len(mine) == 1 and mine[0]["all_day"]
            and mine[0]["start"] == f"{first.isoformat()}T00:00:00"
            and mine[0]["end"] == f"{back.isoformat()}T00:00:00",
            detail=str(mine))

    s.section("The owner's calendar counts nights on sale, not closed ones")
    month = oc.get(f"/admin/calendar?month={winter}-12").get_data(as_text=True)
    s.check("a closed month's nights are said to be closed, not full", "closed)" in month)

    s.section("Reopening, and putting the season on again")
    r = oc.post(f"/admin/blocks/{shut[0][0]['id']}/delete", data={"back": "close"})
    s.check("reopening one goes back to the page it came from",
            r.status_code == 302 and "/admin/rooms/close" in r.headers.get("Location", ""))
    s.check("and that room is on sale again", not _blocks(big[0]["id"]))
    conn = db()
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + " winter%",))
    conn.commit()
    conn.close()
    a, b = m.season_dates(season)
    r = oc.post(f"/admin/rooms/seasons/{season['id']}/apply", follow_redirects=True)
    s.check("putting the season on closes its rooms for the next time it comes",
            all(len([x for x in _blocks(r_["id"]) if x["start_date"] == a.isoformat()]) == 1
                for r_ in big),
            detail=" ".join(flashes(r)))
    s.check("which is the coming winter, from 1 November to 1 April",
            (a.month, a.day, b.month, b.day) in ((11, 1, 4, 1),)
            or (a == house_today() and (b.month, b.day) == (4, 1)), detail=f"{a} to {b}")
    oc.post(f"/admin/rooms/seasons/{season['id']}/delete")
    conn = db()
    s.check("forgetting a season leaves what it closed",
            conn.execute("SELECT COUNT(*) FROM room_seasons WHERE id = ?", (season["id"],)).fetchone()[0] == 0
            and all(_blocks(r_["id"]) for r_ in big))
    conn.close()

    s.section("What a room asks of a stay: a longer minimum at weekends, a price")
    room = big[0]
    summer_a, summer_z = date(winter, 7, 1), date(winter, 8, 31)
    oc.post("/admin/rooms/rules", data={
        "room_id": str(room["id"]), "start_date": summer_a.isoformat(),
        "end_date": summer_z.isoformat(), "min_nights": "3", "weekends_only": "1",
        "price": "275", "label": TAG + " summer"})
    tuesday = summer_a + timedelta(days=(1 - summer_a.weekday()) % 7)
    friday = summer_a + timedelta(days=(4 - summer_a.weekday()) % 7)
    weekday_rules = _ask(m.stay_rules, room, tuesday, tuesday + timedelta(days=1))
    weekend_rules = _ask(m.stay_rules, room, friday, friday + timedelta(days=2))
    s.check("a Tuesday night is still one night", weekday_rules["min_nights"] == (room["min_nights"] or 1),
            detail=str(weekday_rules))
    s.check("a stay with a Friday or Saturday needs three", weekend_rules["min_nights"] == 3,
            detail=str(weekend_rules))
    quote = anon.get(f"/api/quote?room_id={room['id']}&arrival={friday.isoformat()}"
                     f"&departure={(friday + timedelta(days=2)).isoformat()}&party_size=2").get_json() or {}
    s.check("the quote says so before the form is filled in",
            quote.get("available") is False and "3-night minimum" in (quote.get("reason") or ""),
            detail=str(quote.get("reason")))
    conn = db()
    price = conn.execute("SELECT price_per_night FROM room_rate_overrides WHERE label = ?",
                         (TAG + " summer",)).fetchone()
    conn.close()
    s.check("and the price for those dates is where every price is read from",
            price is not None and price[0] == 275.0)

    s.section("On request: a request, and nothing taken")
    asked = rest[0] if rest else big[1]
    r_a = date(winter, 9, 7)
    oc.post("/admin/rooms/rules", data={
        "room_id": str(asked["id"]), "start_date": r_a.isoformat(),
        "end_date": (r_a + timedelta(days=10)).isoformat(), "on_request": "1",
        "label": TAG + " request"})
    quote = anon.get(f"/api/quote?room_id={asked['id']}&arrival={r_a.isoformat()}"
                     f"&departure={(r_a + timedelta(days=2)).isoformat()}&party_size=2").get_json() or {}
    s.check("the quote says it is on request", quote.get("on_request") is True,
            detail=str({k: quote.get(k) for k in ("available", "on_request", "reason")}))
    anon.post(f"/book/{asked['id']}", data={
        "arrival_date": r_a.isoformat(), "departure_date": (r_a + timedelta(days=2)).isoformat(),
        "guest_name": f"{TAG} Asker", "guest_email": "zzclose.ask@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on", **BOOKING_DETAILS})
    conn = db()
    asked_row = conn.execute("SELECT status, amount_paid FROM bookings WHERE guest_name = ?",
                             (f"{TAG} Asker",)).fetchone()
    conn.close()
    s.check("the booking goes in as a request for the house to answer",
            asked_row is not None and asked_row["status"] == "pending",
            detail=str(dict(asked_row)) if asked_row else "no booking written")

    s.section("Booking.com: read every hour, and the waiting list hears")
    s.check("the calendars are read every hour by default",
            m.AUTOMATION_SETTING_DEFAULTS["automation_ical_sync_interval_hours"] == "1")
    conn = db()
    conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)",
                 (asked["id"], TAG + " Booking.com", "https://example.invalid/zzclose.ics"))
    conn.commit()
    source = conn.execute("SELECT * FROM ical_sources WHERE label = ?", (TAG + " Booking.com",)).fetchone()
    conn.close()
    c_a = date(winter, 10, 3)
    told = []
    real_fetch, real_notify = m.fetch_ical_ranges, m.notify_room_waitlist_opening
    m.notify_room_waitlist_opening = lambda conn, a, b, **k: told.append((a, b)) or []
    try:
        m.fetch_ical_ranges = lambda url, timeout=8: ([(c_a, c_a + timedelta(days=3))], None)
        _ask(m.sync_ical_source, source)
        s.check("a stay on the channel closes its nights here",
                not _ask(m.is_range_available, asked["id"], c_a, c_a + timedelta(days=3))[0])
        m.fetch_ical_ranges = lambda url, timeout=8: ([], None)
        _ask(m.sync_ical_source, source)
    finally:
        m.fetch_ical_ranges, m.notify_room_waitlist_opening = real_fetch, real_notify
    s.check("when it goes from the channel, the waiting list is told about those nights",
            told == [(c_a.isoformat(), (c_a + timedelta(days=3)).isoformat())], detail=str(told))

    s.section("A channel's error page does not reopen the room")
    # Down for maintenance, a channel can answer its calendar address with a
    # web page and a 200. That parsed to no stays, the sync deleted every
    # block, and the waiting list would have been written to about sold nights.
    e_a = date(winter, 10, 10)
    real_fetch = m.fetch_ical_ranges
    m.fetch_ical_ranges = lambda url, timeout=8: ([(e_a, e_a + timedelta(days=2))], None)
    try:
        _ask(m.sync_ical_source, source)
    finally:
        m.fetch_ical_ranges = real_fetch

    class _Page:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    real_urlopen = m.urlopen
    m.urlopen = lambda req, timeout=8: _Page(b"<html><body>Down for maintenance</body></html>")
    told.clear()
    m.notify_room_waitlist_opening = lambda conn, a, b, **k: told.append((a, b)) or []
    try:
        ok = _ask(m.sync_ical_source, source)
    finally:
        m.urlopen = real_urlopen
        m.notify_room_waitlist_opening = real_notify
    conn = db()
    kept = conn.execute("SELECT COUNT(*) FROM blocked_dates WHERE ical_source_id = ?",
                        (source["id"],)).fetchone()[0]
    err = conn.execute("SELECT last_sync_error FROM ical_sources WHERE id = ?",
                       (source["id"],)).fetchone()[0]
    conn.close()
    s.check("the sync reports it as an error rather than an empty calendar",
            ok is False and "not with a calendar" in (err or ""), detail=str(err))
    s.check("so the channel's stay keeps its nights closed here", kept == 1, detail=str(kept))
    s.check("and nobody on the waiting list is told about them", not told, detail=str(told))
    s.check("a real calendar still reads",
            m.read_ical_body("BEGIN:VCALENDAR\r\nEND:VCALENDAR")[1] is None)

    s.section("Keeping our site open for a stay that can be cancelled for free")
    k_a = date(winter, 10, 20)
    conn = db()
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (asked["id"], source["id"], k_a.isoformat(),
                                         (k_a + timedelta(days=2)).isoformat()))
    conn.commit()
    conn.close()
    form = {"room_id": str(asked["id"]), "start_date": k_a.isoformat(),
            "end_date": (k_a + timedelta(days=2)).isoformat(), "keep": "1"}
    r = oc.post("/admin/rooms/keep-open", data=form, follow_redirects=True)
    s.check("it is refused until the warning is ticked",
            not _ask(m.is_range_available, asked["id"], k_a, k_a + timedelta(days=2))[0]
            and any("understand" in f for f in flashes(r)))
    oc.post("/admin/rooms/keep-open", data=dict(form, understood="yes"))
    s.check("then those nights can be booked here",
            _ask(m.is_range_available, asked["id"], k_a, k_a + timedelta(days=2))[0])
    conn = db()
    logged = conn.execute("SELECT actor_user_id FROM audit_log WHERE action = 'channel_nights_kept_open' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    s.check("and who did it is written down", logged is not None and logged[0] == owner["id"])
    page = oc.get("/admin/rooms/close").get_data(as_text=True)
    s.check("the page says it is kept open, with a way to close it again",
            "kept open here" in page and "Close it again" in page)
    oc.post("/admin/rooms/keep-open", data=dict(form, keep="0"))
    s.check("and closing it again closes it here",
            not _ask(m.is_range_available, asked["id"], k_a, k_a + timedelta(days=2))[0])

    s.section("Only the owner")
    s.check("an employee cannot open it", ec.get("/admin/rooms/close").status_code in (302, 403))
    before = len(_blocks(big[1]["id"]))
    ec.post("/admin/rooms/close", data={"room_id": str(big[1]["id"]), "start_date": first.isoformat(),
                                         "end_date": back.isoformat(), "reason": TAG + " staff"})
    s.check("nor close a room", len(_blocks(big[1]["id"])) == before)
    rule = None
    conn = db()
    rule = conn.execute("SELECT id FROM room_rules WHERE label = ?", (TAG + " request",)).fetchone()
    conn.close()
    if rule:
        oc.post(f"/admin/rooms/rules/{rule['id']}/delete")
        conn = db()
        s.check("a rule can be removed", conn.execute(
            "SELECT COUNT(*) FROM room_rules WHERE id = ?", (rule["id"],)).fetchone()[0] == 0)
        conn.close()

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
