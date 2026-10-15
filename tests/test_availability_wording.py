"""What a guest is told when their dates are taken.

The owner, 15 October 2026: never "free" -- it reads as "no charge" -- and a
guest should not hear about the house's other booking channels. The quote
said "Those dates are no longer free.", and the booking form and the guest's
own date change passed on "...overlap an existing booking" and "...blocked on
another booking channel" word for word. Staff screens keep the detail.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ROOT
import _harness

m = _harness.m
TAG = "ZZWORDING"
TAKEN = "Those dates are already reserved. Please choose others."


def run():
    s = Suite("Taken dates, in a guest's words")
    oc, _ec, _o, _e = clients()
    c = m.app.test_client()

    s.section("The house's reasons, translated")
    s.check("a clash with a booking becomes the one plain sentence",
            m.for_a_guest(m.DATES_OVERLAP_BOOKING) == TAKEN)
    s.check("and so does a block from another channel, which a guest never hears about",
            m.for_a_guest(m.DATES_BLOCKED_CHANNEL) == TAKEN)
    s.check("a season closure keeps its own note, written for guests",
            m.for_a_guest("Closed for the winter.") == "Closed for the winter.")

    s.section("On the pages a guest sees")
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    start = m.house_today() + timedelta(days=400)
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'Zz Wording', 'zz@example.invalid', ?, ?, 1, 'confirmed', 200, ?)""",
        (room["id"], TAG + "A", TAG + "tokA", start.isoformat(),
         (start + timedelta(days=2)).isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    try:
        r = c.get("/api/quote", query_string={
            "room_id": room["id"], "arrival": start.isoformat(),
            "departure": (start + timedelta(days=1)).isoformat(), "party_size": 1})
        reason = (r.get_json() or {}).get("reason") or ""
        s.check("the quote for taken dates says they are already reserved", reason == TAKEN,
                detail=reason)
        s.check("and never 'free'", "free" not in reason.lower(), detail=reason)
    finally:
        conn = db()
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()

    s.section("No public page says 'free' for dates")
    found = []
    for path in sorted(__import__("glob").glob(f"{ROOT}/templates/*.html")):
        src = re.sub(r"\{#.*?#\}|<!--.*?-->", " ", open(path, encoding="utf-8").read(), flags=re.S)
        if re.search(r"\bwhat (else )?is free\b", src, re.I):
            found.append(path.rsplit("templates", 1)[1])
    s.check("'what is free' and 'what else is free' appear nowhere a guest reads",
            not found, detail=str(found))
    return s


if __name__ == "__main__":
    print(run().report())
