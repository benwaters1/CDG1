"""Merge tags a guest would otherwise read.

The email templates are DATA: the owner edits the wording in the app, and
render_email_template substitutes a context dict into whatever they typed. A tag
the sender does not pass was left in the text UNREPLACED rather than crashing the
send — deliberately, because losing a booking confirmation over a typo is worse.
Which means the typo reached a guest as visible braces, and nothing anywhere told
the owner which tags were even available. The only way to find out was to guess
one, save it, send an email and look.

THE CHECK THAT MATTERS MOST IS THE LAST ONE. EMAIL_TEMPLATE_TAGS is a hand-
written list, and a hand-written list of what the code does goes stale the first
time somebody adds a field to a sender. So it is compared against what the
senders actually pass — read out of app.py — and any drift in either direction
fails. Without that this is a list that agrees with itself.
"""
import io
import os
import re
from collections import defaultdict

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
KEY = "room_feedback_request"


def _template(key):
    conn = db()
    try:
        return conn.execute("SELECT * FROM email_templates WHERE template_key = ?",
                            (key,)).fetchone()
    finally:
        conn.close()


def _restore(key):
    shipped = next((d for d in m.DEFAULT_EMAIL_TEMPLATES if d[0] == key), None)
    if not shipped:
        return
    conn = db()
    conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = ?",
                 (shipped[2], shipped[3], key))
    conn.commit()
    conn.close()


def _senders_from_source():
    """What each sender actually passes, read out of app.py.

    Deliberately a second implementation rather than a call into the app: this
    is the thing EMAIL_TEMPLATE_TAGS is being checked against, so asking the app
    what it passes by asking the same constant would prove nothing.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app.py")
    src = io.open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n")
    passed = defaultdict(set)

    def block(text, open_at, opener, closer):
        depth, i = 0, open_at
        while i < len(text):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    return text[open_at + 1:i]
            i += 1
        return ""

    for call in re.finditer(
            r'render_email_template\(\s*conn\s*,\s*"([a-z0-9_]+)"\s*,\s*\{', src):
        passed[call.group(1)] |= set(re.findall(
            r'"([a-z0-9_]+)"\s*:', block(src, call.end() - 1, "{", "}")))

    # Senders that hand over a context built elsewhere.
    for name in ("event_email_context", "pos_receipt_email_context",
                 "booking_email_context", "workshop_email_context",
                 "restaurant_email_context"):
        fn = re.search(r"def " + name + r"\(", src)
        if not fn:
            continue
        end = src.find("\ndef ", fn.end())
        keys = set(re.findall(r'"([a-z0-9_]+)"\s*:', src[fn.start():end]))
        for call in re.finditer(
                r'(?:send_\w+_email|render_email_template)\(\s*conn\s*,\s*(?:\w+\s*,\s*)?'
                r'"([a-z0-9_]+)"\s*,\s*' + name, src):
            passed[call.group(1)] |= keys
    return passed


def run():
    s = Suite("Merge tags")
    oc, ec, owner, emp = clients()
    _restore(KEY)

    s.section("Every seeded template declares what it may use")
    seeded = {k for k, _l, _s, _b in m.DEFAULT_EMAIL_TEMPLATES}
    undeclared = sorted(seeded - set(m.EMAIL_TEMPLATE_TAGS))
    s.check("none is left out", not undeclared,
            detail=f"{undeclared} — an undeclared template accepts any tag, so "
                   "the guard is off for exactly the wording nobody checked")

    s.section("And the shipped wording passes its own guard")
    # If the house ships wording the house then refuses, the rule is wrong.
    broken = [(k, m.unknown_merge_tags(k, subj, body))
              for k, _l, subj, body in m.DEFAULT_EMAIL_TEMPLATES
              if m.unknown_merge_tags(k, subj, body)]
    s.check("nothing shipped would be refused", not broken, detail=f"{broken}")

    s.section("A tag nothing fills is refused when you save it")
    before = _template(KEY)["body"]
    r = oc.post(f"/management/email-templates/{KEY}/edit", data={
        "subject": "How was your stay?",
        "body": "Hi {guest_name}, tell us about {not_a_real_tag}. {feedback_url}",
    }, follow_redirects=True)
    after = _template(KEY)
    s.check("the wording is not saved", after["body"] == before,
            detail="saved happily, and the first person to see the mistake is "
                   "the guest")
    s.check("and it names the tag", any("not_a_real_tag" in f for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — 'invalid' with no name sends the owner "
                   "hunting through their own paragraph")
    s.check("and lists what this one can use",
            any("{guest_name}" in f for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("A real one saves")
    r = oc.post(f"/management/email-templates/{KEY}/edit", data={
        "subject": "How was your stay?",
        "body": "Hi {guest_name}, how was {room_name}? {feedback_url}",
    }, follow_redirects=True)
    s.check("it goes through", "{room_name}" in (_template(KEY)["body"] or ""),
            detail=f"{flashes(r)[:1]} — a guard that refuses valid wording is "
                   "worse than none, because it teaches people to work round it")

    s.section("The page says what is available, not only what is used")
    body = oc.get("/management/email-templates").get_data(as_text=True)
    s.check("the available list is on it", "Available here" in body,
            detail="the page showed what each template uses and never what it "
                   "could use")
    s.check("with a tag the shipped wording does not use", "{room_name}" in body
            or "room_name" in body,
            detail="the list is just the tags already in the text again")
    s.check("and says what happens if you type another",
            "refused when you save" in body,
            detail="a rule nobody is told about reads as a bug when it fires")

    s.section("If one gets in anyway, the send does not carry it to a guest")
    # The edit form refuses these, so reaching this state means the row was
    # changed some other way — a script, a restore, a hand-edited database.
    conn = db()
    conn.execute("UPDATE email_templates SET body = ? WHERE template_key = ?",
                 ("Hi {guest_name}, about {not_a_real_tag}. {feedback_url}", KEY))
    conn.commit()
    subject, rendered = m.render_email_template(
        conn, KEY, {"guest_name": "Marie", "room_name": "Blue",
                    "feedback_url": "https://example.invalid/f"})
    conn.close()
    s.check("the braces do not reach the guest", "{not_a_real_tag}" not in (rendered or ""),
            detail=f"{(rendered or '')[:80]!r} — the guest reads it exactly as "
                   "typed and the house looks like it cannot send an email")
    s.check("the shipped wording goes instead", bool(subject and rendered),
            detail="nothing sent at all is also a failure: the guest is simply "
                   "never asked")
    s.check("and the guest's own name is still filled in",
            "Marie" in (rendered or ""),
            detail=f"{(rendered or '')[:80]!r}")
    _restore(KEY)

    s.section("The declared list matches what the senders actually pass")
    # The check the rest of this file rests on. A hand-written list of what the
    # code does goes stale the first time somebody adds a field to a sender, so
    # it is read back out of app.py by a second implementation and compared.
    passed = _senders_from_source()
    drift = []
    for key, declared in sorted(m.EMAIL_TEMPLATE_TAGS.items()):
        actual = passed.get(key)
        if not actual:
            continue          # sender uses a shape this reader cannot see
        missing = sorted(set(declared) - actual)
        extra = sorted(actual - set(declared))
        if missing or extra:
            drift.append((key, {"declared not passed": missing,
                                "passed not declared": extra}))
    s.check("no template's list has drifted", not drift, detail=f"{drift[:3]}")
    s.check("and the reader found senders to compare against",
            sum(1 for k in m.EMAIL_TEMPLATE_TAGS if passed.get(k)) >= 10,
            detail=f"{sum(1 for k in m.EMAIL_TEMPLATE_TAGS if passed.get(k))} of "
                   f"{len(m.EMAIL_TEMPLATE_TAGS)} — a reader that finds nothing "
                   "agrees with everything")

    s.section("Guards")
    s.check("an employee cannot edit the wording guests receive",
            ec.post(f"/management/email-templates/{KEY}/edit",
                    data={"subject": "x", "body": "y"},
                    follow_redirects=False).status_code in (302, 403))
    s.check("a template that does not exist is a 404",
            oc.post("/management/email-templates/not_a_template/edit",
                    data={"subject": "x", "body": "y"},
                    follow_redirects=False).status_code == 404)
    s.check("an unlisted key is not refused on a list that does not cover it",
            m.unknown_merge_tags("not_a_template", "{anything}", "") == [],
            detail="a template this map has not been told about would have its "
                   "wording refused for tags nobody claimed were wrong")

    _restore(KEY)
    return s
