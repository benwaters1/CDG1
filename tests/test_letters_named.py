"""Every letter the house sends is named in the mail log and the held list.

WHAT WENT WRONG. Both pages name a letter from the template that made it, and
two thirds of the log -- 58 of 87 held letters in a seeded fortnight -- read
"A letter of the house's own". Three faults made that:
  - the booking confirmation is made by email_template_text, which never left
    its name by the subject for send_email to find; only render_email_template
    did, so the commonest letter in the house was the one it could not name;
  - the name was kept in Flask's g, which exists only in a request, so every
    letter the automation thread sends -- balance reminders, review
    invitations, feedback requests -- reached the log unnamed;
  - the house's own notices, the staff's and the credentials are written in
    code, with no template at all, and nothing named them.

WHAT THIS PINS. The template's name is kept in a request and outside one; a
letter written in code is named by the function writing it (LETTERS_IN_CODE),
held against the functions that actually write one, both ways; campaigns and
letters to a whole group carry their own names; and the log and the held list
show those names through one labeller.

send_email's record of a letter is captured here; nothing is sent.
"""
import ast
import os

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZLN"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Functions that write a letter in code but are named another way: by hand (the
# sender is recorded), a held letter going out again (its line is updated, not
# written), and the two that pass a name of their own.
NAMED_OTHERWISE = {
    "write_to_guest": "written by hand, and filed as such",
    "send_held_letter": "a held letter going out; its original line is brought up to date",
    "write_about_stay": "passes the letter on; the template or the caller names it",
    "send_campaign": "passes campaign:<name>",
    "send_group_note": "passes group:<kind>",
}


def _code_writers():
    """Functions in app.py that send a letter whose subject is written in code."""
    tree = ast.parse(open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())
    out = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", None) in ("send_email", "write_about_stay")):
                continue
            if any(k.arg == "template_key" for k in call.keywords) or len(call.args) < 2:
                continue
            if isinstance(call.args[1], (ast.Constant, ast.JoinedStr)):
                out.add(node.name)
    return out


class Filed:
    """keep_guest_message, stood in for: what send_email files, kept here."""
    def __init__(self):
        self.rows = []

    def __call__(self, to_address, subject, body, channel="email", delivered=False, **more):
        self.rows.append(dict(more, to=to_address, subject=subject))


class _Capture:
    def __enter__(self):
        self.real = (m.keep_guest_message, m.queue_undelivered)
        self.filed = Filed()
        m.keep_guest_message = self.filed
        m.queue_undelivered = lambda *a, **k: None
        return self.filed

    def __exit__(self, *exc):
        m.keep_guest_message, m.queue_undelivered = self.real
        return False


def run():
    s = Suite("Every letter named")
    oc, _ec, _o, _e = clients()

    s.section("The list of letters written in code is the code's own")
    writers = _code_writers()
    unnamed = sorted(writers - set(m.LETTERS_IN_CODE) - set(NAMED_OTHERWISE))
    s.check("every function that writes a letter in code has a name for it", not unnamed,
            detail=f"{unnamed} -- add it to LETTERS_IN_CODE with what to call the letter")
    stale = sorted(k for k in m.LETTERS_IN_CODE if not callable(getattr(m, k, None)))
    s.check("and nothing on the list names a function that no longer exists", not stale,
            detail=str(stale))
    s.check("the reader found the writers it should", len(writers) >= 25,
            detail=f"{len(writers)} -- a reader that finds nothing agrees with everything")

    s.section("A template's name is kept, in a request and outside one")
    with _Capture() as filed:
        with m.app.test_request_context():
            conn = db()
            subject, body, _p = m.email_template_text(conn, "room_confirmed", {
                "guest_name": "A", "room_name": "The King Room"})
            conn.close()
            m.send_email(f"{TAG.lower()}a@example.invalid", subject, body)
        s.check("the booking confirmation is filed as itself",
                filed.rows and filed.rows[-1].get("template_key") == "room_confirmed",
                detail=str(filed.rows[-1:]))
        with m.app.app_context():
            conn = db()
            subject, body, _l = m.render_email_template(conn, "room_balance_before", {
                "guest_name": "B", "room_name": "The King Room"})
            conn.close()
            s.check("(this part runs with no request, as the jobs do)",
                    not m.has_request_context())
            m.send_email(f"{TAG.lower()}b@example.invalid", subject, body)
        s.check("a letter a job sends keeps its template's name too",
                filed.rows[-1].get("template_key") == "room_balance_before",
                detail=str(filed.rows[-1:]))

    s.section("A letter written in code is named by what writes it")
    with _Capture() as filed:
        r = oc.post("/contact", data={"email": f"{TAG.lower()}c@example.invalid",
                                      "name": f"{TAG} Asker", "message": "Two nights?",
                                      **_harness.contact_form_fields()})
        mine = [x for x in filed.rows if x.get("template_key") == "code:contact_page"]
        s.check("the contact form's notice to the house is filed as the contact page's",
                r.status_code in (200, 302) and bool(mine),
                detail=str([(x["subject"], x.get("template_key")) for x in filed.rows]))

        def pass_message():          # named as the real writer is, to ask the walk
            m.send_email(f"{TAG.lower()}d@example.invalid", "Urgent from the pass", "Fire")
        with m.app.test_request_context():
            pass_message()
        s.check("found up the stack, however deep the send is",
                filed.rows[-1].get("template_key") == "code:pass_message")

    s.section("Campaigns and letters to a group carry their own names")
    with _Capture() as filed:
        with m.app.test_request_context():
            conn = db()
            m.send_campaign(conn, {"id": None, "name": f"{TAG} Spring", "area": "general",
                                   "subject": "s", "body": "b"},
                            {f"{TAG.lower()}e@example.invalid": "E"}, None)
            conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ?",
                         (TAG.lower() + "%",))
            conn.commit()
            conn.close()
        s.check("a campaign is filed under its own name",
                filed.rows and filed.rows[-1].get("template_key") == f"campaign:{TAG} Spring")

    s.section("One labeller, for every page that names a letter")
    s.check("a template", m.letter_label("room_confirmed") == "Rooms: Booking confirmed")
    s.check("a letter written in code",
            m.letter_label("code:pass_message") == "Kitchen: a call from the pass")
    s.check("a campaign", m.letter_label(f"campaign:{TAG} Spring") == f"Campaign: {TAG} Spring")
    s.check("a letter to a whole session",
            m.letter_label("group:workshop") == "To everyone on a session")
    s.check("one written by hand", m.letter_label(None, sent_by=1) == "Written by hand")

    s.section("A letter about money answers to accounts; nothing answers to ariege@")
    # The owner, 12 October: money to accounts, the general address
    # reservations, as little as possible to ariege@. Asked of the code with
    # no mailbox connected, which is how the fallbacks decide.
    s.check("money letters reply to accounts@",
            m.reply_to_header_for("accounts") == "accounts@chateaugudanes.com",
            detail=str(m.reply_to_header_for("accounts")))
    s.check("a room's letters to reservations@",
            m.reply_to_header_for("rooms") == "reservations@chateaugudanes.com")
    replies = {a: m.reply_to_header_for(a) for a in list(m.REPLY_TO_AREAS) + [None]}
    s.check("and no kind of letter replies to ariege@",
            not any("ariege@" in (v or "") for v in replies.values()), detail=str(replies))
    with _Capture() as filed:
        with m.app.test_request_context():
            conn = db()
            for key in ("room_statement", "refund_issued", "workshop_deposit_receipt"):
                subject, body, _p = m.email_template_text(conn, key, {"guest_name": "M"})
                m.send_email(f"{TAG.lower()}m@example.invalid", subject, body, area="rooms")
            conn.close()
        s.check("a statement, a refund and a deposit receipt all answer to accounts, "
                "whatever kind of booking sent them",
                [x.get("area") for x in filed.rows[-3:]] == ["accounts"] * 3,
                detail=str([(x.get("template_key"), x.get("area")) for x in filed.rows[-3:]]))
        with m.app.test_request_context():
            conn = db()
            subject, body, _p = m.email_template_text(conn, "room_confirmed", {"guest_name": "M"})
            conn.close()
            m.send_email(f"{TAG.lower()}n@example.invalid", subject, body, area="rooms")
        s.check("and a booking confirmation still answers to the bookings inbox",
                filed.rows[-1].get("area") == "rooms")

    s.section("The mail log and the held list show the names")
    conn = db()
    now = _harness.datetime_now()
    conn.execute("INSERT INTO email_outbox (to_address, subject, body, reason, created_at) "
                 "VALUES (?, 'Urgent from the pass', 'x', 'no email provider configured', ?)",
                 (f"{TAG.lower()}f@example.invalid", now))
    outbox_id = conn.execute("SELECT id FROM email_outbox WHERE to_address = ?",
                             (f"{TAG.lower()}f@example.invalid",)).fetchone()[0]
    for key, to, ob in (("code:pass_message", "f", outbox_id),
                        (f"campaign:{TAG} Spring", "g", None),
                        ("room_confirmed", "h", None)):
        conn.execute(
            """INSERT INTO mail_log (to_address, subject, status, template_key, kept, outbox_id,
               created_at) VALUES (?, 'x', ?, ?, 'here', ?, ?)""",
            (f"{TAG.lower()}{to}@example.invalid", "held" if ob else "sent", key, ob, now))
    conn.commit()
    conn.close()
    log = oc.get(f"/admin/mail-log?q={TAG.lower()}").get_data(as_text=True)
    s.check("the log names a notice written in code", "Kitchen: a call from the pass" in log)
    s.check("and a campaign", f"Campaign: {TAG} Spring" in log)
    s.check("and the booking confirmation", "Rooms: Booking confirmed" in log)
    held = oc.get(f"/admin/email-outbox?q={TAG.lower()}").get_data(as_text=True)
    s.check("the held list names it the same way", "Kitchen: a call from the pass" in held)

    conn = db()
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
