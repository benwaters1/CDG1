"""A phone number the house could actually reach somebody on.

Every form in this app has taken a phone number since the beginning and none
of them ever looked at it. So what is on file is whatever people typed:
"06 12 34 56 78", "06.12.34.56.78", "+33 (0)6 12 34 56 78". None of those can
be sent to. That is the same fault as an unparsed date in a date column — the
page looks right, the send goes nowhere, and it surfaces as a guest who did
not turn up.

The policy is the part worth pinning, because it is a judgement and not a rule:

  - a number that normalises is STORED normalised, so anything that later has
    to reach it can, and normalise_phone is idempotent so reading a stored
    value back gives the same answer;
  - a number that does not normalise is stored exactly as typed and the
    booking still goes through. Turning away a stay over a phone number trades
    a real booking for a tidy column.

So a bad number costs a message, never a booking. Both halves are checked,
because the first on its own would be satisfied by a form that refuses
everything and the second by a form that normalises nothing.

is_mobile_number answers True, False or None on purpose. A landline takes no
text. A foreign number cannot be judged from its prefix here, and "not known"
has to stay distinguishable from "no" — refusing to text a guest because we
could not tell would be worse than trying.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTPHONE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_waitlist WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'join_restaurant_waitlist'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Phone numbers")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("The shapes people actually write")
    for typed, expected in (
            ("06 12 34 56 78", "+33612345678"),
            ("06.12.34.56.78", "+33612345678"),
            ("06-12-34-56-78", "+33612345678"),
            ("0612345678", "+33612345678"),
            ("+33 6 12 34 56 78", "+33612345678"),
            # The trunk zero after a country code. Extremely common on French
            # cards and websites, and correct when dialling inside France.
            ("+33 (0)6 12 34 56 78", "+33612345678"),
            ("0033612345678", "+33612345678"),
            ("+44 7700 900123", "+447700900123"),
            ("+1 415 555 0123", "+14155550123")):
        s.check(f"{typed!r} becomes {expected}",
                m.normalise_phone(typed) == expected,
                detail=str(m.normalise_phone(typed)))

    s.section("And the ones that are not numbers")
    for typed in ("ask my wife", "", "   ", "123", "call the office",
                  "+336123456789012345", "06 12 34 56 7A"):
        s.check(f"{typed!r} is refused rather than guessed at",
                m.normalise_phone(typed) is None,
                detail=str(m.normalise_phone(typed)))

    s.section("Reading a stored number back gives the same number")
    # Everything downstream re-reads what is on file, so a normaliser that
    # changed its own output would corrupt a good number on every save.
    once = m.normalise_phone("06 12 34 56 78")
    s.check("normalising twice changes nothing", m.normalise_phone(once) == once,
            detail=f"{once} -> {m.normalise_phone(once)}")

    s.section("Whether it can take a text at all")
    s.check("a French mobile can", m.is_mobile_number("+33612345678") is True)
    s.check("and an 07 one", m.is_mobile_number("+33712345678") is True)
    s.check("a French landline cannot", m.is_mobile_number("+33161020304") is False,
            detail="the provider would take it, charge for it and report a "
                   "failure nobody reads")
    # Three answers, not two. "Not known" must stay distinguishable from "no".
    s.check("a foreign number is not known either way",
            m.is_mobile_number("+447700900123") is None,
            detail="refusing to text a guest because we cannot tell would be "
                   "worse than trying")
    s.check("and nothing at all is a no", m.is_mobile_number(None) is False)

    s.section("A booking stores the number in a form it could use")
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    conn.close()
    arrival = house_today() + timedelta(days=120)
    anon.post(f"/book/{room['id']}", data={
        "guest_name": TAG + " Amelie", "guest_email": "amelie@example.invalid",
        "guest_phone": "06 12 34 56 78", "party_size": "2",
        "arrival_date": arrival.isoformat(),
        "departure_date": (arrival + timedelta(days=2)).isoformat(),
        "agree_terms": "on",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute(
        "SELECT * FROM bookings WHERE guest_email = 'amelie@example.invalid' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    s.check("the booking is made", made is not None)
    s.check("and the number is stored ready to send to",
            made and made["guest_phone"] == "+33612345678",
            detail=str(made["guest_phone"]) if made else "")
    if made:
        conn = db()
        conn.execute("DELETE FROM bookings WHERE id = ?", (made["id"],))
        conn.commit()
        conn.close()

    s.section("But a number it cannot read never costs a booking")
    # The other half, and the one that would be easy to get wrong in the name
    # of tidiness.
    anon.post(f"/book/{room['id']}", data={
        "guest_name": TAG + " Bernard", "guest_email": "bernard@example.invalid",
        "guest_phone": "ask my wife", "party_size": "2",
        "arrival_date": (arrival + timedelta(days=10)).isoformat(),
        "departure_date": (arrival + timedelta(days=12)).isoformat(),
        "agree_terms": "on",
    }, follow_redirects=True)
    conn = db()
    odd = conn.execute(
        "SELECT * FROM bookings WHERE guest_email = 'bernard@example.invalid' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    s.check("the booking still goes through", odd is not None,
            detail="a stay is worth more than a tidy column")
    s.check("with what they typed kept rather than thrown away",
            odd and odd["guest_phone"] == "ask my wife",
            detail=str(odd["guest_phone"]) if odd else "")
    s.check("and nothing downstream can mistake it for sendable",
            odd and m.normalise_phone(odd["guest_phone"]) is None,
            detail="this is what stops it being tried and billed for")
    if odd:
        conn = db()
        conn.execute("DELETE FROM bookings WHERE id = ?", (odd["id"],))
        conn.commit()
        conn.close()

    s.section("The waitlist too")
    conn = db()
    was_enabled = conn.execute(
        "SELECT enabled FROM restaurant_settings LIMIT 1").fetchone()["enabled"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1")
    conn.execute("DELETE FROM submission_log WHERE action = 'join_restaurant_waitlist'")
    conn.commit()
    conn.close()
    anon.post("/restaurant/waitlist/join", data={
        "name": TAG + " Claire", "email": "claire@example.invalid",
        "phone": "+33 (0)6 98 76 54 32",
    }, follow_redirects=True)
    conn = db()
    entry = conn.execute("SELECT * FROM restaurant_waitlist WHERE name = ?",
                         (TAG + " Claire",)).fetchone()
    conn.execute("UPDATE restaurant_settings SET enabled = ?", (was_enabled,))
    conn.commit()
    conn.close()
    s.check("a waitlist request keeps a reachable number",
            entry and entry["phone"] == "+33698765432",
            detail=str(entry["phone"]) if entry else "no row")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
