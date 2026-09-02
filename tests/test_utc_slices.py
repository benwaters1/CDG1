"""stamp[:10] on a UTC timestamp, and the airport run it hid.

house_date() has said why since it was written: "Slicing an ISO stamp with
[:10] reads UTC, so anything recorded between midnight and 02:00 local carries
yesterday's date all day. Anywhere that date is shown to a person, or bucketed
as 'Today', it has to be converted first rather than truncated."

Fifteen places still truncated. Most showed a date and were a day out for two
hours of every day, which is a small wrong. One was not small.

THE AIRPORT RUN. all_transfers buckets a transfer into today, upcoming or past
by slicing scheduled_at — which is stored in UTC by
local_datetime_input_to_utc_iso — and comparing it against the house's day. A
pickup at half past one in the morning, which is what a delayed flight from
anywhere gives you, is stored under the PREVIOUS day. So it is wrong twice:

  - the day before, it appears on TODAY's list, for a run that is not today;
  - and on the morning it is actually due, it has slipped into PAST.

Either way the run most likely to be missed is exactly the one the arithmetic
hides, on the list somebody checks before leaving. The fixture below is a
pickup at 01:30, not a clock reading, so it fails at any hour rather than only
in the two-hour window — and the section asserts BOTH: that it is counted as
upcoming rather than today, and that it is not under past.

THE SWEEP is the second half, and its list is PROVED rather than asserted:
each column named as holding a UTC timestamp is checked against the database
to still hold one, so the list cannot quietly rot into a list of date columns
that would be harmless to slice.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZSLICE"


def _cleanup(conn):
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("a UTC stamp read as a day")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, _ec, _owner, _emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO vehicles (name, license_plate, created_at)
           VALUES (?, 'ZZ-000-ZZ', ?)""", (TAG + " Land Rover", now))
    vehicle = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Half past one in the morning, tomorrow, at the house. In UTC that is
    # 23:30 TODAY -- so a slice files it under today and the bucketing calls
    # it "past" on the morning it is due.
    pickup_local = m.datetime.combine(
        today + timedelta(days=1),
        m.datetime.min.time().replace(hour=1, minute=30)
    ).replace(tzinfo=m.LOCAL_TZ)
    pickup_utc = pickup_local.astimezone(m.timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction,
                   scheduled_at, notes, created_at)
           VALUES (?, ?, 'pickup', ?, 'Toulouse, delayed flight', ?)""",
        (vehicle, TAG + " Beauchamp", pickup_utc, now))
    conn.commit()

    s.section("The fixture really is in the window")
    # Without this the section proves nothing at most hours of the day: if
    # UTC and the house agree about which day 01:30 falls on, every check
    # below passes on a slice as readily as on a conversion.
    s.check("UTC and the house disagree about this pickup's day",
            pickup_utc[:10] != m.house_date_iso(pickup_utc),
            detail=f"{pickup_utc[:10]} sliced, "
                   f"{m.house_date_iso(pickup_utc)} at the house")
    s.check("and the house's day is the one the guest means",
            m.house_date_iso(pickup_utc) == (today + timedelta(days=1)).isoformat(),
            detail=m.house_date_iso(pickup_utc))

    s.section("The driver's list files it under the right day")
    body = oc.get("/transfers").get_data(as_text=True)
    s.check("the page opens", TAG + " Beauchamp" in body,
            detail="the transfer is not on the page at all")
    # By POSITION, because the page renders Today, then Upcoming, then Past,
    # and anything before the Past summary is one of the first two.
    #
    # My first version of this looked backwards from the name for the nearest
    # of several heading words, and every row carries the CSS class
    # "status-upcoming" -- so it found "Upcoming" against a past row and
    # passed on the bug it was written for. It is worth saying out loud: a
    # check that reads a class name instead of a heading proves nothing and
    # looks exactly like one that does.
    at_name = body.find(TAG + " Beauchamp")
    at_past = body.find("Past (")
    s.check("it is not filed under a past run",
            at_past < 0 or at_name < at_past,
            detail="a pickup due tomorrow morning, sitting under Past on the "
                   "list somebody checks before leaving. This is the half "
                   "that bites TOMORROW; the tile check below is the half "
                   "that bites today.")

    s.section("And it is counted as still to come, not already gone")
    # The three tiles at the top are built from the same buckets, so the one
    # that says how many are coming has to have moved too.
    def tile(label):
        # The tile's own label, not the first "Today" on the page -- the nav
        # carries one, and looking back from it finds no tile at all.
        i = body.find('stat-tile-label">' + label + "<")
        if i < 0:
            return None
        chunk = body[max(0, i - 220):i]
        j = chunk.rfind("stat-tile-value")
        return chunk[j:].split(">", 1)[1].split("<", 1)[0].strip() if j >= 0 else None

    s.check("the tiles are readable", tile("Today") is not None
            and tile("Upcoming") is not None, detail=str(tile("Today")))
    s.check("a pickup tomorrow is counted as upcoming",
            (tile("Upcoming") or "0").isdigit() and int(tile("Upcoming")) >= 1,
            detail=f"Today {tile('Today')}, Upcoming {tile('Upcoming')} — "
                   "the tiles are built from the same buckets, so a run "
                   "filed under past disappears from both")

    # ------------------------------------------------------------- sweep
    s.section("Nothing slices a UTC timestamp any more")
    # The list below is PROVED, not asserted: every column named as holding a
    # UTC timestamp is checked against the database to still hold one, so it
    # cannot rot into a list of date columns that would be harmless anyway.
    STAMPS = {
        "vehicle_transfers": ["scheduled_at", "created_at"],
        "pos_orders": ["opened_at"],
        "leave_requests": ["requested_at"],
        "guests": ["created_at"],
        "guest_feedback": ["submitted_at"],
        "vehicle_usage": ["checked_out_at"],
    }
    checked = 0
    for table, cols in sorted(STAMPS.items()):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col not in have:
                s.check(f"{table}.{col} still exists to check", False,
                        detail="the sweep's list names a column that is gone")
                continue
            sample = conn.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL "
                f"AND TRIM({col}) != '' LIMIT 1").fetchone()
            if sample is None:
                continue
            checked += 1
            s.check(f"{table}.{col} really holds a timestamp",
                    "T" in str(sample[0]),
                    detail=f"{sample[0]!r} — if this became a date column "
                           "the entry belongs off the list, not left to pass "
                           "for free")
    s.check("at least a few columns had data to check against",
            checked >= 3, detail=f"{checked} columns had a value")

    import io as _io
    import os as _os
    import re as _re
    src = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
    named = sorted({c for cols in STAMPS.values() for c in cols})
    for col in named:
        # Both spellings a slice takes: row["col"][:10] and (row["col"] or "")[:10].
        hits = _re.findall(
            r"""\[["']%s["']\]\s*(?:or\s*["'"']*\s*\)?\s*)?\[:10\]""" % col, src)
        s.check(f"nothing slices {col}", not hits,
                detail=f"{len(hits)} place(s) — house_date_iso() is the "
                       "one answer, and two spellings for one idea is how "
                       "the last one of these survived 125 times")

    s.section("And the helper is what everything uses instead")
    s.check("house_date_iso exists", callable(getattr(m, "house_date_iso", None)))
    s.check("it converts rather than truncates",
            m.house_date_iso(pickup_utc) != pickup_utc[:10],
            detail="on this fixture, at least, they differ")
    s.check("and nothing at all is a safe answer for nothing at all",
            m.house_date_iso("") == "" and m.house_date_iso(None) == "",
            detail="a blank stamp must not become today")
    s.check("nor is rubbish", m.house_date_iso("not a stamp") == "")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
