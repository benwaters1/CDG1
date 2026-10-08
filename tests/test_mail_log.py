"""The mail log: a line for every letter the house sends, and whether it went.

What there was: a guest's letters were kept with their correspondence, and
nothing else was kept -- the house's own notices, a sign-in link, a letter to a
colleague went out and left no trace.

What this holds:

  - Every letter leaves a line: to whom, when, what, whether it went.
    - A guest's letter: its words are read from their correspondence, not
      kept twice. Held while nothing can send it; sent later, it reads sent
      and says when, with no second line; thrown away, it says so.
    - The house's own letter is kept here, its private link taken out.
    - A letter that is a key leaves a line and never its words.
    - What a colleague was told is theirs: a line, not the words.
  - The page lists them with counted chips, finds them by address, opens each
    letter -- its words where they may be kept, and why not where they may
    not. The export is the view and carries no letters. An employee sees none.
  - Two years, like the letters, and a line whose letter has gone goes too.
  - A guest's copy of what we hold includes their lines; erasing them takes
    them. The notice says so.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from _harness import Suite, db, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZML"
GUEST = f"{TAG.lower()}@example.invalid"
COLLEAGUE = f"{TAG.lower()}.colleague@example.invalid"
KEY_LINK = "/my/zzmlportaltokenzzmlportaltoken"


def _cleanup():
    conn = db()
    try:
        conn.execute("DELETE FROM mail_log WHERE subject LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM guest_messages WHERE subject LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM guests WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM users WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.commit()
    finally:
        conn.close()


def _lines(where="1=1", *args):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM mail_log WHERE subject LIKE ? AND {where} ORDER BY id",
                            (f"%{TAG}%",) + args).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("The mail log")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _run(s):
    oc, ec, _owner, _emp = clients()
    now = _harness.datetime_now()
    room = _harness.ensure_room()
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Guest", GUEST, now))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (GUEST,)).fetchone()["id"]
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at, linked_guest_id)
                    VALUES (?, ?, ?, ?, ?, '2029-06-01', '2029-06-03', 2, 'confirmed', 300, ?, ?)""",
                 (room["id"], f"{TAG}S", f"{TAG}stok".lower(), f"{TAG} Guest", GUEST, now, gid))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()["id"]
    conn.execute("""INSERT INTO users (name, email, password_hash, role, created_at)
                    VALUES (?, ?, 'x', 'employee', ?)""", (f"{TAG} Colleague", COLLEAGUE, now))
    conn.commit()
    house = m.house_address_for(conn, "workshops")
    conn.close()

    # Outside a request, so each line is written as the letter goes.
    m.send_email(GUEST, f"{TAG} About your stay", "Your room is ready.", about=("room", bid),
                 area="rooms")
    m.send_email(GUEST, f"{TAG} Another letter", "A second one, to throw away.",
                 about=("room", bid))
    m.send_email(house, f"{TAG} Payment received", f"Their account: http://localhost{KEY_LINK}\n",
                 area="workshops", template_key="house_payment_received")
    m.send_email(GUEST, f"{TAG} Your sign-in link", f"Use this: http://localhost{KEY_LINK}",
                 keep=False)
    m.send_email(COLLEAGUE, f"{TAG} Your rota", "You are on the early shift on Friday.")

    s.section("Every letter leaves a line")
    stay = _lines("subject = ?", f"{TAG} About your stay")
    s.check("a guest's letter has its line, held while nothing can send it",
            len(stay) == 1 and stay[0]["status"] == "held" and stay[0]["outbox_id"]
            and stay[0]["to_address"] == GUEST and stay[0]["about_id"] == bid,
            detail=str([dict(r) for r in stay]))
    conn = db()
    filed = conn.execute("SELECT body FROM guest_messages WHERE id = ?",
                         (stay[0]["guest_message_id"] if stay else None,)).fetchone()
    conn.close()
    s.check("its words are read from their correspondence, not kept twice",
            stay and stay[0]["kept"] == "correspondence" and stay[0]["body"] is None
            and filed is not None and "Your room is ready." in filed["body"])
    notice = _lines("subject = ?", f"{TAG} Payment received")
    s.check("the house's own letter is kept here, its private link taken out",
            len(notice) == 1 and notice[0]["kept"] == "here" and notice[0]["area"] == "workshops"
            and "Their account:" in (notice[0]["body"] or "")
            and KEY_LINK not in (notice[0]["body"] or ""),
            detail=str(notice[0]["body"] if notice else None))
    key = _lines("subject = ?", f"{TAG} Your sign-in link")
    s.check("a letter that is a key leaves a line, and never its words",
            len(key) == 1 and key[0]["kept"] == "not" and key[0]["body"] is None
            and key[0]["status"] == "failed" and key[0]["failure"],
            detail=str([dict(r) for r in key]))
    rota = _lines("subject = ?", f"{TAG} Your rota")
    s.check("what a colleague was told is theirs: a line, not the words",
            len(rota) == 1 and rota[0]["kept"] == "colleague" and rota[0]["body"] is None,
            detail=str([dict(r) for r in rota]))

    s.section("Sent later, or thrown away")
    # The real send_email, through a stand-in mail server: the outbox's own
    # send must bring the held letter's line up to date, not write a second.
    class _SMTP:
        went = []

        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            _SMTP.went.append(msg["To"])

    saved = (m.smtplib.SMTP, m.SMTP_HOST, m.SMTP_USERNAME, m.SMTP_PASSWORD, m.SMTP_FROM)
    m.smtplib.SMTP = _SMTP
    m.SMTP_HOST, m.SMTP_USERNAME, m.SMTP_PASSWORD = ("smtp.example.invalid", "u", "p")
    m.SMTP_FROM = "house@example.invalid"
    try:
        oc.post("/admin/email-outbox/send", data={"id": str(stay[0]["outbox_id"])})
    finally:
        (m.smtplib.SMTP, m.SMTP_HOST, m.SMTP_USERNAME, m.SMTP_PASSWORD, m.SMTP_FROM) = saved
    after = _lines("subject = ?", f"{TAG} About your stay")
    s.check("a held letter sent later reads sent, and says when -- on the same line",
            _SMTP.went == [GUEST] and len(after) == 1 and after[0]["status"] == "sent"
            and after[0]["delivered_at"],
            detail=f"{_SMTP.went} / {[dict(r) for r in after]}")
    other = _lines("subject = ?", f"{TAG} Another letter")
    oc.post(f"/admin/email-outbox/{other[0]['outbox_id']}/discard")
    s.check("one thrown away from the outbox says so",
            _lines("subject = ?", f"{TAG} Another letter")[0]["status"] == "discarded")

    s.section("The page")
    page = oc.get(f"/admin/mail-log?q={TAG}").get_data(as_text=True)
    text = visible_text(page)
    s.check("every letter is listed, with whether it went",
            all(w in text for w in (f"{TAG} About your stay", f"{TAG} Payment received",
                                    f"{TAG} Your sign-in link", f"{TAG} Your rota"))
            and "Sent" in text and "Thrown away" in text and "Did not go" in text)
    conn = db()
    lv = m.mail_log_list_view(conn, {"q": TAG})
    conn.close()
    to = next((f for f in lv["facets"] if f["key"] == "to"), {"options": []})
    counts = {o["value"]: o["count"] for o in to["options"]}
    s.check("counted by who it went to",
            counts.get("A guest") == 3 and counts.get("The house") == 1
            and counts.get("A colleague") == 1, detail=str(counts))
    # AN INBOX THE HOUSE HAS RETIRED IS STILL THE HOUSE. The audience was
    # decided against the one owner address configured today, so the day the
    # house moved from owner@ to accounts@, twelve notes it had written to
    # itself were filed under "Somebody else" -- a catch-all chip standing for
    # twelve letters to the owner, which is worse than no chip at all.
    conn = db()
    conn.execute(
        "INSERT INTO mail_log (to_address, subject, status, kept, created_at) "
        "VALUES (?, ?, 'sent', 'here', ?)",
        ("owner@chateaugudanes.com", TAG + " Retired inbox",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    # The owner address is accounts@ for this check, which is what the house
    # actually moved to. Without that, owner@ IS the configured address here
    # and the check passes for the wrong reason -- a control found exactly
    # that, and it would have gone on reading green while production filed
    # twelve letters to the owner under "Somebody else".
    was_owner = m.owner_email
    try:
        m.owner_email = lambda _conn: "accounts@chateaugudanes.com"
        retired = m.mail_log_list_view(conn, {"q": TAG + " Retired"})
    finally:
        m.owner_email = was_owner
    conn.close()
    s.check("a letter to an inbox the house no longer uses is still the house",
            retired["rows"] and retired["rows"][0]["audience"] == "The house",
            detail=str([(r["to_address"], r["audience"]) for r in retired["rows"]]))
    # Taken out again: the export check below counts what the view holds, and
    # a fixture left lying about would move a number that is being asserted.
    conn = db()
    conn.execute("DELETE FROM mail_log WHERE subject = ?", (TAG + " Retired inbox",))
    conn.commit()
    conn.close()

    conn = db()
    found = m.mail_log_list_view(conn, {"q": COLLEAGUE})
    conn.close()
    s.check("found by the address it went to",
            [r["subject"] for r in found["rows"]] == [f"{TAG} Your rota"],
            detail=str([r["subject"] for r in found["rows"]]))
    opened = visible_text(oc.get(f"/admin/mail-log/{stay[0]['id']}").get_data(as_text=True))
    s.check("a guest's letter opens with its words, from their correspondence",
            "Your room is ready." in opened, detail=opened[:300])
    shut = visible_text(oc.get(f"/admin/mail-log/{key[0]['id']}").get_data(as_text=True))
    s.check("a key opens to why its words were never kept, and not to the key",
            "never kept" in shut and KEY_LINK not in shut, detail=shut[:300])
    theirs = visible_text(oc.get(f"/admin/mail-log/{rota[0]['id']}").get_data(as_text=True))
    s.check("and a colleague's letter to why it is theirs",
            "colleague was told is theirs" in theirs and "early shift" not in theirs)
    exported = oc.get(f"/admin/mail-log/export.csv?q={TAG}").get_data(as_text=True)
    lines = list(csv.DictReader(io.StringIO(exported)))
    s.check("the export is the view, and carries no letters",
            len(lines) == len(lv["rows"]) == 5
            # Whatever the log holds the words of -- the house's own letter
            # here -- as well as the ones it reads from elsewhere.
            and not any(w in v for w in ("Their account:", "Your room is ready.", "early shift")
                        for r in lines for v in r.values()),
            detail=f"{len(lines)} rows")
    s.check("an employee sees none of it",
            ec.get("/admin/mail-log").status_code in (302, 403)
            and ec.get(f"/admin/mail-log/{stay[0]['id']}").status_code in (302, 403)
            and ec.get("/admin/mail-log/export.csv").status_code in (302, 403))

    s.section("Two years, like the letters")
    conn = db()
    conn.execute("UPDATE mail_log SET created_at = ? WHERE id = ?",
                 ((m.datetime.now(m.timezone.utc) - timedelta(days=800)).isoformat(), rota[0]["id"]))
    conn.execute("DELETE FROM guest_messages WHERE id = ?", (stay[0]["guest_message_id"],))
    conn.commit()
    result = m.purge_mail_log(conn)
    conn.close()
    s.check("a line two years old goes",
            not _lines("id = ?", rota[0]["id"]), detail=str(result))
    s.check("and so does a line whose letter has gone",
            not _lines("id = ?", stay[0]["id"]) and result.get("old lines in the mail log", 0) >= 2,
            detail=str(result))
    import inspect
    job = inspect.getsource(m.run_health_notes_purge_job)
    s.check("and the housekeeping job does it", "purge_mail_log(conn)" in job)

    s.section("It goes with the person's data")
    conn = db()
    copy = m.guest_data_export(conn, GUEST)
    s.check("a guest's copy of what we hold includes their lines",
            any(r["subject"] == f"{TAG} Your sign-in link"
                for r in copy["tables"].get("mail_log", [])),
            detail=str(sorted(copy["tables"])))
    m.guest_data_erase(conn, GUEST)
    conn.commit()
    left = conn.execute("SELECT COUNT(*) AS c FROM mail_log WHERE LOWER(to_address) = ?",
                        (GUEST,)).fetchone()["c"]
    conn.close()
    s.check("and erasing them takes them", left == 0, detail=str(left))

    s.section("The notice says so")
    words = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("a line for each letter, and for a key that line is all",
            "We also keep a line for each letter we send" in words
            and "for a letter whose text is a key, that line is all we keep" in words)
