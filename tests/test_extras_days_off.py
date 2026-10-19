"""The charcuterie reminder is never on a Sunday.

The owner, 19 October 2026: "the charcuterie reminder can't be on a Sunday".
The crémant and charcuterie board needs three days' notice, so a board for a
Wednesday was to be arranged by the Sunday -- the day the charcutier is shut.
The reminder was a task on the calendar for a day nobody could act on it.

So an extra can say which days its supplier is shut, and:

  - its "arrange by" steps BACK off them: three days before a Wednesday is the
    Saturday, which still gives the notice, not the Monday, which would not;
  - a reminder already late lands on the next day they open, never on a day
    they are shut (it is "today" otherwise, and today might be the Sunday);
  - the board says Sunday, on a house that already has it (once, so the owner
    can untick it) and on one that adds it fresh;
  - the catalogue page sets and shows the days, and clears them.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZDOFF"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _next(weekday, after):
    """The first `weekday` (Monday 0) at least a fortnight after `after`."""
    day = after + timedelta(days=14)
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def run():
    s = Suite("the charcuterie reminder is never on a Sunday")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    try:
        s.section("The rule")
        wed = _next(2, date(2026, 10, 19))
        s.check("three days before a Wednesday is a Sunday", (wed - timedelta(days=3)).weekday() == 6)
        s.check("so with the supplier shut on Sundays it is the Saturday before",
                m.extra_arrange_by(wed, 3, frozenset({6})) == wed - timedelta(days=4),
                detail=str(m.extra_arrange_by(wed, 3, frozenset({6}))))
        s.check("and with no days off it is the Sunday, as it always was",
                m.extra_arrange_by(wed, 3) == wed - timedelta(days=3))
        s.check("shut Saturday and Sunday, it is the Friday",
                m.extra_arrange_by(wed, 3, frozenset({5, 6})) == wed - timedelta(days=5))
        s.check("a supplier shut every day cannot loop for ever: the notice alone decides",
                m.extra_arrange_by(wed, 3, frozenset(range(7))) == wed - timedelta(days=3))
        sunday = wed - timedelta(days=3)
        s.check("a late reminder on a Sunday is for the Monday",
                m.extra_open_day(sunday, frozenset({6})) == sunday + timedelta(days=1))
        s.check("and on an open day stays where it is",
                m.extra_open_day(sunday - timedelta(days=1), frozenset({6})) == sunday - timedelta(days=1))
        s.check("the column reads as weekdays, junk and all",
                m.extra_days_off("6") == {6} and m.extra_days_off("5,6") == {5, 6}
                and m.extra_days_off("") == frozenset() and m.extra_days_off(None) == frozenset()
                and m.extra_days_off("x,9,6") == {6})

        s.section("The board says Sunday")
        board = next((e for k, e in m.ADDED_EXTRAS if "charcut" in e["name"].lower()), None)
        s.check("a house that adds the board fresh adds it shut on Sundays",
                board and board.get("arrange_days_off") == "6", detail=str(board))
        conn = db()
        conn.execute("DELETE FROM app_settings WHERE key = 'extras_sunday_2026_10_19'")
        conn.execute("""INSERT INTO extras (name, price, category, lead_time_days, active,
                        arrange_days_off) VALUES (?, 80, 'food', 3, 1, '')""",
                     (f"{TAG} Charcuterie platter",))
        conn.execute("""INSERT INTO extras (name, price, category, lead_time_days, active,
                        arrange_days_off) VALUES (?, 80, 'food', 3, 1, '0')""",
                     (f"{TAG} Charcuterie the owner set",))
        conn.commit()
        changed = m.apply_extras_sunday_2026_10_19(conn)
        days = {r["name"]: r["arrange_days_off"] for r in conn.execute(
            "SELECT name, arrange_days_off FROM extras WHERE name LIKE ?", (TAG + "%",))}
        s.check("a house that already has one gets Sunday put on it",
                changed >= 1 and days.get(f"{TAG} Charcuterie platter") == "6", detail=str(days))
        s.check("but not over days the owner has chosen",
                days.get(f"{TAG} Charcuterie the owner set") == "0", detail=str(days))
        conn.execute("UPDATE extras SET arrange_days_off = '' WHERE name = ?",
                     (f"{TAG} Charcuterie platter",))
        conn.commit()
        s.check("and only once: unticked afterwards, it stays unticked",
                m.apply_extras_sunday_2026_10_19(conn) == 0
                and conn.execute("SELECT arrange_days_off FROM extras WHERE name = ?",
                                 (f"{TAG} Charcuterie platter",)).fetchone()[0] == "")
        conn.execute("UPDATE extras SET arrange_days_off = '6' WHERE name = ?",
                     (f"{TAG} Charcuterie platter",))
        extra_id = conn.execute("SELECT id FROM extras WHERE name = ?",
                                (f"{TAG} Charcuterie platter",)).fetchone()[0]

        s.section("On the run sheet and the calendar")
        room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        arrive = wed - timedelta(days=1)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 600, ?)""",
            (room["id"], f"{TAG}-A", f"tok{TAG}a".lower(), f"{TAG} Guest",
             f"{TAG.lower()}@example.invalid", arrive.isoformat(),
             (arrive + timedelta(days=3)).isoformat(), _now()))
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}-A",)).fetchone()[0]
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, extra_id, name, unit_price, quantity,
               status, scheduled_for, created_at) VALUES ('room', ?, ?, ?, 80, 1, 'confirmed', ?, ?)""",
            (bid, extra_id, f"{TAG} Charcuterie platter", wed.isoformat(), _now()))
        conn.commit()
        row = next((r for r in m.extras_due(conn, wed - timedelta(days=10))["rows"]
                    if r["line"]["booking_id"] == bid), None)
        s.check("the run sheet says arrange it by the Saturday",
                row and row["arrange_by"] == (wed - timedelta(days=4)).isoformat(),
                detail=str(row and row["arrange_by"]))
        with m.app.test_request_context("/"):
            found, _dropped = m.watch_task_findings(conn, wed - timedelta(days=10))
        mine = [f for f in found if f[0] == "extras" and TAG in f[1]]
        s.check("and its reminder is on the Saturday, not the Sunday",
                mine and mine[0][3] == (wed - timedelta(days=4)).isoformat()
                and m.parse_date(mine[0][3]).weekday() != 6,
                detail=str([f[3] for f in mine]))
        with m.app.test_request_context("/"):
            found, _dropped = m.watch_task_findings(conn, sunday)
        mine = [f for f in found if f[0] == "extras" and TAG in f[1]]
        s.check("left until the Sunday itself, it is for the Monday, the first day anybody can ring",
                mine and mine[0][3] == (sunday + timedelta(days=1)).isoformat(),
                detail=str([f[3] for f in mine]))
        conn.close()

        s.section("The catalogue page")
        r = oc.post(f"/admin/extras/{extra_id}/edit", data={
            "name": f"{TAG} Charcuterie platter", "price": "80", "category": "food",
            "lead_time_days": "3", "arrange_days_off": ["5", "6", "9", "x"]},
            follow_redirects=True)
        conn = db()
        saved = conn.execute("SELECT arrange_days_off FROM extras WHERE id = ?", (extra_id,)).fetchone()[0]
        conn.close()
        s.check("ticking Saturday and Sunday saves both, and nothing that is not a day",
                saved == "5,6", detail=f"{saved!r} {flashes(r)}")
        page = oc.get("/admin/extras").get_data(as_text=True)
        s.check("the catalogue says so beside the notice", "not arranged on Sat, Sun" in page)
        s.check("and the form offers the days to tick, Monday to Sunday",
                page.count('name="arrange_days_off"') >= 14 and "Supplier shut on" in page)
        oc.post(f"/admin/extras/{extra_id}/edit", data={
            "name": f"{TAG} Charcuterie platter", "price": "80", "category": "food",
            "lead_time_days": "3"}, follow_redirects=True)
        conn = db()
        saved = conn.execute("SELECT arrange_days_off FROM extras WHERE id = ?", (extra_id,)).fetchone()[0]
        conn.close()
        s.check("unticking them all clears it", saved == "", detail=repr(saved))
        r = oc.post("/admin/extras/new", data={
            "name": f"{TAG} Fresh bread", "price": "6", "category": "food",
            "lead_time_days": "1", "arrange_days_off": "0"}, follow_redirects=True)
        conn = db()
        made = conn.execute("SELECT arrange_days_off FROM extras WHERE name = ?",
                            (f"{TAG} Fresh bread",)).fetchone()
        conn.close()
        s.check("a new extra can be shut on a Monday from the start",
                made and made[0] == "0", detail=str(made and made[0]))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
