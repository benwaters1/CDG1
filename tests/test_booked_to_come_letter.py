"""A letter to everybody booked to come, quoting each one's own reference.

The owner, 10 November 2026: alongside the 2027 letters to past guests, the
waitlist and the mailing list, write "to current booked guests their
reference number". A campaign could fill in a name and an email address and
nothing else, and had no audience for people still to come. So:

  - "Booked to come" is an audience: every confirmed stay or atelier not yet
    over, one letter per person, an atelier with its emails switched off left
    out, as is anything past or cancelled;
  - {{reference}} and {{guest_page}} fill in that person's own booking -- both
    references for somebody with a room and a retreat, and the page of
    whichever comes first;
  - a letter using them is never sent to somebody with no booking, and never
    sent with the tag left showing: that person is skipped, and the send says
    why;
  - the template page offers the audience and the tags, and previews on a real
    booking.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZBTC"
LOW = TAG.lower()


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject, body))
        return True


def _email(who):
    return f"{LOW}.{who}@example.invalid"


def _clean():
    conn = db()
    like = f"{LOW}.%"
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ? OR template_name LIKE ?",
                 (like, TAG + "%"))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(conn, who, ref, days, nights=2, status="confirmed"):
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    start = house_today() + timedelta(days=days)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 300, ?)""",
        (room, ref, f"{LOW}-{ref}-token", f"{who.title()} {TAG}", _email(who), start.isoformat(),
         (start + timedelta(days=nights)).isoformat(), status, _harness.datetime_now()))


def _atelier(conn, sid, who, ref, quiet=0):
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, status, total_price, created_at, do_not_email)
           VALUES (?, ?, ?, ?, ?, 'confirmed', 2000, ?, ?)""",
        (sid, ref, f"{LOW}-{ref}-token", f"{who.title()} {TAG}", _email(who),
         _harness.datetime_now(), quiet))


def run():
    s = Suite("A letter to everybody booked to come, with their own reference")
    oc, _ec, _owner, _emp = clients()
    _clean()
    mail = Recorder()
    real = m.send_email
    m.send_email = mail
    try:
        _suite(s, oc, mail)
    finally:
        m.send_email = real
        _clean()
    return s


def _suite(s, oc, mail):
    conn = db()
    now = _harness.datetime_now()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                      active, sort_order, created_at) VALUES (?, '', 2000, 10, 1, 94, ?)""",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=200)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
                      created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} s", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()["id"]
    _stay(conn, "ann", f"GUD-{TAG}A", 30)                       # a stay to come
    _stay(conn, "bea", f"GUD-{TAG}B", 60)                       # a stay, and an atelier later
    _atelier(conn, sid, "bea", f"WRK-{TAG}B")
    _atelier(conn, sid, "cat", f"WRK-{TAG}C")                   # an atelier to come
    _atelier(conn, sid, "dee", f"WRK-{TAG}D", quiet=1)          # emails switched off
    _stay(conn, "eve", f"GUD-{TAG}E", -30)                      # been and gone
    _stay(conn, "fay", f"GUD-{TAG}F", 40, status="cancelled")   # called off
    conn.execute("""INSERT INTO campaign_templates (name, area, subject, body, created_at)
                    VALUES (?, 'workshops', 'Your reference: {{reference}}',
                    'Dear {{first_name}}, your reference is {{reference}}. Your page: {{guest_page}}',
                    ?)""", (f"{TAG} reference letter", now))
    template = conn.execute("SELECT * FROM campaign_templates WHERE name = ?",
                            (f"{TAG} reference letter",)).fetchone()
    conn.commit()

    s.section("Who is booked to come")
    with m.app.test_request_context():
        audience = m.campaign_audience(conn, ["booked"])
        bea = m.campaign_booking_context(conn, _email("bea"))
        ann = m.campaign_booking_context(conn, _email("ann"))
        eve = m.campaign_booking_context(conn, _email("eve"))
    ours = {e for e in audience if e.startswith(f"{LOW}.")}
    s.check("every stay and atelier still to come, once per person",
            ours == {_email("ann"), _email("bea"), _email("cat")}, detail=str(sorted(ours)))
    s.check("not an atelier with its emails switched off, nor a stay over or called off",
            not ours & {_email("dee"), _email("eve"), _email("fay")})
    s.check("one booking: its reference, and the link to its own page",
            ann and ann["reference"] == f"GUD-{TAG}A"
            and ann["guest_page"].endswith(f"{LOW}-GUD-{TAG}A-token"), detail=str(ann))
    s.check("a room and a retreat: both references, and the page of the one that comes first",
            bea and bea["reference"] == f"GUD-{TAG}B and WRK-{TAG}B"
            and bea["guest_page"].endswith(f"{LOW}-GUD-{TAG}B-token"), detail=str(bea))
    s.check("somebody been and gone has nothing to quote", eve is None)
    s.check("the audience is accepted by a send", m.valid_segments(["booked"]) == ["booked"])

    s.section("The letter")
    with m.app.test_request_context():
        result = m.send_campaign(conn, template, {_email("ann"): f"Ann {TAG}",
                                                  _email("bea"): f"Bea {TAG}",
                                                  _email("eve"): f"Eve {TAG}"}, None)
    got = {to: (subj, body) for to, subj, body in mail.calls}
    s.check("each booked guest gets their own reference and page",
            f"your reference is GUD-{TAG}A." in got.get(_email("ann"), ("", ""))[1]
            and got[_email("ann")][0] == f"Your reference: GUD-{TAG}A"
            and f"{LOW}-GUD-{TAG}A-token" in got[_email("ann")][1]
            and f"GUD-{TAG}B and WRK-{TAG}B" in got.get(_email("bea"), ("", ""))[1],
            detail=str(got)[:600])
    s.check("and no tag is ever left showing",
            all("{{" not in subj + body for subj, body in got.values()), detail=str(got)[:400])
    skipped = conn.execute("SELECT outcome, detail FROM campaign_sends WHERE recipient_email = ?",
                           (_email("eve"),)).fetchone()
    s.check("somebody with no booking is not written to, and the send says why",
            _email("eve") not in got and skipped and skipped["outcome"] == "skipped"
            and "no booking to come" in (skipped["detail"] or ""),
            detail=str(dict(skipped) if skipped else None))
    mail.calls.clear()
    with m.app.test_request_context():
        m.send_campaign(conn, template, {"owner-test@example.invalid": "Owner"}, None,
                        as_test=True)
    s.check("the owner's own test shows a booking that is plainly nobody's",
            mail.calls and "WRK-EXAMPLE" in mail.calls[0][2] and "{{" not in mail.calls[0][2],
            detail=str(mail.calls)[:300])
    mail.calls.clear()
    conn.close()

    s.section("The template page")
    page = oc.get(f"/admin/emails/{template['id']}").get_data(as_text=True)
    text = visible_text(page)
    s.check("offers “Booked to come”, counted", 'value="booked"' in page
            and "Booked to come (" in text, detail=text[text.find("WHO IT GOES TO"):][:300])
    s.check("lists the two new tags", "{{reference}}" in text and "{{guest_page}}" in text)
    s.check("and previews on a real booking, with nothing left in braces",
            ("your reference is GUD-" in text or "your reference is WRK-" in text)
            and "WRK-EXAMPLE" not in text,
            detail=text[text.find("Preview"):][:400])
    s.check("nothing was sent by looking", not mail.calls, detail=str(mail.calls))


if __name__ == "__main__":
    print(run().report())
