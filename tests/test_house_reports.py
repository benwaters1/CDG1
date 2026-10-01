"""Four more the data was already there for: no-shows, booking pace, what
suppliers have put up, and what the house burns per night sold.

The one worth reading twice is pace. Comparing what is sold for November
against how last November FINISHED is the mistake everyone makes and it always
reads as a disaster in September. The comparison has to be against where last
year stood at the same distance out, which means counting only the bookings
that had been MADE by the equivalent date -- and that is what makes it a real
report rather than a subtraction.
"""
from datetime import timedelta
from itertools import combinations

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-hr-"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM meter_readings WHERE note LIKE ?", (TAG + "%",))
    conn.commit()


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def _stay(conn, room, ref, arrive, nights, created_on, price=400.0):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', ?, ?)""",
        (room, TAG + ref, TAG + "tk" + ref, TAG + "Guest " + ref,
         TAG + ref + "@example.invalid", arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(), price,
         created_on.isoformat() + "T12:00:00+00:00"))


class _Quiet:
    """A Suite that keeps its failures to itself, for checks asked hundreds of
    times over: one line for the whole sweep, not seven hundred."""

    def __init__(self):
        self.failed = []

    def check(self, name, ok, detail=""):
        if not ok:
            self.failed.append((name, detail))
        return bool(ok)


# Last year's three stays, by how many nights each holds. No two subsets of
# 3, 4 and 2 add to the same total, so the nights counted say exactly WHICH
# stays were counted -- a wrong answer names the stay rather than a number.
PACE_LAST_YEAR = {"PACEEARLY": 3, "PACEEDGE": 4, "PACELATE": 2}
PACE_REFS = ("PACE1",) + tuple(PACE_LAST_YEAR)


def _pace_checks(s, conn, room, today, keep=False):
    """The pace report for next month, asked as if `today` were today.

    Measured against what the report said before these stays went in, so a
    real booking in last year's month (the suite runs on a copy of the real
    database) cannot pass or fail a check that is about these four. With
    `keep` the stays are left for the suite's final cleanup.
    """
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    last_year_month = next_month.replace(year=next_month.year - 1)
    days_out = (next_month - today).days
    # The same distance out last year. occupancy_pace counts a stay as sold
    # by then if it was booked any time ON this day -- this year's figure
    # counts everything booked up to and including today, so last year's
    # has to count the whole of the equivalent day too.
    as_at = last_year_month - timedelta(days=days_out)

    def report():
        with m.app.test_request_context():
            pace = m.occupancy_pace(conn, months=2, today=today)
        return next((r for r in pace["months"] if r["month"] == next_month), None)

    before = report()
    # This year: two nights sold for next month, booked already.
    _stay(conn, room, "PACE1", next_month + timedelta(days=5), 2,
          today - timedelta(days=1))
    # Last year, all three inside the month whatever its length. Booked well
    # before the equivalent point: counted.
    _stay(conn, room, "PACEEARLY", last_year_month + timedelta(days=8), 3,
          as_at - timedelta(days=10))
    # Booked ON the equivalent day: counted, it had been made by then.
    _stay(conn, room, "PACEEDGE", last_year_month + timedelta(days=12), 4, as_at)
    # Booked the day AFTER the equivalent point: at the same distance out it
    # was not yet sold. Counted from as_at, not from the month -- the day
    # before the month begins IS as_at on the last day of a month. Never later
    # than the month's first day, so always booked before it arrives (day 5).
    _stay(conn, room, "PACELATE", last_year_month + timedelta(days=5), 2,
          as_at + timedelta(days=1))
    row = report()
    if not keep:
        conn.execute(
            "DELETE FROM bookings WHERE reference_code IN (%s)"
            % ",".join("?" * len(PACE_REFS)), [TAG + r for r in PACE_REFS])
    conn.commit()

    s.check("next month is on the report", row is not None and before is not None,
            detail=str(row))
    if row is None or before is None:
        return
    sold = row["nights"] - before["nights"]
    s.check("what is sold for it is counted", sold == 2, detail=f"{sold} of 2")
    added = row["last_nights"] - before["last_nights"]
    counted = next(
        (set(c) for k in range(len(PACE_LAST_YEAR) + 1)
         for c in combinations(PACE_LAST_YEAR, k)
         if sum(PACE_LAST_YEAR[x] for x in c) == added), None)
    said = (f"counted {sorted(counted)}" if counted is not None
            else f"{added} nights, which is no combination of the three")
    # The whole point of the report.
    s.check("last year's comparison counts only what had been booked by then",
            counted is not None and "PACEEARLY" in counted,
            detail=f"{said} — as at {as_at}")
    s.check("including a stay booked on the equivalent day itself",
            counted is not None and "PACEEDGE" in counted,
            detail=f"{said} — booked {as_at}, the day it compares against")
    s.check("and excludes what was booked after that point",
            counted is not None and "PACELATE" not in counted,
            detail=f"{said} — a stay booked later than the equivalent date "
                   "is not pace, it is hindsight")
    s.check("the date it compared against is stated",
            row["as_at_last"] == as_at, detail=f"{row['as_at_last']}, not {as_at}")


def run():
    s = Suite("house reports")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()["id"]

    # ----------------------------------------------------------- no-shows
    s.section("Tables that did not turn up")
    # Two Saturdays booked, one of them a no-show; one Tuesday that came.
    sat = today - timedelta(days=(today.weekday() - 5) % 7 + 7)
    tue = today - timedelta(days=(today.weekday() - 1) % 7 + 7)
    def table(ref, day, party, no_show=False, price=180.0):
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                 guest_email, party_size, dinner_date, status, total_price,
                 no_show_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)""",
            (TAG + ref, TAG + "tk" + ref, TAG + "Diner " + ref,
             TAG + ref + "@example.invalid", party, day.isoformat(), price,
             _now() if no_show else None, _now()))
    table("S1", sat, 4, no_show=True)
    table("S2", sat, 2)
    table("T1", tue, 2)
    conn.commit()

    with m.app.test_request_context():
        ns = m.no_show_report(conn, today - timedelta(days=30), today)
    s.check("a marked no-show is counted", ns["no_shows"] >= 1, detail=str(ns["no_shows"]))
    s.check("and the covers it held are counted with it",
            ns["covers_lost"] >= 4, detail=str(ns["covers_lost"]))

    # None and nothing are different answers. A month the restaurant was shut
    # has no no-show rate; it does not have a rate of zero, which on the tile
    # reads as a perfect record. The per-weekday rate already knew this and
    # the headline did not.
    quiet = m.no_show_report(conn, today + timedelta(days=1200),
                             today + timedelta(days=1228))
    s.check("a month with nothing booked has no rate at all",
            quiet["rate"] is None,
            detail=f"{quiet['rate']!r} — 0% on a shut month reads as nobody "
                   "having failed to turn up")
    # Asked of the page itself, over that same quiet window, because the tile
    # is what an owner reads. The first version of this check was an `or`
    # chain whose last clause was true regardless, so it passed without ever
    # loading the page.
    a = (today + timedelta(days=1200)).isoformat()
    b = (today + timedelta(days=1228)).isoformat()
    body = oc.get(f"/admin/restaurant/no-shows?from={a}&to={b}").get_data(
        as_text=True)
    tile = body[body.find("stat-tiles"):][:400]
    s.check("and the tile shows a dash rather than a figure",
            "0%" not in tile and ("&mdash;" in tile or "—" in tile),
            detail=" ".join(tile.split())[:150])
    s.check("and says there were none rather than naming a share",
            "No tables booked" in tile,
            detail=" ".join(tile.split())[:150])
    s.check("what it was worth is counted too", ns["value_lost"] >= 180.0,
            detail=str(ns["value_lost"]))
    days = {d["day"]: d for d in ns["by_day"]}
    s.check("it lands on the night it was booked for", "Saturday" in days,
            detail=str(sorted(days)))
    # Asked of THIS test's booking, not of the whole Tuesday: the suite runs
    # against a copy of the real database and another night's genuine no-show
    # would otherwise fail a check that has nothing to do with it.
    missed_refs = {r["reference"] for r in ns["recent"]}
    s.check("a table that came is not a no-show",
            TAG + "T1" not in missed_refs and TAG + "S2" not in missed_refs,
            detail=str(sorted(r for r in missed_refs if r.startswith(TAG))))
    # A night the house never opens must not read as a perfect record.
    s.check("only nights with bookings appear at all",
            all(d["booked"] > 0 for d in ns["by_day"]),
            detail="a night nobody books is not a night nobody misses")

    # ---------------------------------------------------------------- pace
    s.section("Where next month stands against the same point last year")
    # Asked once, of today, this section sees the last day of a month twelve
    # days a year -- and the last day is where it went wrong. The booking
    # meant to be "made after the equivalent point" was made the day before
    # last year's month began, which is only after that point while there is
    # more than a day to go. On the 30th of September the two were the same
    # day, it was counted, and the run went red for a reason that had nothing
    # to do with the code. So the same checks are asked of every day for a
    # year either side: every month end, both clock changes, a new year.
    # The sweep goes first and today's pass last, so today's stays are still
    # in place when the pace page is rendered further down.
    sweep_from = today - timedelta(days=365)
    red = []
    for n in range(731):
        day = sweep_from + timedelta(days=n)
        quiet = _Quiet()
        _pace_checks(quiet, conn, room, day)
        if quiet.failed:
            red.append(f"{day}: {quiet.failed[0][0]} ({quiet.failed[0][1]})")
    _pace_checks(s, conn, room, today, keep=True)
    s.check("and all of that holds on every day for a year either side",
            not red, detail=f"{len(red)} days red, first {red[:3]}")

    # ------------------------------------------------------ supplier prices
    s.section("What suppliers have put up")
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'food', 'kg', 10.0, 1, ?)""", (TAG + "Butter", _now()))
    conn.commit()
    item = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                        (TAG + "Butter",)).fetchone()["id"]
    for cost, ago in ((10.0, 200), (11.0, 100), (13.0, 10)):
        conn.execute(
            """INSERT INTO stock_movements (stock_item_id, delta, reason, unit_cost,
                 note, created_at)
               VALUES (?, 5, 'purchase', ?, ?, ?)""",
            (item, cost, TAG + "buy",
             (today - timedelta(days=ago)).isoformat() + "T09:00:00+00:00"))
    # Bought once: no second price, so no claim either way.
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'food', 'kg', 4.0, 1, ?)""", (TAG + "Saffron", _now()))
    conn.commit()
    once = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                        (TAG + "Saffron",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason, unit_cost, note, created_at)
           VALUES (?, 1, 'purchase', 4.0, ?, ?)""",
        (once, TAG + "buy", (today - timedelta(days=30)).isoformat() + "T09:00:00+00:00"))
    conn.commit()

    with m.app.test_request_context():
        prices = m.supplier_price_changes(conn, days=365)
    row = next((r for r in prices["rows"] if r["name"] == TAG + "Butter"), None)
    s.check("an item that has gone up is found", row is not None,
            detail=str([r["name"] for r in prices["rows"]][:5]))
    s.check("compared oldest against newest, not against the middle",
            row and row["old"] == 10.0 and row["new"] == 13.0,
            detail=f"{row['old'] if row else '?'} -> {row['new'] if row else '?'}")
    s.check("and the rise is a percentage of what it was",
            row and abs(row["pct"] - 30.0) < 0.5, detail=str(row["pct"] if row else None))
    # Asked with no threshold at all. At the default 5% a single reading is
    # filtered out for having moved 0%, so the check would pass whether the
    # guard existed or not -- it has to be asked where the two differ.
    with m.app.test_request_context():
        every = m.supplier_price_changes(conn, days=365, min_pct=0)
    s.check("an item bought once is not reported as unchanged",
            not any(r["name"] == TAG + "Saffron" for r in every["rows"]),
            detail="one price is not a comparison, at any threshold")
    s.check("but it is counted so the silence is visible",
            prices["single_reading"] >= 1, detail=str(prices["single_reading"]))

    # ------------------------------------------------------------- energy
    s.section("What the house burns per night sold")
    a_day = today - timedelta(days=40)
    b_day = today - timedelta(days=10)
    _stay(conn, room, "ENERGY", a_day + timedelta(days=2), 4, a_day)
    conn.commit()
    for reading, on in ((1000.0, a_day), (1300.0, b_day)):
        conn.execute(
            """INSERT INTO meter_readings (meter, read_on, reading, note, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (TAG + "elec", on.isoformat(), reading, TAG + "note", _now()))
    conn.commit()
    with m.app.test_request_context():
        energy = m.energy_per_night(conn)
    meter = next((mt for mt in energy["meters"] if mt["meter"] == TAG + "elec"), None)
    s.check("a meter with two readings gives a consumption", meter is not None,
            detail=str([mt["meter"] for mt in energy["meters"]][:5]))
    period = meter["periods"][0] if meter else None
    s.check("used is the difference between them",
            period and period["used"] == 300.0, detail=str(period["used"] if period else None))
    s.check("divided by nights SOLD in that window, not by days",
            period and period["nights"] is not None
            and period["nights"] != period["days"],
            detail=f"{period['nights'] if period else '?'} nights over "
                   f"{period['days'] if period else '?'} days")
    if period and period["nights"]:
        s.check("giving a figure per night",
                abs(period["per_night"] - 300.0 / period["nights"]) < 0.02,
                detail=str(period["per_night"]))
    else:
        s.check("and with nothing sold it says so rather than dividing by zero",
                period and period["per_night"] is None and period["note"],
                detail=str(period["note"] if period else None))

    # A meter that goes backwards has been replaced. Counting it would show
    # the house generating electricity.
    conn.execute(
        """INSERT INTO meter_readings (meter, read_on, reading, note, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (TAG + "elec", (today - timedelta(days=5)).isoformat(), 20.0, TAG + "note", _now()))
    conn.commit()
    with m.app.test_request_context():
        energy = m.energy_per_night(conn)
    meter = next((mt for mt in energy["meters"] if mt["meter"] == TAG + "elec"), None)
    s.check("a meter that goes backwards is not counted as consumption",
            meter and any(p["used"] is None for p in meter["periods"]),
            detail=str([p["used"] for p in meter["periods"]] if meter else None))
    s.check("and it is called out rather than dropped",
            energy["unreadable"] >= 1, detail=str(energy["unreadable"]))

    s.section("Every page renders with something on it")
    for path in ("/admin/restaurant/no-shows", "/management/pace",
                 "/management/supplier-prices", "/management/energy",
                 "/admin/restaurant/no-shows.csv", "/management/pace.csv",
                 "/management/supplier-prices.csv", "/management/energy.csv"):
        r = oc.get(path)
        s.check(f"{path} renders with data on it", r.status_code == 200,
                detail="HTTP %s" % r.status_code)

    s.section("Only the owner")
    for path in ("/admin/restaurant/no-shows", "/management/pace",
                 "/management/supplier-prices", "/management/energy"):
        s.check(f"an employee cannot open {path}",
                ec.get(path).status_code in (302, 403),
                detail="HTTP %s" % ec.get(path).status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
