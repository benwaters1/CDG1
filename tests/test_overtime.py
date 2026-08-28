"""Long weeks, with a memory.

The dashboard says who is over 35 hours THIS week and is used in exactly one
place. A single week cannot distinguish a bad week from how the house runs,
and those want opposite answers — one a conversation, the other another pair
of hands.

The checks that matter: weeks are Monday-start in local time (a Sunday night
shift belongs to the week it was rostered in), the run of consecutive long
weeks actually breaks when somebody has a normal one, and no euro figure
appears anywhere.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-ot-"


def _monday(offset_weeks=0):
    today = datetime.now(m.LOCAL_TZ).date()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)


def _cleanup(conn):
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name, role="employee"):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', ?, ?, 'General', 'active', ?)""",
        (f"{TAG}{name}@example.invalid", role, name,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _work(conn, uid, day, hours, at_hour=9):
    start = datetime(day.year, day.month, day.day, at_hour, tzinfo=m.LOCAL_TZ)
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
        (uid, start.astimezone(timezone.utc).isoformat(),
         (start + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()))
    conn.commit()


def _long_week(conn, uid, monday, hours=42):
    """Spread `hours` over Mon-Fri of that week."""
    per = hours / 5
    for d in range(5):
        _work(conn, uid, monday + timedelta(days=d), per)


def _week(data, monday):
    return next((w for w in data["weeks"] if w["week_start"] == monday.isoformat()), None)


def run():
    s = Suite("long weeks")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("A long week is counted, a normal one is not")
    ana = _person(conn, "Ana")
    _long_week(conn, ana, _monday(-2), hours=42)
    _long_week(conn, ana, _monday(-1), hours=30)      # a normal week

    data = m.overtime_history(conn, weeks=6)
    long_week = _week(data, _monday(-2))
    ok_week = _week(data, _monday(-1))
    s.check("the long week is flagged", long_week and long_week["over"],
            detail=str(long_week["over"]) if long_week else "")
    s.check("with the hours past the standard week, not the total",
            long_week and abs(long_week["extra_hours"] - 7) < 0.05,
            detail=str(long_week["extra_hours"]) if long_week else "")
    s.check("a thirty-hour week is not flagged", ok_week and ok_week["over"] == [],
            detail=str(ok_week["over"]) if ok_week else "")
    s.check("but its hours are still counted",
            ok_week and abs(ok_week["worked_hours"] - 30) < 0.05,
            detail=str(ok_week["worked_hours"]) if ok_week else "")

    s.section("Every week in the window appears, empty or not")
    # A page that only lists bad weeks cannot show that most weeks are fine,
    # which is the context that makes a bad one mean something.
    s.check("all six weeks are present", len(data["weeks"]) == 6,
            detail=str(len(data["weeks"])))

    s.section("The owner is not held to the employee standard")
    boss = _person(conn, "Boss", role="owner")
    _long_week(conn, boss, _monday(-2), hours=60)
    data = m.overtime_history(conn, weeks=6)
    names = {p["name"] for w in data["weeks"] for p in w["over"]}
    s.check("an owner working sixty hours is not listed as overtime",
            "Boss" not in names, detail=str(names))
    # week_overtime only looks at the CURRENT week, so asserting against hours
    # written into week -2 was true whatever the employee filter did. Give the
    # owner a long week THIS week, which is the only window that function sees.
    today = datetime.now(m.LOCAL_TZ).date()
    _long_week(conn, boss, _monday(0), hours=60)
    week_names = {p["name"] for p in m.week_overtime(conn, today)}
    s.check("the dashboard's weekly figure scopes the same way",
            "Boss" not in week_names, detail=str(week_names))
    # ...and proves it is looking at all, rather than returning nothing.
    grafter = _person(conn, "Grafter")
    _long_week(conn, grafter, _monday(0), hours=48)
    s.check("while an employee's long week IS on the dashboard figure",
            "Grafter" in {p["name"] for p in m.week_overtime(conn, today)},
            detail=str({p["name"] for p in m.week_overtime(conn, today)}))

    s.section("Several long weeks running")
    ben = _person(conn, "Ben")
    for wk in (-3, -2, -1):
        _long_week(conn, ben, _monday(wk), hours=45)
    data = m.overtime_history(conn, weeks=6)
    s.check("three in a row is called chronic",
            any(c["name"] == "Ben" for c in data["chronic"]),
            detail=str(data["chronic"]))

    # The run must BREAK on a normal week, or "three in a row" means nothing.
    cal = _person(conn, "Cal")
    _long_week(conn, cal, _monday(-4), hours=45)
    _long_week(conn, cal, _monday(-3), hours=45)
    _long_week(conn, cal, _monday(-2), hours=20)      # normal — breaks the run
    _long_week(conn, cal, _monday(-1), hours=45)
    data = m.overtime_history(conn, weeks=6)
    s.check("a normal week in the middle breaks the run",
            not any(c["name"] == "Cal" for c in data["chronic"]),
            detail=str(data["chronic"]))

    s.section("What was on that week")
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    mon = _monday(-2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Guest', 'g@example.invalid', ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "BK", TAG + "tk", mon.isoformat(),
         (mon + timedelta(days=3)).isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    data = m.overtime_history(conn, weeks=6)
    s.check("the week says what the house was doing",
            "stay" in (_week(data, mon)["on"] or ""),
            detail=str(_week(data, mon)["on"]))

    s.section("The page")
    page = oc.get("/admin/overtime?weeks=12").get_data(as_text=True)
    s.check("it renders", "Long weeks" in page)
    # The whole reason there is no cost column: pay_rate is free text.
    s.check("it states plainly that it puts no money on the hours",
            "would be invented" in page)
    s.check("and that what was on is not an explanation",
            "does not prove one caused the other" in page)
    s.check("there is no euro figure anywhere on it", "€" not in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
