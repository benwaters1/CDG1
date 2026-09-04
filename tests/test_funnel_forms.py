"""The other three ways a stranger commits to something, filled in as rendered.

test_booking_journey does this for a room. The dinner table, a workshop place
and an event enquiry are the same shape and carry the same exposure: every
suite that covers them posts the field names the ROUTE expects, straight to the
route, so a template that renames or drops a field leaves all of them green
while the form is broken for everyone who opens it.

    anon.post("/events/inquire", data={"contact_name": ..., "event_type": ...})

So these fill in what the page renders and post to the action the page names,
and never type a field name of their own except as an ANSWER to a field that
is there. If the template and the route disagree, the submission fails the way
it would for a guest and this goes red.

Two things about the arrangement.

Each funnel gets its own remote address. The public forms are rate limited per
connection and every test client in this suite is 127.0.0.1, so a funnel test
that ran late in a full pass would be turned away at the door by the suites
before it -- passing alone and failing in company, which is the worst kind of
test to leave lying about.

The dinner service is switched OFF in the house's settings, so /restaurant/book
is a 404 today. That is a real configuration, not a fault, and the page is
worth testing anyway: it will be switched on one day and nobody will re-read
the form first. So it is enabled in the throwaway copy for the length of the
check and put back afterwards.
"""
from _harness import Suite, db, fill, flashes, forms_on

from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZFUNNEL"


def _answers(when, later):
    """One bank of answers, keyed by the names these forms actually use."""
    return {
        # who
        "guest_name": TAG + " Guest", "contact_name": TAG + " Guest",
        "guest_email": "zzfunnel@example.invalid",
        "contact_email": "zzfunnel@example.invalid",
        "email": "zzfunnel@example.invalid",
        "guest_phone": "+33 6 00 00 00 00", "contact_phone": "+33 6 00 00 00 00",
        # when
        "dinner_date": when, "preferred_date": when, "alternate_date": later,
        "arrival": when, "departure": later,
        # how many
        "party_size": "2", "guest_count": "20",
        # the rest
        "event_type": "wedding", "message": TAG + " enquiry",
        "notes": "", "dietary_notes": "", "medical_notes": "",
        "other_guest_names": "", "interests": "", "promo_code": "",
        "agree_terms": "on",
    }


def _clean(conn):
    for sql in (
        "DELETE FROM restaurant_bookings WHERE guest_email = 'zzfunnel@example.invalid'",
        "DELETE FROM workshop_bookings WHERE guest_email = 'zzfunnel@example.invalid'",
        "DELETE FROM event_inquiries WHERE contact_email = 'zzfunnel@example.invalid'",
        "DELETE FROM guests WHERE email = 'zzfunnel@example.invalid'",
        "DELETE FROM submission_log WHERE ip_address LIKE '203.0.113.%'",
    ):
        try:
            conn.execute(sql)
        except Exception:                              # noqa: BLE001 - table may not exist
            pass
    conn.commit()


def _client(ip):
    c = m.app.test_client()
    c.environ_base["REMOTE_ADDR"] = ip
    return c


def _submit(s, name, page_url, pick, answers, count_sql, conn):
    """Open a page, fill the form it renders, post where it says, count rows."""
    guest = _client("203.0.113.%d" % (abs(hash(name)) % 200 + 20))
    page = guest.get(page_url)
    if page.status_code != 200:
        s.check("%s: the page opens" % name, False,
                detail="HTTP %s at %s" % (page.status_code, page_url))
        return None
    html = page.get_data(as_text=True)
    candidates = [f for f in forms_on(html) if f["method"] == "post" and pick(f)]
    s.check("%s: the page carries its form" % name, bool(candidates),
            detail="%d post form(s) on the page, none of them the right one"
                   % len([f for f in forms_on(html) if f["method"] == "post"]))
    if not candidates:
        return None
    form = candidates[0]

    action = form["action"] or page_url
    s.check("%s: its action is a real route" % name,
            m.app.url_map.bind("localhost").test(action.split("?")[0], "POST"),
            detail=action)

    data = fill(form, answers)
    unanswered = sorted(f["name"] for f in form["fields"]
                        if f["required"] and f["type"] not in ("checkbox", "radio")
                        and not data.get(f["name"]))
    s.check("%s: every required field has something to put in it" % name,
            not unanswered, detail=str(unanswered))

    before = conn.execute(count_sql).fetchone()[0]
    sent = guest.post(action, data=data, follow_redirects=True)
    s.check("%s: the form submits" % name, sent.status_code == 200, sent)
    after = conn.execute(count_sql).fetchone()[0]
    s.check("%s: and it was recorded" % name, after == before + 1,
            detail="; ".join(flashes(sent)[:2]) or "nothing written, nothing said")
    return sent


def run():
    s = Suite("the other funnels")
    conn = db()
    _clean(conn)

    when = (m.house_today() + timedelta(days=190)).isoformat()
    later = (m.house_today() + timedelta(days=197)).isoformat()
    answers = _answers(when, later)

    # ----------------------------------------------------------- the table
    s.section("A table for dinner")

    settings = conn.execute("SELECT * FROM restaurant_settings LIMIT 1").fetchone()
    was_enabled = settings["enabled"] if settings else None
    s.check("the dinner service is a setting, not a hard-coded fact",
            settings is not None,
            detail="no restaurant_settings row; the 404 is unexplained")
    if settings is not None:
        # Switched on for the length of this check. It is off in the house's
        # own settings today, which is why /restaurant/book is a 404 -- a real
        # configuration rather than a fault, and no reason to leave the form
        # unchecked until the morning somebody switches it on.
        conn.execute("UPDATE restaurant_settings SET enabled = 1 WHERE id = ?",
                     (settings["id"],))
        conn.commit()
        try:
            _submit(s, "dinner", "/restaurant/book",
                    lambda f: any(x["name"] == "dinner_date" for x in f["fields"]),
                    answers,
                    "SELECT COUNT(*) FROM restaurant_bookings", conn)
        finally:
            conn.execute("UPDATE restaurant_settings SET enabled = ? WHERE id = ?",
                         (was_enabled, settings["id"]))
            conn.commit()
        back = conn.execute("SELECT enabled FROM restaurant_settings WHERE id = ?",
                            (settings["id"],)).fetchone()["enabled"]
        s.check("and the setting is put back exactly as it was",
                back == was_enabled, detail="%s -> %s" % (was_enabled, back))

    # ------------------------------------------------------- a workshop place
    s.section("A place on a workshop")

    session = conn.execute(
        """SELECT s.id FROM workshop_sessions s
            WHERE COALESCE(s.capacity, 0) > 0
            ORDER BY s.start_date DESC LIMIT 1""").fetchone()
    s.check("there is a session with places on it to register for",
            session is not None,
            detail="nothing to book; the form cannot be exercised")
    if session is not None:
        _submit(s, "workshop", "/workshops/register/%d" % session["id"],
                lambda f: any(x["name"] == "guest_email" for x in f["fields"]),
                answers,
                "SELECT COUNT(*) FROM workshop_bookings", conn)

    # ---------------------------------------------------------- an enquiry
    s.section("An enquiry about an event")

    _submit(s, "event", "/events",
            lambda f: any(x["name"] == "event_type" for x in f["fields"]),
            answers,
            "SELECT COUNT(*) FROM event_inquiries", conn)

    # ------------------------------------------------------------- the shape
    s.section("What each of them stored is what was typed")

    dinner = conn.execute(
        "SELECT * FROM restaurant_bookings WHERE guest_email = ? ORDER BY id DESC LIMIT 1",
        ("zzfunnel@example.invalid",)).fetchone()
    if dinner is not None:
        s.check("dinner: the date is the one asked for",
                str(dinner["dinner_date"])[:10] == when, detail=str(dinner["dinner_date"]))
        s.check("dinner: and the name went in",
                (dinner["guest_name"] or "").startswith(TAG))

    enquiry = conn.execute(
        "SELECT * FROM event_inquiries WHERE contact_email = ? ORDER BY id DESC LIMIT 1",
        ("zzfunnel@example.invalid",)).fetchone()
    if enquiry is not None:
        # A date the app cannot read must not land in a date column: the whole
        # point of a preferred date is that something can later search on it.
        s.check("event: the preferred date was parsed, not stored as typed",
                not enquiry["preferred_date"]
                or m.parse_date(str(enquiry["preferred_date"])) is not None,
                detail=str(enquiry["preferred_date"]))
        s.check("event: the enquiry knows who it is from",
                (enquiry["contact_name"] or "").startswith(TAG))

    _clean(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
