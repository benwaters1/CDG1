"""A room sold on Airbnb must not be sellable here on the same night.

This is the worst failure the guest side has: two parties arrive, one room. The
machinery for it existed and none of it was tested — the parser, the sync, the
fail-safe when a feed goes down, and whether a channel block actually stops a
booking going through.

Three things make it safe, and each is checked here by breaking it:

  - the sync REPLACES a source's blocks wholesale, so re-importing is
    repeatable: a range cancelled on the other side stops blocking, and one
    still in the feed is never duplicated.
  - a feed that fails to fetch keeps the blocks it already had. Clearing them
    on a network error would open a booked room to the public the moment
    Airbnb had a bad minute, which is the expensive direction to fail.
  - the public booking form refuses the dates, rather than only greying them
    out. A date picker is a suggestion; the POST is what takes the money.

No network here. parse_ical_ranges is pure, and fetch_ical_ranges is the seam
the rest is exercised through.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZICAL"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM blocked_dates WHERE ical_source_id IN
                    (SELECT id FROM ical_sources WHERE label LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM ical_sync_log WHERE ical_source_id IN
                    (SELECT id FROM ical_sources WHERE label LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _ics(ranges):
    """A feed the way a channel actually sends one: DTEND is EXCLUSIVE."""
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN"]
    for i, (start, end) in enumerate(ranges):
        out += ["BEGIN:VEVENT", f"UID:{TAG}-{i}@test",
                f"DTSTART;VALUE=DATE:{start.replace('-', '')}",
                f"DTEND;VALUE=DATE:{end.replace('-', '')}",
                "SUMMARY:Reserved", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out)


def _source(room_id, url=f"https://example.invalid/{TAG}.ics"):
    conn = db()
    conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)",
                 (room_id, f"{TAG} Airbnb", url))
    conn.commit()
    row = conn.execute("SELECT * FROM ical_sources WHERE label = ?",
                       (f"{TAG} Airbnb",)).fetchone()
    conn.close()
    return row


def _blocks(source_id):
    conn = db()
    try:
        return sorted((r["start_date"], r["end_date"]) for r in conn.execute(
            "SELECT start_date, end_date FROM blocked_dates WHERE ical_source_id = ?",
            (source_id,)).fetchall())
    finally:
        conn.close()


def _sync(source, ics=None, error=None):
    """Run a real sync with the network seam replaced."""
    original = m.fetch_ical_ranges
    if error is not None:
        m.fetch_ical_ranges = lambda url, timeout=8: (None, error)
    else:
        m.fetch_ical_ranges = lambda url, timeout=8: (m.parse_ical_ranges(ics), None)
    conn = db()
    try:
        return m.sync_ical_source(conn, source)
    finally:
        conn.close()
        m.fetch_ical_ranges = original


def run():
    s = Suite("iCal sync")
    _cleanup()
    oc, ec, owner, emp = clients()
    pub = m.app.test_client()
    room = _harness.ensure_room()

    # Far enough out that nothing else in the database is in the way.
    a1, a2 = "2032-08-10", "2032-08-13"      # DTEND exclusive: 10, 11, 12 booked
    b1, b2 = "2032-09-01", "2032-09-03"

    s.section("Reading a feed")
    parsed = m.parse_ical_ranges(_ics([(a1, a2)]))
    s.check("one reserved range comes back", len(parsed) == 1, detail=f"{parsed}")
    if parsed:
        st, en = parsed[0]
        s.check("with the dates the channel sent",
                (st.isoformat(), en.isoformat()) == (a1, a2),
                detail=f"{st}..{en}")
    s.check("junk does not raise, it just yields nothing",
            m.parse_ical_ranges("not an ics file at all") == [],
            detail="a broken feed must not be able to take the booking page down")

    s.section("Syncing writes the blocks")
    src = _source(room["id"])
    s.check("the sync reports success", _sync(src, _ics([(a1, a2), (b1, b2)])) is True)
    s.check("both ranges are stored", _blocks(src["id"]) == [(a1, a2), (b1, b2)],
            detail=f"{_blocks(src['id'])}")

    s.section("Those nights are not available here")
    conn = db()
    unavailable = m.unavailable_nights(conn, room["id"],
                                       date(2032, 8, 1), date(2032, 8, 31))
    conn.close()
    held = {str(k): v for k, v in unavailable.items()} if isinstance(unavailable, dict) \
        else {str(x): "" for x in unavailable}
    s.check("the 10th is held", any("2032-08-10" in k for k in held),
            detail=f"{sorted(held)[:6]}")
    s.check("and the 12th, the last night of the stay",
            any("2032-08-12" in k for k in held), detail=f"{sorted(held)[:6]}")
    s.check("but not the 13th, because DTEND is exclusive",
            not any("2032-08-13" in k for k in held),
            detail="treating DTEND as inclusive loses a sellable night every stay")
    s.check("and the reason says it came from a channel",
            any("another channel" in str(v).lower() for v in held.values())
            if held else False,
            detail=f"{list(held.values())[:3]}")

    s.section("A booking over those nights is refused, not just discouraged")
    # The date picker is a suggestion. The gate is is_range_available, and the
    # public form is what actually takes the money.
    conn = db()
    # It returns (ok, why) rather than a bare bool, and the reason is the point:
    # "blocked on another channel" is what tells whoever is on the phone that
    # the night is genuinely gone rather than the app being awkward.
    ok, why = m.is_range_available(conn, room["id"], date(2032, 8, 11), date(2032, 8, 12))
    conn.close()
    s.check("the gate itself says no", ok is False,
            detail="a room sold on another channel is still bookable here")
    s.check("and says it is the other channel",
            "another booking channel" in (why or "").lower(), detail=f"{why!r}")

    before = _booking_count()
    r = pub.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} Chancer", "guest_email": f"{TAG.lower()}@example.invalid",
        "guest_phone": "0600000000", "arrival_date": "2032-08-11",
        "departure_date": "2032-08-12", "party_size": "2", "adults": "2",
        "guests_under_18": "0", "notes": "", "agree_terms": "on",
    }, follow_redirects=True)
    s.check("and the public form writes nothing", _booking_count() == before,
            detail=f"{before} -> {_booking_count()}; a room sold on another "
                   "channel was sold again here")

    s.section("A feed that goes down keeps the blocks it had")
    # Clearing them on a network error opens a booked room the moment the other
    # side has a bad minute.
    kept = _blocks(src["id"])
    s.check("the sync reports failure", _sync(src, error="HTTP Error 503") is False)
    s.check("but the blocks are still there", _blocks(src["id"]) == kept,
            detail=f"{_blocks(src['id'])} — a room booked elsewhere just opened up")
    conn = db()
    row = conn.execute("SELECT last_sync_error FROM ical_sources WHERE id = ?",
                       (src["id"],)).fetchone()
    conn.close()
    s.check("and the error is on the record", "503" in (row["last_sync_error"] or ""),
            detail=f"{row['last_sync_error']!r}")

    s.section("Cancelled on the other side stops blocking here")
    s.check("a re-sync without that range succeeds",
            _sync(src, _ics([(b1, b2)])) is True)
    s.check("the cancelled range is gone", _blocks(src["id"]) == [(b1, b2)],
            detail=f"{_blocks(src['id'])}")
    s.check("and the night is sellable again",
            not _held_on(room["id"], date(2032, 8, 11)),
            detail="a cancellation on the channel never freed the night here")

    s.section("Re-importing the same feed does not pile up duplicates")
    _sync(src, _ics([(b1, b2)]))
    _sync(src, _ics([(b1, b2)]))
    s.check("still one row for one range", _blocks(src["id"]) == [(b1, b2)],
            detail=f"{_blocks(src['id'])}")

    s.section("Every run is logged, so a feed that stopped working is visible")
    conn = db()
    log = conn.execute(
        """SELECT success, added, removed, error FROM ical_sync_log
           WHERE ical_source_id = ? ORDER BY id""", (src["id"],)).fetchall()
    conn.close()
    s.check("the runs are recorded", len(log) >= 4, detail=f"{len(log)} entries")
    s.check("including the failure", any(not r["success"] for r in log))
    s.check("and what moved on the first import",
            any(r["added"] == 2 for r in log), detail=f"{[dict(r) for r in log][:2]}")

    s.section("Guards")
    s.check("an employee cannot add a feed",
            ec.post(f"/admin/rooms/{room['id']}/ical-sources/new", data={
                "label": "x", "url": "https://example.invalid/x.ics"}).status_code in (302, 403))
    s.check("nor force a sync",
            ec.post(f"/admin/ical-sources/{src['id']}/sync").status_code in (302, 403))

    _cleanup()
    return s


def _booking_count():
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE guest_name LIKE ?",
                            (TAG + "%",)).fetchone()["c"]
    finally:
        conn.close()


def _held_on(room_id, day):
    conn = db()
    try:
        held = m.unavailable_nights(conn, room_id, day, day + timedelta(days=1))
    finally:
        conn.close()
    keys = held.keys() if isinstance(held, dict) else held
    return any(day.isoformat() in str(k) for k in keys)
