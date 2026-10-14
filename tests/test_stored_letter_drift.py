"""A stored guest letter that has fallen behind the house rules on payment is named.

init_db brings a stored letter up to new wording only while it still reads
word for word as shipped. On 13 October 2026 a real copy had the stay
confirmation with no payment sentence and "ninety-four" rooms, and the balance
reminder offering to take the money "on arrival if you would rather" -- a
paragraph added on 1 October had made both differ, so neither migration ran.
Nothing said so.

Held here: the shipped wording breaks none of the rules; that drift is named on
the home page, the readiness page and as a task, linking to the letter's
editor; nothing rewrites it; and every line closes itself once the letter keeps
to the rules. The fixtures are the wording found that day, so this runs the
same on a fresh seed and on a copy of a real database.
"""
from _harness import Suite, clients, db, flashes, keep_rows
import _harness

m = _harness.m

# As stored on 13 Oct 2026 (local copy), trimmed to what matters.
DRIFT_ROOM_CONFIRMED = (
    "Your room is held, {first_name}\n\nFive bedrooms of ninety-four are finished, and one of "
    "them is yours for the dates below.\n\n{stay_details}\n\nPlease pack lightly: the bedrooms "
    "are reached by the original staircase, and there is no lift.")
DRIFT_BALANCE_BEFORE = (
    "Hi {guest_name},\n\nThere is {balance_amount} still outstanding on your stay. You can "
    "settle it online before you travel, or on arrival if you would rather:\n{manage_url}\n\n"
    "Reference code: {reference_code}\n\n— Château de Gudanes")


def _set(conn, key, body):
    conn.execute("UPDATE email_templates SET body = ?, updated_at = '2026-10-01T09:00:00+00:00' "
                 "WHERE template_key = ?", (body, key))
    conn.commit()


def _restore_all_shipped(conn):
    for key, _l, subject, body in m.DEFAULT_EMAIL_TEMPLATES:
        conn.execute("UPDATE email_templates SET subject = ?, body = ?, updated_at = NULL "
                     "WHERE template_key = ?", (subject, body, key))
    conn.commit()


def _home(conn):
    with m.app.test_request_context("/"):
        return [w for w in m.owner_home_warnings(conn, m.house_today())
                if "guest letter" in w["title"]]


def _readiness(conn):
    return next(c for c in m.readiness_checks(conn, include_slow=False)
                if c["label"] == "Guest letters keep to the house rules on payment")


def _letter_tasks(conn):
    with m.app.test_request_context("/"):
        m.generate_watch_tasks(conn)
    return [r["title"] for r in conn.execute(
        "SELECT title FROM tasks WHERE origin = ? AND status != 'done' AND title LIKE ?",
        (m.WATCH_TASK_ORIGIN, "Guest letter out of step%"))]


def run():
    s = Suite("Stored letters held to the house rules on payment")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = keep_rows(conn, "email_templates", "1 = 1")
    top_rev = conn.execute("SELECT COALESCE(MAX(id), 0) FROM email_template_revisions").fetchone()[0]
    try:
        _run(s, oc, conn)
    finally:
        put_back()
        conn.execute("DELETE FROM email_template_revisions WHERE id > ?", (top_rev,))
        conn.commit()
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.close()
    return s


def _run(s, oc, conn):
    s.section("The shipped wording keeps its own rules")
    shipped = {k: m.letter_rule_breaches(k, sj, b) for k, _l, sj, b in m.DEFAULT_EMAIL_TEMPLATES}
    s.check("no shipped letter breaks a rule", not any(shipped.values()),
            detail=str({k: v for k, v in shipped.items() if v}))
    s.check("the letters that must carry the sentence are the ones test_confirmed_facts names",
            m.LETTERS_WITH_PAYMENT_LINE == {"room_confirmed", "room_balance_before",
                                            "room_balance_after", "workshop_confirmed",
                                            "workshop_deposit_receipt", "workshop_balance_reminder"})

    s.section("With every letter as shipped, nothing is said")
    _restore_all_shipped(conn)
    s.check("no line on the home page", _home(conn) == [], detail=str(_home(conn)))
    s.check("the readiness check passes, and is listed", _readiness(conn)["ok"])
    s.check("and no task", _letter_tasks(conn) == [])

    s.section("The drift found on 13 October is named")
    _set(conn, "room_confirmed", DRIFT_ROOM_CONFIRMED)
    _set(conn, "room_balance_before", DRIFT_BALANCE_BEFORE)
    found = {d["key"]: {b["rule"] for b in d["breaches"]} for d in m.stored_letter_drift(conn)}
    s.check("the confirmation: no payment sentence, and ninety-four",
            found.get("room_confirmed") == {"payment_line", "room_count"}, detail=str(found))
    s.check("the reminder: no payment sentence, and pay on arrival",
            found.get("room_balance_before") == {"payment_line", "pay_at_house"}, detail=str(found))
    s.check("and nothing else", set(found) == {"room_confirmed", "room_balance_before"},
            detail=str(found))
    home = _home(conn)
    s.check("one line on the home page, a blocker because a letter says something untrue",
            len(home) == 1 and home[0]["severity"] == "blocker" and home[0]["count"] == 2,
            detail=str(home))
    s.check("naming the letters",
            home and "Rooms: Balance due, before arrival" in home[0]["detail"]
            and "Rooms: Booking confirmed" in home[0]["detail"], detail=str(home))
    s.check("linking to the editor, filtered to them, at the first one",
            home and home[0]["href"].startswith("/management/email-templates?rules=Out")
            and "#tpl-room_" in home[0]["href"], detail=str(home and home[0]["href"]))
    check = _readiness(conn)
    s.check("the readiness check fails as a blocker",
            not check["ok"] and check["severity"] == "blocker", detail=str(check))
    tasks = _letter_tasks(conn)
    s.check("one task per letter",
            sorted(tasks) == sorted(f"Guest letter out of step with the payment rules — {l}"
                                    for l in ("Rooms: Booking confirmed",
                                              "Rooms: Balance due, before arrival")),
            detail=str(tasks))
    stored = conn.execute("SELECT body FROM email_templates WHERE template_key = "
                          "'room_balance_before'").fetchone()["body"]
    s.check("and nothing rewrote the letter", stored == DRIFT_BALANCE_BEFORE)

    s.section("The editor shows it")
    page = oc.get("/management/email-templates?rules=Out+of+step").get_data(as_text=True)
    text = _harness.visible_text(page)
    s.check("the chip lists exactly those two", page.count('class="tpl-card') == 2
            and 'id="tpl-room_confirmed"' in page and 'id="tpl-room_balance_before"' in page)
    s.check("each card says what is wrong", "on arrival if you would rather" in text
            and "ninety-four" in text and "Restore original wording" in page)

    s.section("A reworded letter that says the rule is not breaking it")
    said = m.letter_rule_breaches("room_balance_before", "x", (
        "Nothing is taken at the door: settle it online.\n{manage_url}\n\n" + m.PAYMENT_SAFETY_LINE))
    s.check("\"nothing is taken at the door\" is not an offer to take it", said == [], detail=str(said))
    s.check("champagne on arrival is not a payment",
            m.letter_rule_breaches("room_confirmed", "x", "A glass of champagne on arrival. {stay_details} "
                                   + m.PAYMENT_SAFETY_LINE) == [])
    ev = m.letter_rule_breaches("event_balance_reminder", "x", "Pay it here.\n\n" + m._PAYMENT_SAFETY_LINE_5OCT)
    s.check("an event letter carrying the card-only line is untrue",
            [b["rule"] for b in ev] == ["event_card_only"] and ev[0]["untrue"], detail=str(ev))

    s.section("It closes itself")
    oc.post("/management/email-templates/room_balance_before/restore")
    left = {d["key"] for d in m.stored_letter_drift(conn)}
    s.check("restoring one takes that one off", left == {"room_confirmed"}, detail=str(left))
    s.check("and closes its task, leaving the other",
            _letter_tasks(conn) == ["Guest letter out of step with the payment rules — "
                                    "Rooms: Booking confirmed"])
    r = oc.post("/management/email-templates/room_confirmed/edit", data={
        "subject": "Booking confirmed — {room_name}",
        "body": "Your room is held, {first_name}\n\n{stay_details}\n\nMy own words.\n\n"
                + m.PAYMENT_SAFETY_LINE}, follow_redirects=True)
    s.check("editing the other in the owner's own words is enough",
            m.stored_letter_drift(conn) == [] and _home(conn) == [] and _letter_tasks(conn) == [],
            detail=str(flashes(r)))
    r = oc.post("/management/email-templates/room_confirmed/edit", data={
        "subject": "Booking confirmed — {room_name}",
        "body": "Your room is held, {first_name}\n\n{stay_details}\n\nPay in cash when you arrive."},
        follow_redirects=True)
    s.check("saving drifted wording is allowed, and said at once",
            any("out of step" in f for f in flashes(r))
            and "Pay in cash" in conn.execute("SELECT body FROM email_templates WHERE "
                                              "template_key = 'room_confirmed'").fetchone()["body"],
            detail=str(flashes(r)))

    s.section("Negative control: a detector that misses the sentence is caught")
    real = m.LETTERS_WITH_PAYMENT_LINE
    m.LETTERS_WITH_PAYMENT_LINE = frozenset()
    try:
        _set(conn, "room_balance_after", "Hi {guest_name}. Pay here:\n{manage_url}")
        missed = "room_balance_after" not in {d["key"] for d in m.stored_letter_drift(conn)}
    finally:
        m.LETTERS_WITH_PAYMENT_LINE = real
    caught = "room_balance_after" in {d["key"] for d in m.stored_letter_drift(conn)}
    s.check("broken, the letter slips through; restored, it is named", missed and caught)


if __name__ == "__main__":
    print(run().report())
