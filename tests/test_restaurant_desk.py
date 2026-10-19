"""A table the house takes itself, and the reservations by night.

19 October 2026, reviewing the restaurant page: online reservations are
switched off and there was no other way to write one down, so a dinner
booked on the telephone had nowhere to go. And every reservation was a card
of its own in one long column, so "how is Saturday looking" meant adding up
cards.

WHAT THIS PINS.
  - Add a reservation: confirmed at once, sends nothing unless asked (no
    "request received" letter for a table the house took itself), and with
    "Email them a confirmation" sends the confirmation.
  - Refused for a guest under a standing instruction not to accept them, for
    a night that has gone, and with no name.
  - Covers past what the room seats are said.
  - The list heads each night with its covers against the room, what is
    still to decide, and who is working.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZRSDESK"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE 'zzrsdesk.%'")
    conn.execute("DELETE FROM guests WHERE email LIKE 'zzrsdesk.%'")
    conn.commit()


def _letters(conn, to):
    return [r["template_key"] for r in conn.execute(
        "SELECT template_key FROM mail_log WHERE to_address = ? ORDER BY id", (to,))]


def run():
    s = Suite("A table the house takes itself")
    conn = db()
    oc, ec, _o, _e = clients()
    _cleanup(conn)
    try:
        _checks(s, conn, oc, ec)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn, oc, ec):
    night = m.service_day() + timedelta(days=9)
    settings = m.get_restaurant_settings(conn)
    seats = settings["capacity"] if settings else 0

    s.section("Adding one")
    to = "zzrsdesk.quiet@example.invalid"
    r = oc.post("/admin/restaurant/new", data={
        "dinner_date": night.isoformat(), "party_size": "4", "guest_name": f"{TAG} Quiet",
        "guest_email": to, "guest_phone": "+33 6 11 22 33 44", "dietary_notes": "One vegetarian"})
    row = conn.execute("SELECT * FROM restaurant_bookings WHERE guest_name = ?", (f"{TAG} Quiet",)).fetchone()
    s.check("it is written down", row is not None)
    if not row:
        return
    s.check("confirmed at once", row["status"] == "confirmed" and row["decided_at"])
    s.check("with its party, night and notes", row["party_size"] == 4
            and row["dinner_date"] == night.isoformat() and row["dietary_notes"] == "One vegetarian")
    s.check("and nothing sent: no 'request received' for a table the house took", _letters(conn, to) == [],
            detail=str(_letters(conn, to)))
    s.check("the desk lands on that night", r.headers.get("Location", "").endswith(
        "#night-" + night.strftime("%Y%m%d")), detail=r.headers.get("Location", ""))

    told = "zzrsdesk.told@example.invalid"
    oc.post("/admin/restaurant/new", data={
        "dinner_date": night.isoformat(), "party_size": "2", "guest_name": f"{TAG} Told",
        "guest_email": told, "tell_them": "1"})
    s.check("with 'Email them a confirmation', they get the confirmation",
            _letters(conn, told) == ["restaurant_confirmed"], detail=str(_letters(conn, told)))
    oc.post("/admin/restaurant/new", data={
        "dinner_date": night.isoformat(), "party_size": "2", "guest_name": f"{TAG} Phone only",
        "guest_phone": "06 98 76 54 32", "tell_them": "1"})
    s.check("a telephone booking with no address is taken too",
            conn.execute("SELECT 1 FROM restaurant_bookings WHERE guest_name = ?",
                         (f"{TAG} Phone only",)).fetchone() is not None)

    s.section("What it refuses")
    def count(name):
        return conn.execute("SELECT COUNT(*) FROM restaurant_bookings WHERE guest_name = ?",
                            (name,)).fetchone()[0]
    oc.post("/admin/restaurant/new", data={
        "dinner_date": (m.service_day() - timedelta(days=1)).isoformat(), "party_size": "2",
        "guest_name": f"{TAG} Late"})
    s.check("a night that has gone", count(f"{TAG} Late") == 0)
    oc.post("/admin/restaurant/new", data={"dinner_date": night.isoformat(), "party_size": "2",
                                           "guest_name": ""})
    s.check("no name", conn.execute("SELECT COUNT(*) FROM restaurant_bookings WHERE guest_name = ''"
                                     ).fetchone()[0] == 0)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO guests (name, email, caution_level, caution, created_at)
                    VALUES (?, 'zzrsdesk.no@example.invalid', 'refuse', 'Walked out on a bill', ?)""",
                 (f"{TAG} Refused", now))
    conn.commit()
    r = oc.post("/admin/restaurant/new", data={
        "dinner_date": night.isoformat(), "party_size": "2", "guest_name": f"{TAG} Refused",
        "guest_email": "zzrsdesk.no@example.invalid"}, follow_redirects=True)
    s.check("a guest under a standing instruction not to accept them",
            count(f"{TAG} Refused") == 0 and "standing instruction" in " ".join(flashes(r)))
    s.check("staff without the owner's rights are turned away",
            ec.post("/admin/restaurant/new", data={"dinner_date": night.isoformat(), "party_size": "2",
                                                   "guest_name": f"{TAG} Staff"}).status_code in (302, 403)
            and count(f"{TAG} Staff") == 0)

    if seats:
        r = oc.post("/admin/restaurant/new", data={
            "dinner_date": night.isoformat(), "party_size": str(seats), "guest_name": f"{TAG} Big"},
            follow_redirects=True)
        s.check("a night past what the room seats is said", "over the" in " ".join(flashes(r)),
                detail=" ".join(flashes(r)))

    s.section("The list, by night")
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
                    party_size, dinner_date, status, created_at)
                    VALUES ('RST-ZZRSDESKP', 'zzrsdeskp', ?, 'zzrsdesk.p@example.invalid', 3, ?, 'pending', ?)""",
                 (f"{TAG} Pending", night.isoformat(), now))
    conn.commit()
    page = oc.get("/admin/restaurant", query_string={"q": TAG}).get_data(as_text=True)
    head = page[page.find(f'id="night-{night.strftime("%Y%m%d")}"'):]
    head = head[:head.find("<table")]
    covers = conn.execute("SELECT COALESCE(SUM(party_size), 0) FROM restaurant_bookings WHERE dinner_date = ? "
                          "AND status IN ('pending', 'confirmed')", (night.isoformat(),)).fetchone()[0]
    s.check("each night heads its tables", bool(head) and m.format_date_short(night.isoformat()) in head)
    s.check("with its covers against what the room seats",
            f"<b>{covers}</b>" in head and (not seats or f"of {seats}" in head), detail=head[-300:])
    s.check("and what is still to decide", "1 to decide" in head)
    s.check("and who is working", "nobody on yet" in head or " on" in head)
    s.check("the form to add one is on the page", "/admin/restaurant/new" in page)


if __name__ == "__main__":
    print(run().report())
