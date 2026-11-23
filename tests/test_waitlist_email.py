"""The house's note of a waitlist request says how many and why (owner, 12 Nov 2026).

A guest searched Stay for 28-29 December, found nothing and joined the
waitlist; the note to reservations@ said only "nothing was available", which
left the house guessing whether the rooms were full or the party too big.
Now it carries the party size and, room by room, the reason Stay gave on the
card -- from room_offer, the one function Stay itself asks, so the two cannot
disagree. Held here:
  - the party size, or that none was given; the phone when there is one
  - one line per room the guest could have been offered, in Stay's words
  - the same reasons the Stay page printed for the same search
  - through the form: the note that is sent is this one
"""
import html
import re
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZTWLE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("The waitlist note says how many and why")
    _cleanup()
    arrive = m.house_today() + timedelta(days=130)
    leave = arrive + timedelta(days=1)
    a, d = arrive.isoformat(), leave.isoformat()
    conn = db()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall()
    small = [r for r in rooms if (r["max_occupancy"] or 0) < 4]
    try:
        s.section("What the note says")
        with m.app.test_request_context():
            body = m.waitlist_email_body(conn, TAG + " Guest", "g@example.invalid", "+33 6 00 00 00 00",
                                         a, d, 4, "Two adults and two children")
            bare = m.waitlist_email_body(conn, TAG + " Guest", "g@example.invalid", "", a, d, None, "")
            expected = {r["name"]: m.room_offer(conn, r, arrive, leave, 4) for r in rooms}
        s.check("how many: for 4 guests", f"would like {a} to {d}, for 4 guests, but nothing was available" in body,
                detail=body[:200])
        s.check("and the phone, beside the email", "(g@example.invalid, +33 6 00 00 00 00)" in body)
        s.check("with no party size, it says so rather than guessing", "for party size not given" in bare)
        lines = dict(re.findall(r"^  (.+?): (.+)$", body, re.M))
        s.check("one line for each room Stay would have shown", set(lines) == set(expected),
                detail=f"{sorted(lines)} vs {sorted(expected)}")
        s.check("each in Stay's own words, from the same function",
                all(lines.get(n) == ("free now" if ok else why) for n, (ok, why) in expected.items()),
                detail=str([(n, lines.get(n), w) for n, (ok, w) in expected.items()][:3]))
        s.check("a room too small for four says it sleeps up to two",
                small and all(lines.get(r["name"]) == f"Sleeps up to {r['max_occupancy']}" for r in small),
                detail=str([(r["name"], lines.get(r["name"])) for r in small]))
        s.check("and the guest's own note comes last", body.rstrip().endswith("Two adults and two children"))

        s.section("The same reasons as the Stay page printed")
        page = m.app.test_client().get(f"/book?arrival={a}&departure={d}&party_size=4").get_data(as_text=True)
        shown = html.unescape(page)
        s.check("every reason in the note is on Stay's page for the same search",
                all(why in shown for ok, why in expected.values() if not ok and why),
                detail=str([w for ok, w in expected.values() if not ok and w and w not in shown][:2]))

        s.section("Through the form")
        sent = []

        def capture(to, subject, body, *args, **kwargs):
            sent.append((to, subject, body))
            return True

        real, was_enabled = m.send_email, m.email_enabled
        m.email_enabled, m.send_email = (lambda: True), capture
        try:
            had = conn.execute("SELECT value FROM app_settings WHERE key = 'contact_email'").fetchone()
            r = m.app.test_client().post("/waitlist/join", data={
                "name": TAG + " Hopeful", "email": "zt-wle@example.invalid", "desired_arrival": a,
                "desired_departure": d, "party_size": "4", "notes": TAG + " note"})
        finally:
            m.send_email, m.email_enabled = real, was_enabled
        note = [b for to, subj, b in sent if subj == "New waitlist request"]
        s.check("joining the waitlist sends the house its note", r.status_code in (302, 303) and len(note) == 1,
                detail=f"{r.status_code}, {len(note)} sent; house address set: {bool(had)}")
        s.check("and it is this note: how many, and each room's reason",
                bool(note) and "for 4 guests" in note[0] and "What each room said:" in note[0],
                detail=(note[0][:240] if note else ""))
    finally:
        conn.close()
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
