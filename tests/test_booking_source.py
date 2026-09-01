"""Where a booking came from, and what the mix looks like.

Nothing recorded this. The house could not tell a guest who found it themselves
from one an agent sent, so "how much of this is direct" — the question a small
hotel lives on — had no answer at all, and neither did "is the place growing or
just retaining".

Two decisions carry most of the weight here:

  - STAMPED BY THE PATH, not chosen on a form. The app already knows which door
    each booking came through, and a field somebody has to remember to set is a
    field that is mostly wrong. The one thing it cannot know is that a walk-in
    first saw the house on an agent's listing, so a person can correct it.
  - A RETURNING GUEST IS ITS OWN SOURCE, promoted ahead of the raw path. Someone
    who has stayed before booking through the website is the house's own
    audience; a stranger doing the same is new business, and lumping them
    together hides whether the place is growing.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZSRC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action IN ('book_room', 'walk_in')")
    conn.commit()
    conn.close()


def _room(sleeps=2):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM rooms WHERE active = 1 AND max_occupancy >= ? "
            "ORDER BY id LIMIT 1", (sleeps,)).fetchone()
    finally:
        conn.close()


def _booked(name_like):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
            (name_like + "%",)).fetchone()
    finally:
        conn.close()


def _raw(ref, *, source, email, arrival, nights=2, price=400.0, status="confirmed"):
    """A stay written straight in, for the mix arithmetic."""
    conn = db()
    room = _room()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, source, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, ?, 0, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         email, arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         status, price, source, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _mix(start, end):
    conn = db()
    try:
        return m.booking_source_mix(conn, start.isoformat(), end.isoformat())
    finally:
        conn.close()


def run():
    s = Suite("Where a booking came from")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("The path stamps it, so nobody has to remember")
    room = _room()
    arrival = house_today() + timedelta(days=45)
    r = anon.post(f"/book/{room['id']}", data={
        "arrival_date": arrival.isoformat(),
        "departure_date": (arrival + timedelta(days=2)).isoformat(),
        "guest_name": f"{TAG} Website", "guest_email": "zzsrc.web@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    web = _booked(f"{TAG} Website")
    s.check("a website booking is taken", web is not None,
            detail=f"HTTP {r.status_code}")
    s.check("and marked direct", web and web["source"] == "direct",
            detail=f"{web['source']!r} if web else None — a field somebody has to "
                   "set is a field that is mostly wrong")

    s.section("The desk says desk")
    wi = house_today() + timedelta(days=60)
    oc.post("/admin/bookings/walk-in", data={
        "room_id": str(room["id"]),
        "arrival_date": wi.isoformat(),
        "departure_date": (wi + timedelta(days=1)).isoformat(),
        "guest_name": f"{TAG} Desk", "guest_email": "", "guest_phone": "",
        "party_size": "2", "guests_under_18": "0", "special_requests": "",
        "charge": "200", "payment_method": "cash",
    }, follow_redirects=True)
    desk = _booked(f"{TAG} Desk")
    s.check("a stay taken at the door says so", desk and desk["source"] == "desk",
            detail=f"{desk['source'] if desk else None!r}")

    s.section("A guest who has STAYED before is its own source")
    # Departed, not merely booked. Somebody with a confirmed stay still ahead of
    # them booking a second one is one person planning one trip; counting that
    # as returning would make a good week of forward bookings look like loyalty.
    _raw("Past", source="direct", email="zzsrc.web@example.invalid",
         arrival=house_today() - timedelta(days=40), nights=2, price=400)
    again = house_today() + timedelta(days=90)
    anon2 = m.app.test_client()
    anon2.post(f"/book/{room['id']}", data={
        "arrival_date": again.isoformat(),
        "departure_date": (again + timedelta(days=2)).isoformat(),
        "guest_name": f"{TAG} Website Again", "guest_email": "zzsrc.web@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    second = _booked(f"{TAG} Website Again")
    s.check("it reads as returning", second and second["source"] == "returning",
            detail=f"{second['source'] if second else None!r} — counted as direct "
                   "it is impossible to tell growth from retention")

    s.section("But a second booking before the first stay is not")
    anon3 = m.app.test_client()
    anon3.post(f"/book/{room['id']}", data={
        "arrival_date": (again + timedelta(days=30)).isoformat(),
        "departure_date": (again + timedelta(days=32)).isoformat(),
        "guest_name": f"{TAG} Planner", "guest_email": "zzsrc.plan@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    anon4 = m.app.test_client()
    anon4.post(f"/book/{room['id']}", data={
        "arrival_date": (again + timedelta(days=60)).isoformat(),
        "departure_date": (again + timedelta(days=62)).isoformat(),
        "guest_name": f"{TAG} Planner Two", "guest_email": "zzsrc.plan@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    planner = _booked(f"{TAG} Planner Two")
    s.check("two forward bookings are not loyalty",
            planner and planner["source"] != "returning",
            detail=f"{planner['source'] if planner else None!r} — a good week of "
                   "forward bookings would read as guests won back")

    s.section("A person can correct it, for the one thing the app cannot know")
    r = oc.post(f"/admin/bookings/{desk['id']}/edit", data={
        "arrival_date": desk["arrival_date"],
        "departure_date": desk["departure_date"],
        "party_size": "2", "guests_under_18": "0", "guest_phone": "",
        "special_requests": "", "source": "agent",
    }, follow_redirects=True)
    s.check("it changes", (_booked(f"{TAG} Desk") or {})["source"] == "agent",
            detail="a walk-in who first saw the house on a listing is the one "
                   "case the app cannot work out for itself")
    r = oc.post(f"/admin/bookings/{desk['id']}/edit", data={
        "arrival_date": desk["arrival_date"],
        "departure_date": desk["departure_date"],
        "party_size": "2", "guests_under_18": "0", "guest_phone": "",
        "special_requests": "", "source": "not_a_source",
    }, follow_redirects=True)
    s.check("and nonsense is ignored rather than stored",
            (_booked(f"{TAG} Desk") or {})["source"] == "agent",
            detail=f"{(_booked(f'{TAG} Desk') or {})['source']!r}")

    s.section("The mix counts nights, not bookings")
    # Four one-night stays from an agent against one guest taking a fortnight is
    # not four to one in any sense the owner would act on.
    _cleanup()
    start = house_today() + timedelta(days=200)
    end = start + timedelta(days=30)
    for i in range(4):
        _raw(f"A{i}", source="agent", email=f"zzsrc.a{i}@example.invalid",
             arrival=start + timedelta(days=i), nights=1, price=200)
    _raw("LONG", source="direct", email="zzsrc.long@example.invalid",
         arrival=start + timedelta(days=10), nights=14, price=2800)
    mix = _mix(start, end)
    by = {r["key"]: r for r in mix["rows"]}
    s.check("the agent has four nights", by["agent"]["nights"] == 4,
            detail=f"{by['agent']}")
    s.check("direct has fourteen", by["direct"]["nights"] == 14,
            detail=f"{by['direct']}")
    s.check("so direct leads on nights",
            mix["rows"][0]["key"] == "direct",
            detail=f"{[r['key'] for r in mix['rows']]} — counted as bookings the "
                   "agent would appear to be four times the business")
    s.check("and the shares add to a hundred",
            abs(sum(r["nights_pct"] for r in mix["rows"]) - 100) < 0.2,
            detail=f"{[(r['key'], r['nights_pct']) for r in mix['rows']]}")

    s.section("A stay is clipped to the window, like everywhere else")
    _cleanup()
    _raw("STRADDLE", source="direct", email="zzsrc.s@example.invalid",
         arrival=start - timedelta(days=3), nights=10, price=1000)
    mix = _mix(start, end)
    s.check("only the nights inside it count", mix["nights"] == 7,
            detail=f"{mix['nights']} — a stay beginning before the window counted "
                   "whole makes a month look busier than it was")
    s.check("and the money is split with them",
            abs(mix["revenue"] - 700) < 1, detail=f"{mix['revenue']}")

    s.section("Nothing recorded is its own row, and it is last")
    _cleanup()
    _raw("OLD", source=None, email="zzsrc.old@example.invalid",
         arrival=start + timedelta(days=1), nights=9, price=900)
    _raw("NEW", source="direct", email="zzsrc.new@example.invalid",
         arrival=start + timedelta(days=1), nights=2, price=200)
    mix = _mix(start, end)
    keys = [r["key"] for r in mix["rows"]]
    s.check("it is not folded into direct", "unrecorded" in keys,
            detail=f"{keys} — every booking taken before this was written has no "
                   "source, and calling those direct reports a number the house "
                   "never measured")
    s.check("and it sits last despite being the biggest",
            keys[-1] == "unrecorded",
            detail=f"{keys} — at the top it reads as a channel called Not recorded")
    s.check("the count is offered so it can be chased",
            mix["unrecorded"] == 9, detail=f"{mix['unrecorded']}")

    s.section("The report shows it")
    # Naming the window, because the fixture's stays are 200 days out and the
    # page defaults to this month — the same trap as the labour report, where a
    # check asked a page about a period it had never set.
    body = oc.get(f"/admin/reports/guest?period=month&date={start.isoformat()}"
                  ).get_data(as_text=True)
    s.check("the section is there", "Where they came from" in body)
    s.check("every table is wrapped for a phone",
            body.count("<table") == body.count('class="table-wrap"'))

    s.section("Guards")
    s.check("an employee cannot read the guest report",
            ec.get("/admin/reports/guest").status_code in (302, 403))

    _cleanup()
    return s
