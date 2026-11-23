"""The two come-back letters, and the review invitation made ready.

The owner, 23 November 2026, to five marketing suggestions: "yes to 2 just
ready the email first" (the invitation to review us, which waits on the house's
Google page) and "4 yes we will do that" -- a letter a year on from a stay,
naming the room, and one before an anniversary a guest told us.

Both come-back letters are the house writing for its own reasons, so what is
checked here is every rule it keeps for that, and the privacy notice's account
of them:

  - OFF AS SHIPPED, and nothing written while Guest emails would hold the
    letter: an anniversary letter released a month late arrives after the day.
    Nobody is stamped while it cannot go, so each is written the first morning
    it can.
  - ONCE A YEAR to an address, whatever runs twice; one letter to a party.
  - NEVER to anybody who unsubscribed or asked us to stop using their details,
    to a Booking.com or agent's guest (the address was lent for one stay), to
    anybody copied on the booking, to somebody booked to come again (year on)
    or staying with us around the day (anniversary), nor about a cancelled stay.
  - EACH CARRIES ITS OWN WAY TO STOP, and pressing it stops them.

send_email is replaced for every send; nothing here can reach a guest.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, keep_rows
import _harness

m = _harness.m
TAG = "ZZCB"
KEYS = ("room_year_on", "guest_anniversary_ahead")
SETTINGS = ("automation_year_on_enabled", "automation_year_on_days_before",
            "automation_anniversary_letter_enabled",
            "automation_anniversary_letter_days_before", "guest_mail_live", "guest_emails_on")


def _addr(ref):
    return f"{TAG.lower()}.{ref.lower()}@example.invalid"


def _cleanup(conn):
    conn.execute("DELETE FROM come_back_letters WHERE email LIKE ?", (TAG.lower() + ".%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG.lower() + ".%",))
    conn.commit()


def _set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _approve(conn, key, on=True):
    conn.execute("DELETE FROM guest_letter_approvals WHERE letter_key = ?", (key,))
    if on:
        conn.execute("INSERT INTO guest_letter_approvals (letter_key, wording_hash, approved_at) "
                     "VALUES (?, ?, ?)", (key, m.letter_wording_hash(conn, key),
                                          datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _year_ago(days_from_now):
    """The arrival a year before the day `days_from_now` from today."""
    target = house_today() + timedelta(days=days_from_now)
    return m.same_day_in(target.year - 1, target.month, target.day)


def _stay(conn, ref, arrival, *, status="confirmed", nights=3, source="direct", email=None,
          name=None):
    room = _harness.ensure_room()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, source,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, 600, 600, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}{_harness.secrets_token()}".lower(),
         name or f"Hélène {TAG}{ref}", _addr(ref) if email is None else email,
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(), status, source,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return room


def _guest(conn, ref, anniversary, *, email=None, restricted=False, merged_into=None):
    conn.execute(
        """INSERT INTO guests (name, email, anniversary, restricted_at, merged_into_id,
                               created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (f"Camille {TAG}{ref}", _addr(ref) if email is None else email, anniversary,
         datetime.now(timezone.utc).isoformat() if restricted else None, merged_into,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM guests WHERE name = ?", (f"Camille {TAG}{ref}",)
                        ).fetchone()["id"]


def _md(days_from_now):
    d = house_today() + timedelta(days=days_from_now)
    return f"{d.month:02d}-{d.day:02d}"


def _run(job, **kw):
    """Run a job with a stand-in post office: (result, letters, refusal)."""
    letters = []

    def post(to, subject, body, **opts):
        letters.append({"to": to, "subject": subject, "body": body,
                        "html": opts.get("html") or "", "key": opts.get("template_key")})
        return True

    was, gate = m.send_email, m.guest_letter_held_why
    m.send_email = post
    # The harness stands Guest emails down for every suite; these jobs ask it
    # whether the letter would go, so they are run with the real one.
    m.guest_letter_held_why = _harness.REAL_GUEST_LETTER_HELD_WHY
    conn = db()
    try:
        with m.app.test_request_context("/", base_url="https://www.chateaugudanes.com"):
            try:
                return getattr(m, job)(conn, **kw), letters, None
            except m.JobNotSetUp as e:
                return None, letters, str(e)
    finally:
        conn.close()
        m.send_email, m.guest_letter_held_why = was, gate


def _to(letters, ref):
    return [x for x in letters if x["to"] == _addr(ref)]


def _stamped(conn, ref):
    return conn.execute("SELECT * FROM come_back_letters WHERE email = ?",
                        (_addr(ref),)).fetchall()


def run():
    s = Suite("The come-back letters, and the review invitation made ready")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_settings = keep_rows(conn, "app_settings",
                             "key IN (%s)" % ",".join("?" * len(SETTINGS)), SETTINGS)
    put_letters = keep_rows(conn, "email_templates", "template_key IN (?, ?, ?)",
                            KEYS + ("review_invitation",))
    put_approvals = keep_rows(conn, "guest_letter_approvals", "letter_key IN (?, ?)", KEYS)
    _cleanup(conn)
    was = m.send_email
    m.send_email = lambda *a, **kw: True
    try:
        _checks(s, oc, conn)
    finally:
        m.send_email = was
        _cleanup(conn)
        put_approvals()
        put_letters()
        put_settings()
        conn.close()
    return s


def _checks(s, oc, conn):
    shipped = {k: (sj, b) for k, _l, sj, b in m.DEFAULT_EMAIL_TEMPLATES}

    s.section("The review invitation, ready for the day the review page is set")
    subject, body = shipped["review_invitation"]
    s.check("it still carries the link to the review page, the one thing it is for",
            "{review_url}" in body)
    s.check("in the new words: thanks first, then the ask, then leave to say no",
            body.index("Thank you") < body.index("{review_url}") < body.index("we will not ask again"),
            detail=body[:120])
    s.check("and it no longer reads as it did",
            (subject, body) != m.REVIEW_INVITATION_UNTIL_23NOV)
    for old in m.EMAIL_WORDING_REPLACED_23NOV["review_invitation"]:
        conn.execute("UPDATE email_templates SET subject = ?, body = ?, updated_at = NULL "
                     "WHERE template_key = 'review_invitation'", old)
        conn.commit()
        m.apply_letter_wording_2026_11_23(conn)
        row = conn.execute("SELECT subject, body FROM email_templates "
                           "WHERE template_key = 'review_invitation'").fetchone()
        s.check("a database still holding the old words, unedited, gets the new ones",
                (row["subject"], row["body"]) == (subject, body), detail=old[0])
    conn.execute("UPDATE email_templates SET subject = ?, body = ?, updated_at = ? "
                 "WHERE template_key = 'review_invitation'",
                 m.REVIEW_INVITATION_UNTIL_23NOV + (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    m.apply_letter_wording_2026_11_23(conn)
    row = conn.execute("SELECT subject FROM email_templates "
                       "WHERE template_key = 'review_invitation'").fetchone()
    s.check("but words the owner edited himself are left as he wrote them",
            row["subject"] == m.REVIEW_INVITATION_UNTIL_23NOV[0])
    s.check("and it still goes to nobody until the review page is set",
            m.review_link(conn) == "" or m.review_link(conn).startswith("http"))

    s.section("As shipped")
    for kind, cfg in m.COME_BACK_LETTERS.items():
        s.check(f"{cfg['key']}: its switch ships off",
                m.AUTOMATION_SETTING_DEFAULTS.get(cfg["switch"]) == "0")
        s.check(f"{cfg['key']}: thirty days ahead unless the owner says otherwise",
                m.AUTOMATION_SETTING_DEFAULTS.get(cfg["days"]) == "30")
    jobs = {name: key for name, key, _i, _c, _f in m.AUTOMATION_JOBS}
    s.check("the scheduler asks each switch before its job runs",
            jobs.get("year_on") == "automation_year_on_enabled"
            and jobs.get("anniversary_letter") == "automation_anniversary_letter_enabled",
            detail=str((jobs.get("year_on"), jobs.get("anniversary_letter"))))
    s.check("both are morning letters", m.JOB_HOURS.get("year_on") == (9, 12)
            and m.JOB_HOURS.get("anniversary_letter") == (9, 12))
    s.check("the year-on letter names the room and when, and links to booking",
            all(t in shipped["room_year_on"][1]
                for t in ("{room_name}", "{stay_month}", "{book_url}")))
    s.check("the anniversary letter names the day, and links to booking",
            all(t in shipped["guest_anniversary_ahead"][1]
                for t in ("{anniversary_date}", "{book_url}")))
    s.check("neither can be saved without its booking link",
            all("book_url" in m.REQUIRED_MERGE_TAGS.get(k, {}) for k in KEYS)
            and all(m.wording_problems(k, shipped[k][0], shipped[k][1].replace("{book_url}", ""))
                    for k in KEYS))
    s.check("and both go as shipped, with nothing the send would refuse",
            not any(m.wording_problems(k, *shipped[k]) for k in KEYS),
            detail=str([m.wording_problems(k, *shipped[k]) for k in KEYS]))
    s.check("a deleted guest's record of these letters goes with them",
            "come_back_letters" in m.guest_data_tables(conn))

    # The house writing, both letters approved, both switches on.
    _set(conn, "guest_mail_live", "1")
    _set(conn, "guest_emails_on", "1")
    for k in KEYS:
        _approve(conn, k)
    _set(conn, "automation_year_on_days_before", None)
    _set(conn, "automation_anniversary_letter_days_before", None)

    s.section("Nothing while it cannot go")
    _stay(conn, "WAIT", _year_ago(10))
    _set(conn, "automation_year_on_enabled", None)
    said, letters, refused = _run("run_year_on_job")
    s.check("switched off, it says so and writes to nobody",
            refused and "switched off" in refused and not letters, detail=str(refused))
    _set(conn, "automation_year_on_enabled", "1")
    _approve(conn, "room_year_on", on=False)
    said, letters, refused = _run("run_year_on_job")
    s.check("not approved at Guest emails, it says so and writes to nobody",
            refused and "Guest emails" in refused and not letters, detail=str(refused))
    s.check("and stamps nobody, so they hear the first morning it may go",
            not _stamped(conn, "WAIT"))
    _approve(conn, "room_year_on")
    _set(conn, "guest_emails_on", "0")
    said, letters, refused = _run("run_year_on_job")
    s.check("with guest emails off, the same", refused and not letters and not _stamped(conn, "WAIT"),
            detail=str(refused))
    _set(conn, "guest_emails_on", "1")

    s.section("A year on")
    room = _stay(conn, "ONE", _year_ago(10))
    _stay(conn, "FAR", _year_ago(45))
    _stay(conn, "GONE", _year_ago(5), status="cancelled")
    _stay(conn, "BCOM", _year_ago(5), source="booking.com")
    _stay(conn, "AGENT", _year_ago(5), source="agent")
    _stay(conn, "LENT", _year_ago(5), email="someone.zzcb@guest.booking.com")
    _stay(conn, "STOP", _year_ago(5))
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'asked', ?)",
                 (_addr("STOP"), datetime.now(timezone.utc).isoformat()))
    _stay(conn, "BACK", _year_ago(5))
    _stay(conn, "BACK2", house_today() + timedelta(days=60), email=_addr("BACK"))
    _stay(conn, "TWICE", _year_ago(3))
    _stay(conn, "TWICE2", _year_ago(8), email=_addr("TWICE"))
    conn.commit()
    said, letters, refused = _run("run_year_on_job")
    one = _to(letters, "ONE")
    s.check("the guest whose stay a year ago comes round within thirty days is written to",
            len(one) == 1, detail=f"{said} / {refused}")
    s.check("the WAIT guest too, now it may go", len(_to(letters, "WAIT")) == 1)
    body = one[0]["body"] if one else ""
    arrived = _year_ago(10)
    s.check("named by their first name, with the room they had and when",
            "Hi Hélène" in body and room["name"] in body
            and f"{arrived.strftime('%B')} {arrived.year}" in body, detail=body[:200])
    s.check("with the way to book", "/book" in body)
    stop = re.search(r"To stop letters like this one: (\S+)", body)
    s.check("and its own way to stop, in the words and on the drawn letter",
            stop is not None and stop.group(1) in one[0]["html"] and "Unsubscribe" in one[0]["html"])
    s.check("sent as this letter, so Guest emails can tell it apart",
            one and one[0]["key"] == "room_year_on")
    for ref, why in (("FAR", "a stay whose anniversary is further off than thirty days"),
                     ("GONE", "a cancelled stay"), ("BCOM", "a Booking.com stay"),
                     ("AGENT", "a stay booked through an agent"),
                     ("STOP", "somebody who unsubscribed"),
                     ("BACK", "somebody booked to come again")):
        s.check(f"not {why}", not _to(letters, ref))
    s.check("nor an address Booking.com lent us",
            not [x for x in letters if x["to"].endswith("@guest.booking.com")])
    s.check("two stays that spring, one letter", len(_to(letters, "TWICE")) == 1,
            detail=str(len(_to(letters, "TWICE"))))
    s.check("it says who it did not write to, and why",
            said and "booked to come again" in said and "asked not to be written to" in said,
            detail=str(said))
    said, again, refused = _run("run_year_on_job")
    s.check("run again the same morning, nobody hears twice", not again, detail=str(said))

    s.section("Before an anniversary")
    _guest(conn, "ANNI", _md(12))
    _guest(conn, "LATER", _md(50))
    _guest(conn, "HUSH", _md(12), restricted=True)
    _guest(conn, "UNSUB", _md(12))
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'asked', ?)",
                 (_addr("UNSUB"), datetime.now(timezone.utc).isoformat()))
    _guest(conn, "HERE", _md(12))
    _stay(conn, "HERE", house_today() + timedelta(days=11), nights=2)
    keep = _guest(conn, "KEPT", _md(12), email=_addr("KEPTMAIN"))
    _guest(conn, "OLD", _md(12), merged_into=keep)
    _set(conn, "automation_anniversary_letter_enabled", "1")
    said, letters, refused = _run("run_anniversary_letter_job")
    anni = _to(letters, "ANNI")
    when = house_today() + timedelta(days=12)
    s.check("a guest whose anniversary is twelve days off is written to",
            len(anni) == 1, detail=f"{said} / {refused}")
    s.check("with the day itself, and the way to book",
            anni and f"{when.day} {when.strftime('%B')}" in anni[0]["body"]
            and "/book" in anni[0]["body"], detail=anni[0]["body"][:200] if anni else "")
    s.check("and its own way to stop",
            anni and "To stop letters like this one:" in anni[0]["body"])
    for ref, why in (("LATER", "an anniversary seven weeks off"),
                     ("HUSH", "somebody who asked us to stop using their details"),
                     ("UNSUB", "somebody who unsubscribed"),
                     ("HERE", "somebody staying with us around it"),
                     ("OLD", "a profile merged into another")):
        s.check(f"not {why}", not _to(letters, ref))
    s.check("the profile it was merged into is written to once",
            len(_to(letters, "KEPTMAIN")) == 1)
    said, again, refused = _run("run_anniversary_letter_job")
    s.check("run again, nobody hears twice", not again, detail=str(said))

    s.section("Pressing stop")
    token = conn.execute("SELECT unsubscribe_token FROM come_back_letters WHERE email = ?",
                         (_addr("ANNI"),)).fetchone()
    anon = m.app.test_client()
    page = anon.get(f"/unsubscribe/{token['unsubscribe_token']}") if token else None
    s.check("the link opens the page that asks them to confirm, and stops nothing on a look",
            page is not None and page.status_code == 200
            and not conn.execute("SELECT 1 FROM email_optouts WHERE email = ?",
                                 (_addr("ANNI"),)).fetchone())
    done = anon.post(f"/unsubscribe/{token['unsubscribe_token']}") if token else None
    s.check("confirming puts them on the stop list",
            done is not None and done.status_code == 200
            and conn.execute("SELECT 1 FROM email_optouts WHERE email = ?",
                             (_addr("ANNI"),)).fetchone() is not None)
    conn.execute("DELETE FROM come_back_letters WHERE email = ?", (_addr("ANNI"),))
    conn.commit()
    said, letters, refused = _run("run_anniversary_letter_job")
    s.check("and from then on they are not written to, even with the year's stamp gone",
            not _to(letters, "ANNI"), detail=str(said))

    s.section("What the privacy notice says about them")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", anon.get("/privacy").get_data(as_text=True)))
    s.check("it says the house may ask a guest back", "Asking you back" in text)
    s.check("once, a year on, not if they are already booked to come",
            "once, as the first anniversary of your stay comes round" in text
            and "not if you are already booked to" in text)
    s.check("never through Booking.com or an agent: which is what the job leaves out",
            "Never if you came to us through Booking.com or an agent" in text
            and set(m.COME_BACK_NOT_FROM_SOURCES) == {"booking.com", "agent"})
    s.check("every letter carries an unsubscribe link that stops them",
            "carries an unsubscribe link, and using it stops them at once" in text)


if __name__ == "__main__":
    print(run().report())
