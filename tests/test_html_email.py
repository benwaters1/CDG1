"""The confirmation, drawn — and the plain text still underneath it.

Every message the house sent was plain text: send_email_via_resend posted
only "text". So the booking page arrived as a bare URL a guest had to copy
out, there was nowhere to put directions anybody could tap, and the most
opened letter the house sends ended after the reference number.

The HTML is an ADDITION, never a replacement, and three things keep that true.

  THE TEXT IS STILL AUTHORITATIVE. It is what a client set to plain text
  shows, what search indexes, and what this house keeps as its own record of
  what it said — booking_correspondence reads that record, and a page of
  <table role="presentation"> is not something anybody can answer a telephone
  from. So the text is sent first and stored; the HTML is sent alongside it.

  AND IT GOES OUT EVEN WHEN THE DRAWING FAILS. A template that will not
  render is a reason to send a plainer letter. It is not a reason for a guest
  to hear nothing about a room the house has just taken off the market.

  AND THE MAP PIN IS NOT INVENTED. The email uses coordinates rather than the
  address precisely because a search for a building with no street number
  drops people in the village square — so a pin that is merely NEARBY fails at
  the exact job it was added for, on an unlit road, at night, with more
  authority than no pin at all. The template arrived with 42.7847 / 1.6564
  hard-coded and a note saying to check them against the actual gates. Nobody
  has. Until somebody does, the letter says to telephone.

  IT IS WRITTEN FOR MAIL CLIENTS, which is a different discipline from the
  site: tables rather than flexbox, every style inline, no web fonts. Outlook
  draws with Word's engine and Gmail strips <style> blocks, and neither of
  those failures shows up anywhere except in somebody's inbox.
"""
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZHE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN ('house_lat', 'house_lng')")
    conn.commit()
    conn.close()


def _coords(lat, lng):
    conn = db()
    for key, value in (("house_lat", lat), ("house_lng", lng)):
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        if value is not None:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                         (key, value))
    conn.commit()
    conn.close()


def _render(booking, room_name):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.booking_confirmation_html(conn, booking, room_name)
    finally:
        conn.close()


def run():
    s = Suite("The confirmation, drawn")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    arrival = m.house_today() + timedelta(days=300)
    with m.app.test_request_context("/"):
        for _try in range(60):
            if m.is_range_available(conn, room["id"], arrival,
                                    arrival + timedelta(days=3)):
                break
            arrival += timedelta(days=4)
    with m.app.test_request_context("/"):
        ref, token = m.create_booking(
            conn, room, TAG + " Guest", "zzhe@example.invalid", "",
            arrival, arrival + timedelta(days=3), 2, "", [],
            payment_status="unpaid")
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (ref,)).fetchone()
    conn.close()

    sent = []
    was_email, was_resend = m.send_email, m.send_email_via_resend
    try:
        s.section("It is written for mail clients, not for a browser")
        html = _render(booking, room["name"])
        s.check("it renders", len(html) > 2000, detail=str(len(html)))
        s.check("as a whole document",
                html.lstrip().lower().startswith("<!doctype"))
        # Outlook draws with Word's engine, which has neither. This is the one
        # rule that cannot be seen from here and only shows up in an inbox.
        s.check("with no flexbox and no grid",
                "display:flex" not in html.replace(" ", "")
                and "display:grid" not in html.replace(" ", ""),
                detail="Outlook uses Word's rendering engine and supports "
                       "neither")
        s.check("and no stylesheet to be stripped",
                "<style" not in html.lower() and "stylesheet" not in html.lower(),
                detail="Gmail drops <style> blocks on some clients, so every "
                       "rule has to be inline")
        s.check("and no web font to fail to load",
                "fonts.googleapis" not in html and "@font-face" not in html)
        s.check("it says which booking it is about", ref in html)
        s.check("and carries the page that changes it",
                token in html,
                detail="the manage link is the one thing every one of these "
                       "letters exists to deliver")

        s.section("What it does not assert")
        # The fixture it arrived with passed four nights over three dates and
        # printed "4 nights" without complaint. A letter that contradicts its
        # own dates is worse than one that says less.
        s.check("no count of nights over the top of the dates",
                "night" not in html.lower().split("finding us")[0]
                or "3 night" in html.lower() or "three night" in html.lower(),
                detail="the dates are unambiguous; a number printed over them "
                       "can only ever disagree with them")

        s.section("The pin nobody has checked")
        _coords(None, None)
        blank = _render(booking, room["name"])
        s.check("with no coordinates set there is no map button",
                "maps.apple.com" not in blank and "google.com/maps" not in blank,
                detail="a pin that is merely nearby fails at the exact job it "
                       "was added for")
        s.check("and it does not say 'use the pin below' over nothing",
                "the pin below" not in blank,
                detail="the sentence and the buttons stand or fall together")
        s.check("it tells them to telephone instead",
                "talk you up the last few miles" in blank)
        s.check("and still says not to search the address",
                "Do not search the address" in blank,
                detail="that is the actual advice, and it is true either way")

        _coords("42.8", "1.7")
        pinned = _render(booking, room["name"])
        s.check("once somebody sets them, both maps appear",
                "maps.apple.com" in pinned and "google.com/maps" in pinned)
        s.check("with the coordinates in the link, not the address",
                "42.8,1.7" in pinned,
                detail="an address search for a building with no street "
                       "number is the whole problem")

        # A transposed pair puts the pin in the sea, and the letter would say
        # "use the pin" about it. Numbers that cannot be coordinates are the
        # same as none.
        _coords("428", "1.7")
        silly = _render(booking, room["name"])
        s.check("a latitude that is not a latitude is treated as unset",
                "maps.apple.com" not in silly,
                detail="the letter would otherwise say 'use the pin below' "
                       "about a point in the sea")
        _coords(None, None)

        s.section("The text is still what the house said")
        m.send_email = lambda to, subj, body, **k: (
            sent.append((to, subj, body, k.get("html") or "")), True)[1]
        conn = db()
        with m.app.test_request_context("/"):
            del sent[:]
            outcome = m.confirm_booking_by_id(conn, booking["id"])
            # Moved rather than picked. A date counted forward from today
            # lands on whatever the seed data holds, and is_range_available
            # does not know about workshop holds -- confirm itself is the only
            # thing that does, so it is the oracle.
            for _try in range(40):
                if outcome[0]:
                    break
                start = m.parse_date(
                    conn.execute("SELECT arrival_date FROM bookings WHERE id = ?",
                                 (booking["id"],)).fetchone()["arrival_date"])
                start += timedelta(days=4)
                conn.execute(
                    """UPDATE bookings SET arrival_date = ?, departure_date = ?,
                           status = 'pending' WHERE id = ?""",
                    (start.isoformat(), (start + timedelta(days=3)).isoformat(),
                     booking["id"]))
                conn.commit()
                del sent[:]
                outcome = m.confirm_booking_by_id(conn, booking["id"])
        conn.close()
        s.check("the confirmation went", len(sent) == 1,
                detail="confirm_booking_by_id said %r" % (outcome,))
        to, subject, body, drawn = sent[0] if sent else ("", "", "", "")
        s.check("with a plain-text body that reads on its own",
                "Reference code:" in body and "<table" not in body,
                detail=repr(body[:80]))
        s.check("and the drawn version alongside it",
                drawn.lstrip().lower().startswith("<!doctype"),
                detail="html is an addition to the text, never a replacement")
        s.check("both about the same booking",
                ref in body and ref in drawn)

        s.section("And it still goes when the drawing fails")
        # A template that will not render is a reason to send a plainer
        # letter, not a reason for the guest to hear nothing at all.
        conn = db()
        try:
            broken = m.booking_confirmation_html(conn, booking, room["name"])
        finally:
            conn.close()
        s.check("outside a request, where url_for cannot work, it returns empty",
                broken == "",
                detail="raising here would take the whole confirmation down "
                       "with it: %r" % broken[:60])
        m.send_email = lambda to, subj, body, **k: (
            sent.append((to, subj, body, k.get("html") or "")), True)[1]
        was_html = m.booking_confirmation_html
        m.booking_confirmation_html = lambda *a, **k: ""
        conn = db()
        conn.execute("UPDATE bookings SET status = 'pending' WHERE id = ?",
                     (booking["id"],))
        conn.commit()
        with m.app.test_request_context("/"):
            del sent[:]
            m.confirm_booking_by_id(conn, booking["id"])
        conn.close()
        m.booking_confirmation_html = was_html
        s.check("the letter goes anyway", len(sent) == 1, detail=str(len(sent)))
        s.check("as text, with everything that matters in it",
                sent and "Reference code:" in sent[0][2] and sent[0][3] == "",
                detail=repr(sent[0][2][:60]) if sent else "")

        s.section("Resend is given both parts")
        posted = {}
        m.send_email_via_resend = lambda to, subj, body, ics=None, name=None, html=None: (
            posted.update({"text": body, "html": html}), True)[1]
        with m.app.test_request_context("/"):
            m.send_email_via_resend("zzhe@example.invalid", "s", "the plain one",
                                    html="<p>the drawn one</p>")
        s.check("both reach the provider",
                posted.get("text") == "the plain one"
                and posted.get("html") == "<p>the drawn one</p>",
                detail=str(posted))
        posted.clear()
        was_enabled = m.resend_enabled
        m.resend_enabled = lambda: True
        m.send_email_via_resend = lambda to, subj, body, ics=None, name=None, html=None: (
            posted.update({"text": body, "html": html}), True)[1]
        try:
            with m.app.test_request_context("/"):
                was_email(  # the REAL send_email, not the mock above it
                    "zzhe@example.invalid", "s", "the plain one",
                    html="<p>the drawn one</p>", keep=False)
        finally:
            m.resend_enabled = was_enabled
        s.check("and send_email itself carries it the whole way down",
                posted.get("html") == "<p>the drawn one</p>",
                detail="dropping html= from the one call between them is "
                       "invisible: every letter still goes, still says the "
                       "right thing, and is plain text for ever. Got %r"
                       % (posted.get("html"),))

        real = open("app.py", encoding="utf-8").read().split(
            "def send_email_via_resend")[1].split(chr(10) + "def ")[0]
        s.check("and the real one puts html in the payload only when there is some",
                'payload["html"] = html' in real and "if html:" in real,
                detail="sending html='' would make every client show an empty "
                       "message instead of the text")
    finally:
        m.send_email, m.send_email_via_resend = was_email, was_resend
        _cleanup()
    return s
