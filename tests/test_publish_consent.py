"""Nothing is published without asking you first.

That sentence is on the feedback form a guest fills in after a stay, and on
the atelier one. It is a promise the software made and did not keep: the
featured flag was a tick on an admin page, and ticking it put the guest's
first name, their words, their rating and their room on the public booking
page. Nobody was asked, nothing recorded that anybody had been, and there was
no way to record it if they had.

WHERE THE RULE LIVES IS THE POINT. It is in the two SELECTs that build the
public pages, not in a check on the admin page. A page can be got round, and
the next person to add a review carousel writes the query again from scratch;
wiring consent into the query is what makes the promise hold when somebody
ticks the wrong row. So the checks below feature a review WITHOUT consent and
then load the actual public page.

NULL IS NOT NO. Never-asked, said-no and said-yes are three states, and the
middle of them is the whole reason this exists — a review nobody has asked
about is a letter somebody still has to write, not a refusal. The admin page
has to say which it is.

The tick on the form is unticked. A pre-ticked box is not consent, and a form
that promises to ask has to actually ask.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZCONSENT"


def _cleanup(conn):
    conn.execute("DELETE FROM guest_feedback WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_feedback WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM guest_feedback WHERE booking_id IN "
        "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("permission to publish")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    def add(name, consent, *, featured=1):
        conn.execute(
            """INSERT INTO guest_feedback (booking_id, guest_name, rating,
                       comment, featured, publish_consent, publish_consent_at,
                       publish_consent_how, submitted_at)
               VALUES (NULL, ?, 5, ?, ?, ?, ?, ?, ?)""",
            (TAG + " " + name, f"{TAG} words from {name}", featured, consent,
             now if consent is not None else None,
             "form" if consent == 1 else None, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    said_yes = add("Agnes", 1)
    said_no = add("Bertrand", 0)
    never_asked = add("Colette", None)
    not_featured = add("Denis", 1, featured=0)
    conn.commit()

    s.section("The public page shows only what the house may show")
    public = oc.get("/book").get_data(as_text=True)
    if "What Guests Say" not in public:
        s.check("the booking page has a reviews section to check",
                False,
                detail="without it these checks prove nothing, so this is "
                       "reported rather than skipped quietly")
    s.check("a review they said yes to appears",
            f"{TAG} words from Agnes" in public)
    s.check("one they said NO to does not",
            f"{TAG} words from Bertrand" not in public,
            detail="featured is ticked on it; the tick is not the rule")
    s.check("and one NOBODY HAS ASKED does not either",
            f"{TAG} words from Colette" not in public,
            detail="never asked is not a yes, and the form they filled in "
                   "says nothing is published without asking first")
    s.check("something with consent but not featured stays off",
            f"{TAG} words from Denis" not in public,
            detail="consent is permission, not a decision to publish")

    s.section("The rule is in the query, not on a page")
    # The check that would survive somebody rewriting the admin page, or
    # adding a second carousel: the SQL that builds the public page carries
    # the condition itself.
    import inspect
    for fn_name, what in (("book_rooms", "the booking page"),
                          ("workshops_public", "the workshops page")):
        fn = m.app.view_functions.get(fn_name)
        if not fn:
            s.check(f"{fn_name} exists to check", False)
            continue
        src = inspect.getsource(fn)
        s.check(f"{what} asks for consent in its own query",
                "publish_consent = 1" in src,
                detail="a check on the admin page can be got round; this "
                       "cannot")

    s.section("Never asked reads differently from refused")
    states = m.publishable_reviews(conn)
    names = {k: {e["row"]["guest_name"] for e in v} for k, v in states.items()}
    s.check("Agnes is ready", TAG + " Agnes" in names["ready"])
    s.check("Bertrand is a refusal", TAG + " Bertrand" in names["refused"])
    s.check("Colette is unasked", TAG + " Colette" in names["unasked"])
    s.check("and unasked is not lumped in with refused",
            TAG + " Colette" not in names["refused"],
            detail="one of those is a letter somebody still has to write and "
                   "the other is an answer")
    s.check("nothing unfeatured is in any of the three",
            TAG + " Denis" not in (names["ready"] | names["refused"]
                                   | names["unasked"]),
            detail="this list is about what the house intends to publish")

    s.section("The admin page says which it is")
    body = oc.get("/admin/feedback").get_data(as_text=True)
    s.check("it opens", "ZZCONSENT" in body or TAG in body)
    s.check("a featured review nobody asked about says so",
            "Nobody has asked them yet" in body)
    s.check("and a refusal says the tick does not matter",
            "whatever the tick above says" in body)
    s.check("and one they agreed to says how it was obtained",
            "They said we may use it" in body)

    s.section("The page counts the letters still to write")
    # publishable_reviews existed and nothing called it, which the
    # unreachable-code sweep caught. Refusing to publish is the easy half of
    # consent; REMEMBERING TO ASK is the half that needs a number on a page,
    # or the letter never gets written and the review never goes up.
    s.check("it says how many are waiting to be asked",
            "nobody has asked the guest about" in body,
            detail="a review chosen for the website and never asked about "
                   "sits there doing nothing, and nothing tells anybody")
    s.check("and the list can be filtered down to them",
            "Waiting to be asked" in body,
            detail="somebody with twenty minutes to write letters needs the "
                   "list, not the count")

    s.section("Recording that somebody asked, afterwards")
    r = oc.post(f"/admin/feedback/stay/{never_asked}/permission",
                data={"answer": "yes", "how": "spoken"}, follow_redirects=True)
    row = conn.execute(
        "SELECT publish_consent, publish_consent_at, publish_consent_how "
        "FROM guest_feedback WHERE id = ?", (never_asked,)).fetchone()
    s.check("it is recorded", row["publish_consent"] == 1)
    s.check("with when", row["publish_consent_at"] is not None)
    s.check("and with WHO asked, not just how",
            row["publish_consent_how"] and ":" in row["publish_consent_how"],
            detail=str(row["publish_consent_how"]) + " — 'she said yes on the "
                   "telephone' is a real answer, and it is worth nothing "
                   "without a name against it")
    s.check("and it now appears on the public page",
            f"{TAG} words from Colette" in oc.get("/book").get_data(as_text=True))

    s.section("And that somebody said no")
    oc.post(f"/admin/feedback/stay/{said_yes}/permission",
            data={"answer": "no", "how": "email"}, follow_redirects=True)
    s.check("it comes straight off the public page",
            f"{TAG} words from Agnes" not in oc.get("/book").get_data(as_text=True),
            detail="withdrawing has to work, or the consent was never real")

    s.section("Putting a wrong row back to not-asked")
    oc.post(f"/admin/feedback/stay/{said_yes}/permission",
            data={"answer": "unasked"}, follow_redirects=True)
    back = conn.execute("SELECT publish_consent, publish_consent_at, "
                        "publish_consent_how FROM guest_feedback WHERE id = ?",
                        (said_yes,)).fetchone()
    s.check("the answer goes", back["publish_consent"] is None)
    s.check("and the date and the name with it",
            back["publish_consent_at"] is None
            and back["publish_consent_how"] is None,
            detail="a date left behind still says somebody was asked")
    s.check("and it is still off the public page",
            f"{TAG} words from Agnes" not in oc.get("/book").get_data(as_text=True))

    s.section("Rubbish is refused")
    r = oc.post(f"/admin/feedback/stay/{said_no}/permission",
                data={"answer": "maybe", "how": "spoken"})
    s.check("an answer that is not one of the three is a 400",
            r.status_code == 400, detail=f"HTTP {r.status_code}")
    r = oc.post(f"/admin/feedback/stay/{said_no}/permission",
                data={"answer": "yes", "how": "telepathy"})
    s.check("and so is a way of asking that is not one of ours",
            r.status_code == 400, detail=f"HTTP {r.status_code}")
    r = oc.post(f"/admin/feedback/nonsense/{said_no}/permission",
                data={"answer": "yes", "how": "email"})
    s.check("and a kind of feedback that does not exist is a 404",
            r.status_code == 404, detail=f"HTTP {r.status_code}")

    s.section("The form asks, and does not assume")
    # Read the templates rather than guessing a token URL. What matters is
    # that the box exists, is not checked, and is opt-in.
    import io as _io
    for tpl, what in (("templates/guest_feedback_form.html", "the stay form"),
                      ("templates/workshop_feedback_form.html", "the atelier form")):
        src = _io.open(tpl, encoding="utf-8").read()
        s.check(f"{what} asks", 'name="publish_consent"' in src)
        box = src[src.find('name="publish_consent"') - 120:
                  src.find('name="publish_consent"') + 120]
        s.check(f"{what} leaves it unticked", "checked" not in box,
                detail="a pre-ticked box is not consent")

    s.section("A guest who ticks it is recorded as having ticked it")
    # Through the real handler, not by writing the column.
    # Its own booking rather than whatever happens to be in the database. A
    # suite that borrows a real row passes or fails on what somebody else
    # left behind, and the day it goes red it looks like this code broke.
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    yesterday = house_today() - timedelta(days=3)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, arrival_date, departure_date,
                   party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?)""",
        (room["id"], TAG + "FORM", TAG.lower() + "-form-token",
         TAG + " Emile", TAG.lower() + "@example.invalid",
         (yesterday - timedelta(days=2)).isoformat(), yesterday.isoformat(),
         now))
    booking_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    anon = m.app.test_client()
    anon.post(f"/feedback/{TAG.lower()}-form-token",
              data={"rating": "5", "comment": TAG + " through the form",
                    "publish_consent": "1"}, follow_redirects=True)
    made = conn.execute(
        "SELECT publish_consent, publish_consent_how FROM guest_feedback "
        "WHERE booking_id = ?", (booking_id,)).fetchone()
    s.check("the tick is stored", made and made["publish_consent"] == 1,
            detail=str(dict(made)) if made else "no feedback row was written "
                   "at all, so the form did not go through")
    s.check("and says it came from the form",
            made and made["publish_consent_how"] == "form")

    s.section("And somebody who leaves it alone is not recorded as agreeing")
    conn.execute("DELETE FROM guest_feedback WHERE booking_id = ?", (booking_id,))
    conn.commit()
    anon2 = m.app.test_client()
    anon2.post(f"/feedback/{TAG.lower()}-form-token",
               data={"rating": "5", "comment": TAG + " no tick"},
               follow_redirects=True)
    quiet = conn.execute(
        "SELECT publish_consent FROM guest_feedback WHERE booking_id = ?",
        (booking_id,)).fetchone()
    s.check("it comes back as never asked, not as a refusal",
            quiet is not None and quiet["publish_consent"] is None,
            detail=str(dict(quiet)) if quiet else "no row")
    conn.execute("DELETE FROM guest_feedback WHERE booking_id = ?", (booking_id,))
    conn.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    conn.commit()

    s.section("And the atelier form does the same")
    # Its own path and its own INSERT. Testing only the stay form left the
    # atelier one free to record everybody as agreeing, and a control proved
    # exactly that.
    ws = conn.execute(
        """SELECT workshop_sessions.id AS sid FROM workshop_sessions
            WHERE workshop_sessions.end_date < ? ORDER BY end_date DESC LIMIT 1""",
        (house_today().isoformat(),)).fetchone()
    if not ws:
        wid = conn.execute(
            """INSERT INTO workshops (title, description, price_per_person,
                       default_capacity, active, created_at)
               VALUES (?, '', 100, 8, 1, ?)""",
            (TAG + " Past Atelier", now)) or conn.execute(
            "SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                       capacity, created_at)
               VALUES (?, ?, ?, 8, ?)""",
            (wid, (house_today() - timedelta(days=6)).isoformat(),
             (house_today() - timedelta(days=4)).isoformat(), now))
        sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    else:
        sid = ws["sid"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                   party_size, status, reference_code, manage_token,
                   total_price, created_at)
           VALUES (?, ?, ?, 1, 'confirmed', ?, ?, 100, ?)""",
        (sid, TAG + " Fabienne", TAG.lower() + ".ws@example.invalid",
         TAG + "WS", TAG.lower() + "-ws-token", now))
    wsb = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    anon3 = m.app.test_client()
    anon3.post(f"/workshops/feedback/{TAG.lower()}-ws-token",
               data={"rating": "5", "comment": TAG + " atelier, no tick"},
               follow_redirects=True)
    wrow = conn.execute(
        "SELECT publish_consent FROM workshop_feedback WHERE workshop_booking_id = ?",
        (wsb,)).fetchone()
    s.check("somebody who leaves the box alone is not recorded as agreeing",
            wrow is not None and wrow["publish_consent"] is None,
            detail=str(dict(wrow)) if wrow else "no feedback row was written, "
                   "so the form did not go through")
    conn.execute("DELETE FROM workshop_feedback WHERE workshop_booking_id = ?", (wsb,))
    conn.commit()
    anon4 = m.app.test_client()
    anon4.post(f"/workshops/feedback/{TAG.lower()}-ws-token",
               data={"rating": "5", "comment": TAG + " atelier, ticked",
                     "publish_consent": "1"}, follow_redirects=True)
    wrow = conn.execute(
        "SELECT publish_consent, publish_consent_how FROM workshop_feedback "
        "WHERE workshop_booking_id = ?", (wsb,)).fetchone()
    s.check("and somebody who ticks it is", wrow and wrow["publish_consent"] == 1,
            detail=str(dict(wrow)) if wrow else "no row")
    s.check("recorded as having come from the form",
            wrow and wrow["publish_consent_how"] == "form")
    conn.execute("DELETE FROM workshop_feedback WHERE workshop_booking_id = ?", (wsb,))
    conn.execute("DELETE FROM workshop_bookings WHERE id = ?", (wsb,))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()

    s.section("Only somebody who handles guests may record it")
    r = ec.post(f"/admin/feedback/stay/{said_no}/permission",
                data={"answer": "yes", "how": "email"}, follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
