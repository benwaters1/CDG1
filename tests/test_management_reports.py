"""Four questions the house could not ask: how does this year compare, how far
ahead do people book, what does a booking cost to get, and what did the
cancellations actually cost.

Each of these is a number somebody will make a decision on, so the checks are
mostly about the ways a report lies quietly:

  - a year lined up by the calendar can hold a different number of weekends
    than the year it is being compared to, which moves occupancy more than
    anything anybody did;
  - an average lead time is dragged by one booking made two years out;
  - a channel with no commission rate entered is not a free channel;
  - a cancellation four months out and one two days out are not the same loss.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-mr-"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key LIKE ?",
                 (m.CHANNEL_COMMISSION_PREFIX + "%",))
    conn.commit()


def _room(conn):
    return conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()["id"]


def _book(conn, ref, arrive, depart, *, booked_on=None, price=400.0,
          status="confirmed", source=None, decided=None, reason=None):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, source, created_at, decided_at, cancel_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?)""",
        (_room(conn), TAG + ref, TAG + "tok" + ref, TAG + "Guest " + ref,
         TAG + ref + "@example.invalid", arrive.isoformat(), depart.isoformat(),
         status, price, source,
         (booked_on or arrive).isoformat() + "T12:00:00+00:00",
         decided.isoformat() + "T12:00:00+00:00" if decided else None, reason))
    conn.commit()


def run():
    s = Suite("management reports")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()

    # ---------------------------------------------------------------- shape
    s.section("How far ahead, and how long")
    # Booked 200 days out for 3 nights, and one booked the same day for 1.
    _book(conn, "FAR", today - timedelta(days=30), today - timedelta(days=27),
          booked_on=today - timedelta(days=230))
    _book(conn, "SAMEDAY", today - timedelta(days=20), today - timedelta(days=19),
          booked_on=today - timedelta(days=20))
    with m.app.test_request_context():
        shape = m.booking_shape(conn, today - timedelta(days=365), today)
    bands = {r["band"]: r["count"] for r in shape["lead_rows"]}
    s.check("a booking made the same day lands in Same day",
            bands.get("Same day", 0) >= 1, detail=str(bands))
    s.check("and one made two hundred days out lands in 6 months or more",
            bands.get("6 months or more", 0) >= 1, detail=str(bands))
    stay_bands = {r["band"]: r["count"] for r in shape["stay_rows"]}
    s.check("a one-night stay is counted as one night",
            stay_bands.get("One night", 0) >= 1, detail=str(stay_bands))
    s.check("and a three-night stay is not", stay_bands.get("3-4 nights", 0) >= 1,
            detail=str(stay_bands))

    # The whole reason for a median. One absurd booking must not move it.
    before = shape["median_lead"]
    _book(conn, "MAD", today - timedelta(days=10), today - timedelta(days=9),
          booked_on=today - timedelta(days=900))
    with m.app.test_request_context():
        after = m.booking_shape(conn, today - timedelta(days=365), today)
    # The property, stated so it holds at any sample size: the typical figure
    # stays down near the ordinary bookings instead of being dragged towards
    # the outlier. An average would sit between the two.
    s.check("the typical figure stays nowhere near the freak booking",
            after["median_lead"] is not None and after["longest_lead"]
            and after["median_lead"] < after["longest_lead"] / 3,
            detail=f"typical {after['median_lead']} against a longest of "
                   f"{after['longest_lead']} — an average would land between them")
    s.check("but the freak booking is still reported as the longest",
            after["longest_lead"] is not None and after["longest_lead"] >= 800,
            detail=str(after["longest_lead"]))

    # A booking entered after the guest arrived is a walk-in typed up later.
    _book(conn, "WALKIN", today - timedelta(days=40), today - timedelta(days=39),
          booked_on=today - timedelta(days=35))
    with m.app.test_request_context():
        walk = m.booking_shape(conn, today - timedelta(days=365), today)
    # Counted, not inferred from a symptom. A negative lead has no band to
    # fall into, so a broken guard does not produce a negative number -- it
    # quietly files a walk-in under "6 months or more" and the only visible
    # trace is that one more booking got a band than has a lead time.
    expected = 0
    for row in conn.execute(
            """SELECT arrival_date, created_at FROM bookings
                 WHERE status = 'confirmed' AND arrival_date >= ? AND arrival_date < ?""",
            ((today - timedelta(days=365)).isoformat(), today.isoformat())).fetchall():
        arrival = m.parse_date(row["arrival_date"])
        with m.app.test_request_context():
            booked = m.house_date(row["created_at"])
        if arrival and booked and (arrival - booked).days >= 0:
            expected += 1
    banded = sum(r["count"] for r in walk["lead_rows"])
    s.check("a booking entered after the guest arrived gets no lead time at all",
            banded == expected,
            detail=f"{banded} banded against {expected} with a real lead time — "
                   "a walk-in typed up later is not a booking made in advance")

    # ------------------------------------------------------------- channels
    s.section("What a booking costs to get")
    _book(conn, "OTA1", today - timedelta(days=15), today - timedelta(days=13),
          price=1000.0, source="booking.com")
    _book(conn, "DIR1", today - timedelta(days=14), today - timedelta(days=12),
          price=1000.0, source="direct")
    with m.app.test_request_context():
        chan = m.channel_cost(conn, today - timedelta(days=365), today)
    rows = {r["source"]: r for r in chan["rows"]}
    s.check("each source is its own line",
            "booking.com" in rows and "direct" in rows, detail=str(list(rows)))
    # The point of the whole page: an unpriced channel is unknown, not free.
    s.check("a channel with no rate set shows no commission",
            rows["booking.com"]["cost"] is None, detail=str(rows["booking.com"]))
    s.check("and it is named as unpriced rather than passed off as nil",
            "booking.com" in chan["unpriced"], detail=str(chan["unpriced"]))
    s.check("so it is not counted in the commission total", chan["cost"] == 0,
            detail=str(chan["cost"]))

    oc.post("/management/channels/settings",
            data={"rate_booking_com": "15", "rate_direct": "0"}, follow_redirects=True)
    with m.app.test_request_context():
        chan = m.channel_cost(conn, today - timedelta(days=365), today)
    rows = {r["source"]: r for r in chan["rows"]}
    s.check("once a rate is entered the commission is worked out",
            rows["booking.com"]["cost"] == 150.0, detail=str(rows["booking.com"]["cost"]))
    s.check("and what the house keeps follows it",
            rows["booking.com"]["net"] == 850.0, detail=str(rows["booking.com"]["net"]))
    # Nil is a real answer once somebody types it, and different from blank.
    s.check("a rate of nil is a rate, not an unknown",
            rows["direct"]["cost"] == 0 and "direct" not in chan["unpriced"],
            detail=str(rows["direct"]))

    bad = oc.post("/management/channels/settings",
                  data={"rate_booking_com": "150"}, follow_redirects=True)
    s.check("a commission over 100 per cent is refused", bad.status_code == 200,
            detail="HTTP %s" % bad.status_code)
    with m.app.test_request_context():
        s.check("and the old rate still stands",
                m.channel_commission(conn, "booking.com") == 15.0,
                detail=str(m.channel_commission(conn, "booking.com")))
    words = oc.post("/management/channels/settings",
                    data={"rate_booking_com": "about fifteen"}, follow_redirects=True)
    s.check("so is a rate that is not a number", words.status_code == 200)
    with m.app.test_request_context():
        s.check("and again nothing was overwritten",
                m.channel_commission(conn, "booking.com") == 15.0,
                detail=str(m.channel_commission(conn, "booking.com")))
        # Blank means unknown. It has to CLEAR the rate rather than store 0,
        # or there would be no way back to "we do not know".
        oc.post("/management/channels/settings",
                data={"rate_booking_com": ""}, follow_redirects=True)
        s.check("clearing a rate returns it to unknown, not to nil",
                m.channel_commission(conn, "booking.com") is None,
                detail=str(m.channel_commission(conn, "booking.com")))

    # --------------------------------------------------------- cancellations
    s.section("What the cancellations cost")
    _book(conn, "LATE", today + timedelta(days=3), today + timedelta(days=5),
          price=800.0, status="cancelled", decided=today, reason="Changed plans")
    _book(conn, "EARLY", today + timedelta(days=200), today + timedelta(days=202),
          price=800.0, status="cancelled", decided=today, reason="Changed plans")
    with m.app.test_request_context():
        canc = m.cancellation_analysis(conn, today - timedelta(days=365),
                                       today + timedelta(days=365))
    refs = [x["reference"] for x in canc["late"]]
    s.check("one cancelled three days out is too late to re-sell",
            TAG + "LATE" in refs, detail=str(refs))
    # The distinction the page exists to make.
    s.check("one cancelled two hundred days out is not",
            TAG + "EARLY" not in refs, detail=str(refs))
    s.check("the late ones carry what they were worth",
            any(x["value"] == 800.0 for x in canc["late"]), detail=str(canc["late"][:2]))
    s.check("the rate counts cancelled against everything booked",
            canc["total"] >= canc["lost"] > 0 and canc["rate"] > 0,
            detail=f"{canc['lost']}/{canc['total']} = {canc['rate']}%")
    s.check("a cancellation with no reason is called Not recorded",
            all(r["reason"] for r in canc["reasons"]),
            detail=str([r["reason"] for r in canc["reasons"]][:4]))

    # -------------------------------------------------------- year on year
    s.section("This year against last")
    with m.app.test_request_context():
        period = m.resolve_period("month", today.isoformat())
        cal = m.year_on_year(conn, period, "calendar")
        wk = m.year_on_year(conn, period, "weekday")
    s.check("the calendar view looks at the same dates last year",
            cal["prev_start"].year == period["start"].year - 1
            and cal["prev_start"].month == period["start"].month,
            detail=str(cal["prev_start"]))
    s.check("the week view looks 52 weeks back",
            (period["start"] - wk["prev_start"]).days == 364,
            detail=str((period["start"] - wk["prev_start"]).days))
    # The reason both exist: 52 weeks back lands on the same weekday.
    s.check("so the week view lands on the same day of the week",
            wk["prev_start"].weekday() == period["start"].weekday(),
            detail=f"{wk['prev_start'].strftime('%A')} vs {period['start'].strftime('%A')}")
    s.check("and the page says which alignment it used",
            cal["alignment_label"] != wk["alignment_label"],
            detail=f"{cal['alignment_label']} / {wk['alignment_label']}")
    s.check("an alignment nobody offers falls back rather than failing",
            m.year_on_year(conn, period, "sideways")["alignment"] == "calendar")

    # A stay inside the current window, so the three headline figures are
    # actually exercised. With nothing sold they are all zero and the check
    # below would pass on a page that had confused them.
    _book(conn, "THISMONTH", period["start"] + timedelta(days=1),
          period["start"] + timedelta(days=3), price=900.0)
    with m.app.test_request_context():
        cal = m.year_on_year(conn, period, "calendar")

    keys = {mtr["key"] for mtr in cal["metrics"]}
    s.check("occupancy, the average night and revenue per room are all there",
            {"occupancy", "adr", "revpar"} <= keys, detail=str(sorted(keys)))
    # RevPAR is the one that cannot be gamed by moving the other two.
    revpar = next(x for x in cal["metrics"] if x["key"] == "revpar")
    occ = next(x for x in cal["metrics"] if x["key"] == "occupancy")
    rev = next(x for x in cal["metrics"] if x["key"] == "revenue")
    s.check("with a stay in the window all three are non-zero",
            (occ["now"] or 0) > 0 and (rev["now"] or 0) > 0 and (revpar["now"] or 0) > 0,
            detail=f"occupancy {occ['now']}, revenue {rev['now']}, revpar {revpar['now']}")
    # RevPAR is revenue over EVERY room-night the house had to sell, which is
    # the whole point of it: filling the house by halving the rate moves
    # occupancy up and this figure down.
    s.check("revenue per available room is revenue over the whole capacity",
            revpar["now"] is not None and cal["now"]["capacity"]
            and abs(revpar["now"] - rev["now"] / cal["now"]["capacity"]) < 0.02,
            detail=f"{revpar['now']} vs {rev['now']}/{cal['now']['capacity']}")
    s.check("and it is not the average night in disguise",
            revpar["now"] != next(x for x in cal["metrics"] if x["key"] == "adr")["now"],
            detail="revpar equals ADR, which only happens at 100% occupancy")
    s.check("a year with nothing in it is called out, not shown as infinite growth",
            isinstance(cal["no_history"], bool), detail=str(cal["no_history"]))

    s.section("Only the owner")
    for path in ("/management/year-on-year", "/management/booking-shape",
                 "/management/channels"):
        r = ec.get(path)
        s.check(f"an employee cannot open {path}", r.status_code in (302, 403),
                detail="HTTP %s" % r.status_code)
    denied = ec.post("/management/channels/settings",
                     data={"rate_direct": "99"}, follow_redirects=False)
    s.check("nor set a commission rate", denied.status_code in (302, 403),
            detail="HTTP %s" % denied.status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
