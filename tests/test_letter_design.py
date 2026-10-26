"""Every letter drawn as the house's own, and the three faults found doing it.

The owner, 19 October 2026, on a statement that reached a guest: "shouldn't
it have a link to payment or something rather than statement ... the design
isn't as good as it could be either, where's the logos, a proper layout ...
go over all the emails and do them properly".

What this holds:

  THE PAPER. Every letter carries the house's own logo (from static/email,
  never the Squarespace account the site no longer publishes from), a foot
  with the telephone and an address to write to -- it printed a lone "·"
  where both should have been -- and a heading that does not repeat the
  house's name the logo has just said.

  WHAT EACH PARAGRAPH IS. The greeting, a list of facts, a column of sums,
  the line about paying safely and the signature are told apart and drawn as
  themselves. "Thank you: we have received..." is a sentence, not a fact.

  THE THREE FAULTS.
    - "Book it and send a payment link" sent the confirmation AND the
      statement, a second letter whose words sent the guest to a statement
      page with no way to pay on it. Now one letter, the confirmation, with
      the amount owed and the button that pays it; and the statement page
      has the button too.
    - The drawn confirmation told a guest who owed money it was "due on
      arrival. We take cards and cash" -- against the rule that a stay is
      paid online before it starts, in the letter whose last paragraph says
      the house only ever takes payment by card on its own site.
    - A discount was listed among the charges and taken off again under
      "Discounts and reductions", so the rows above the subtotal added up to
      thirty euros less than it said.

  EVERY LETTER TO A GUEST IS DRAWN, including the ones written in code and
  the campaigns, at the one door they all go through. The house's notices to
  itself stay plain.
"""
import os
import re
from datetime import timedelta

from markupsafe import escape as m_escape

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zzdesign"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run():
    s = Suite("Letters drawn as the house's own")
    conn = db()
    _cleanup(conn)
    sent = []
    was_send = m.send_email

    def capture(to, subject, body, *a, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "html": kw.get("html")})
        return True

    try:
        _paper(s)
        _paragraphs(s)
        m.send_email = capture
        _desk_booking(s, conn, sent)
    finally:
        m.send_email = was_send
        _cleanup(conn)
        conn.close()
    _one_door(s)
    return s


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _paper(s):
    s.section("The paper every letter is written on")
    with m.app.test_request_context(base_url="https://www.chateaugudanes.com"):
        drawn = {}
        for key, _label, subject, body in m.DEFAULT_EMAIL_TEMPLATES:
            drawn[key] = m.preview_email_template(key, subject, body)["html"]
    empty = [k for k, h in drawn.items() if not h]
    s.check("every letter the house sends from a template is drawn", not empty, detail=empty)
    no_logo = [k for k, h in drawn.items() if h and "/static/email/logo-white.png" not in h]
    s.check("every one carries the house's own logo", not no_logo, detail=no_logo)
    hotlinked = [k for k, h in drawn.items() if h and "squarespace" in h.lower()]
    s.check("and no picture from the Squarespace account", not hotlinked, detail=hotlinked)
    no_foot = [k for k, h in drawn.items() if h and not (
        m.HOUSE_TELEPHONE in h and re.search(r'mailto:[a-z]+@chateaugudanes\.com', h))]
    s.check("every foot gives the telephone and an address to write to -- "
            "not the lone '·' it printed", not no_foot, detail=no_foot)
    money = [k for k in m.MONEY_LETTERS if k in drawn and drawn[k]
             and "mailto:accounts@chateaugudanes.com" not in drawn[k]]
    s.check("a letter about money gives accounts@ to write to", not money, detail=money)
    # "Your stay at Château de Gudanes" is a heading; "Confirm your
    # subscription — Château de Gudanes" is a heading with the house's name
    # tacked on, under a logo that has just said it.
    named = [k for k, h in drawn.items() if h and re.search(
        r"<h1[^>]*>[^<]*—\s*Ch[âa]teau de Gudanes\s*</h1>", h)]
    s.check("no heading ends with ' — Château de Gudanes' under the logo", not named,
            detail=named)
    s.check("a subject splits into the heading and what it is about",
            m.letter_heading("Booking confirmed — The King Room") == ("Booking confirmed", "The King Room")
            and m.letter_heading("Thank you for subscribing — Château de Gudanes")
            == ("Thank you for subscribing", ""))
    pictures = {"logo-white.png", "crest-navy.png", *m.LETTER_HERO.values(), "hero-house.jpg"}
    missing = [p for p in pictures if not os.path.exists(os.path.join(ROOT, "static", "email", p))]
    s.check("every picture a letter names is in static/email", not missing, detail=missing)
    big = [k for k, h in drawn.items() if h and len(h.encode("utf-8")) > 90_000]
    s.check("none is near the size at which Gmail cuts a letter off", not big, detail=big)


def _paragraphs(s):
    s.section("Each paragraph drawn as what it is")
    text = ("Hi Robert Revelli,\n\n"
            "Thank you: we have received €100.00 towards Noël at Gudanes.\n\n"
            "Arrival: 29 October 2026\nDeparture: 1 November 2026\nReference code: GUD-1AG30U\n\n"
            "Three nights at €270 €810.00\nTaxe de séjour €4.80\nTotal €814.80\n\n"
            + m.PAYMENT_SAFETY_LINE + "\n\n— Château de Gudanes")
    blocks = m.dress_blocks(m.letter_blocks(text))
    kinds = [b["kind"] for b in blocks]
    s.check("greeting, sentence, facts, sums, notice and signature, in order",
            kinds == ["greeting", "text", "facts", "money", "notice", "signoff"], detail=kinds)
    facts = next((b for b in blocks if b["kind"] == "facts"), {"rows": []})
    s.check("the facts are label and value", ("Reference code", "GUD-1AG30U") in facts["rows"],
            detail=facts["rows"])
    sums = next((b for b in blocks if b["kind"] == "money"), {"rows": []})
    s.check("the sums have their own column, the total marked",
            [r["amount"] for r in sums["rows"]] == ["€810.00", "€4.80", "€814.80"]
            and sums["rows"][-1]["kind"] == "total", detail=sums["rows"])
    receipt = m.dress_blocks(m.letter_blocks(
        "Hi Marie,\n\nThank you.\n\nChâteau de Gudanes SASU\n— Château de Gudanes"))
    s.check("a signature on the last line of a paragraph is still the signature",
            receipt[-1]["kind"] == "signoff" and receipt[-1]["text"] == "Château de Gudanes",
            detail=[b["kind"] for b in receipt])
    reserved = m.letter_blocks("To secure it, add your details and pay here — it takes a "
                               "couple of minutes:\nhttps://example.invalid/booking/abc/finish")
    s.check("a button the sentence cannot name is named by where it goes",
            [b.get("label") for b in reserved if b["kind"] == "button"] == ["Finish and pay"],
            detail=reserved)
    with m.app.test_request_context(base_url="https://www.chateaugudanes.com"):
        drawn = m.letter_html("A test", "Hello <script>,\n\nA line.\n\n— Château de Gudanes")
    s.check("a guest's name is drawn as the characters it is",
            "<script>" not in drawn and "&lt;script&gt;" in drawn)


def _desk_booking(s, conn, sent):
    s.section("Book it and send a payment link")
    owner = clients()[0]
    conn.execute("""INSERT INTO rooms (name, export_token, active, max_occupancy, max_adults,
                    max_children, price_per_night, min_nights, sort_order)
                    VALUES (?, 'tok-zzdesign', 1, 2, 2, 0, 270, 1, 96)""", (f"{TAG} King",))
    conn.commit()
    room = conn.execute("SELECT * FROM rooms WHERE name = ?", (f"{TAG} King",)).fetchone()
    a = m.house_today() + timedelta(days=200)
    while not m.is_range_available(conn, room["id"], a, a + timedelta(days=3))[0]:
        a += timedelta(days=4)
    b = a + timedelta(days=3)
    rack = m.compute_room_total(conn, room, a, b)
    guest = f"{TAG}.robert@example.invalid"
    before = len(sent)
    owner.post("/admin/bookings/walk-in", data={
        "mode": "paylink", "room_id": str(room["id"]), "arrival_date": a.isoformat(),
        "departure_date": b.isoformat(), "guest_name": f"{TAG} Robert", "guest_email": guest,
        "adults": "2", "guests_under_18": "0", "party_size": "2",
        # Thirty below the rate card: written as a discount.
        "total_price": f"{rack - 30:.2f}"})
    stay = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                        "JOIN rooms ON rooms.id = bookings.room_id WHERE guest_name = ?",
                        (f"{TAG} Robert",)).fetchone()
    s.check("the stay is booked", stay is not None and stay["status"] == "confirmed")
    if not stay:
        return
    to_guest = [x for x in sent[before:] if x["to"] == guest]
    s.check("exactly one letter goes to the guest", len(to_guest) == 1,
            detail=[x["subject"] for x in to_guest])
    s.check("and it is not a statement", not [x for x in to_guest if "statement" in x["subject"].lower()],
            detail=[x["subject"] for x in to_guest])
    letter = to_guest[0] if to_guest else {"html": "", "body": ""}
    pay = f"/book/pay/{stay['manage_token']}"
    s.check("the confirmation carries the button that pays what is owed",
            pay in (letter["html"] or "") and "Still to pay" in (letter["html"] or ""),
            detail=(letter["html"] or "")[:200])
    s.check("and never asks for it on arrival, in cash",
            "on arrival" not in (letter["html"] or "").lower()
            and "cards and cash" not in (letter["html"] or "").lower())

    s.section("The statement adds up, and can be paid from")
    view = m.booking_statement_view(conn, "room", stay["id"])
    added = round(sum(x["charge"] for x in view["added"]), 2)
    off = round(sum(x["charge"] for x in view["taken_off"]), 2)
    s.check("the rows above the subtotal add up to it", added == view["subtotal"],
            detail=f"{added} vs {view['subtotal']}")
    s.check("the subtotal less each reduction is the total",
            round(view["subtotal"] + off, 2) == view["total"] and off < 0,
            detail=f"{view['subtotal']} {off} {view['total']}")
    page = m.app.test_client().get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True)
    # Counted by what the line says, not by how its sum is printed: listed
    # among the charges it printed as "€-30.00", which a count of "−€30.00"
    # does not see.
    what = str(m_escape(view["taken_off"][0]["what"])) if view["taken_off"] else "(none)"
    s.check("the discount is on the page once, not twice",
            page.count(what) == 1 and "€-" not in page and "Discounts and reductions" not in page,
            detail=f"{what!r} x{page.count(what)}")
    s.check("the statement page has the button its letter promised",
            f'href="{pay}"' in page or pay in page)
    sent_before = len(sent)
    with m.app.test_request_context(base_url="https://www.chateaugudanes.com"):
        m.send_room_statement(conn, stay)
    statement = [x for x in sent[sent_before:] if x["to"] == guest]
    html = statement[0]["html"] if statement else ""
    s.check("the statement letter draws the statement as a table, with Pay now",
            "Balance due" in (html or "") and pay in (html or "") and "<table" in (html or ""))
    s.check("and lists the discount once there too", (html or "").count("−€30.00") == 1,
            detail=(html or "").count("−€30.00"))


def _one_door(s):
    s.section("Every letter to a guest is drawn")
    with m.app.test_request_context(base_url="https://www.chateaugudanes.com"):
        out = m.letter_html("Your code", "Hello,\n\nYour code is 123456.\n\n— Château de Gudanes")
    s.check("a letter written in code draws in the same shell",
            "/static/email/logo-white.png" in out)
    s.check("send_email draws a letter that arrives without one, for a guest",
            _drawn_at_the_door(f"{TAG}.guest@example.invalid") is True)
    s.check("and leaves the house's notices to itself plain",
            _drawn_at_the_door("reservations@chateaugudanes.com") is False)
    # A job has no request: the balance reminders, the feedback requests. They
    # went out bare while the same letter sent from a page went drawn.
    out = m.letter_html("Before you arrive — The King Room",
                        "Hi Robert,\n\nWe are looking forward to seeing you.\n\n— Château de Gudanes")
    s.check("a letter written by a job, outside any request, is drawn too",
            "/static/email/logo-white.png" in (out or ""))


def _drawn_at_the_door(address):
    """Whether send_email hands its transport a drawn letter for this address.

    The provider is stood in for, never called: the transport is replaced and
    resend_enabled() answers yes for the length of one letter."""
    seen = {}
    was_transport, was_enabled = m.send_email_via_resend, m.resend_enabled

    def transport(to_address, subject, body, ics_content=None, ics_filename=None,
                  html=None, **kw):
        seen["html"] = html
        return True, None

    m.send_email_via_resend = transport
    m.resend_enabled = lambda: True
    try:
        with m.app.test_request_context(base_url="https://www.chateaugudanes.com"):
            m.send_email(address, f"{TAG} a plain letter",
                         "Hello,\n\nA line.\n\n— Château de Gudanes",
                         despite_switch="a test of the drawing", keep=False)
    finally:
        m.send_email_via_resend = was_transport
        m.resend_enabled = was_enabled
        conn = db()
        for table in ("mail_log", "guest_messages"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE subject LIKE ?", (TAG + "%",))
            except Exception:
                pass
        conn.commit()
        conn.close()
    if "html" not in seen:
        return None
    return bool(seen["html"] and "/static/email/logo-white.png" in seen["html"])


if __name__ == "__main__":
    print(run().report())
