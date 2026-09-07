"""Twenty-one letters drawn, from one set of words.

Every message this house sends was plain text, so the manage link arrived as
a bare URL a guest had to copy out. The room confirmation got an HTML version
of its own, and the handover asked for four more written the same way —
workshop confirmed, workshop received, restaurant confirmed, the pre-arrival
note.

Written that way it would have been four second copies of the same sentences,
and then seventeen more. THE WORDS ARE DATA: all twenty-one live in
email_templates and the owner edits them under Management, Email templates. An
HTML version of each is a second copy of words somebody is going to change —
and the first time they did, a guest would get the new wording in plain text
and the old wording drawn nicely, with nothing anywhere reporting it.

So there is one set of words, and the shell is wrapped around whatever it is
handed. Four things carry it:

  ONE FUNCTION, THIRTEEN CALLERS. render_email_template now returns the drawn
  version alongside the text. Widening the return is the point: a caller
  nobody updated is a ValueError on the next run, not a letter that quietly
  went out plainer than the others. Adding it at thirteen sites by hand is
  adding it at the twelve somebody remembers.

  A LINE THAT IS ONLY A LINK BECOMES A BUTTON, labelled with the words the
  letter already used to introduce it. That is the finding EMAILS.md opened
  with, fixed for all twenty-one at once.

  A SENTENCE WITH A LINK IN THE MIDDLE STAYS A SENTENCE. Turning that into a
  button loses the sentence, and the sentence is what the owner wrote.

  AND THE TEXT IS STILL WHAT WAS SENT. The drawn version is an addition. It
  is what a mail client shows; the text is what a plain-text client shows,
  what this house keeps as its record of what it said, and what goes out on
  its own if the shell will not render.
"""
import io
import os
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m


def run():
    s = Suite("Twenty-one letters, drawn")
    oc, _ec, _owner, _emp = clients()

    s.section("The owner's text, taken apart")
    text = ("Hi Eleanor,\n\n"
            "Your registration for Plaster & Lime is confirmed.\n\n"
            "Manage your registration:\n"
            "https://example.invalid/workshops/manage/abc123\n\n"
            "-- Chateau de Gudanes")
    blocks = m.letter_blocks(text)
    kinds = [b["kind"] for b in blocks]
    s.check("the paragraphs survive", kinds.count("text") >= 3,
            detail=str(kinds))
    s.check("and the bare link became a button", "button" in kinds,
            detail="a manage link arriving as a URL a guest has to copy out "
                   "is the whole complaint: " + str(kinds))
    button = [b for b in blocks if b["kind"] == "button"][0]
    s.check("pointing where the text pointed",
            button["href"] == "https://example.invalid/workshops/manage/abc123")
    # THE LABEL IS THE OWNER'S OWN WORDING. The letters all introduce their
    # link the same way, so the words are there and do not need inventing.
    s.check("labelled with the words the letter already used",
            button["label"] == "Manage your registration",
            detail=repr(button["label"]))

    s.section("A sentence with a link in it stays a sentence")
    # Turning this into a button loses the sentence, and the sentence is what
    # the owner wrote.
    mid = m.letter_blocks("Pay the balance at https://example.invalid/pay/9 "
                          "before you arrive.")
    s.check("it is not turned into a button",
            [b["kind"] for b in mid] == ["text"], detail=str(mid))
    s.check("and the address is still readable in it",
            "https://example.invalid/pay/9" in mid[0]["text"])

    s.section("Nothing in, nothing out")
    s.check("an empty body draws nothing", m.letter_blocks("") == [])
    with m.app.test_request_context("/"):
        s.check("and letter_html says so rather than an empty shell",
                m.letter_html("Subject", "") == "",
                detail="a letterhead with no letter in it is worse than the "
                       "plain text on its own")

    s.section("Drawn for a mail client, not a browser")
    with m.app.test_request_context("/"):
        html = m.letter_html("Registration confirmed", text)
    s.check("it renders", len(html) > 1500, detail=str(len(html)))
    s.check("as a whole document", html.lstrip().lower().startswith("<!doctype"))
    # Outlook draws with Word's engine and has neither. This is the rule that
    # cannot be seen from here and only shows up in somebody's inbox.
    s.check("with no flexbox and no grid",
            "display:flex" not in html.replace(" ", "")
            and "display:grid" not in html.replace(" ", ""))
    s.check("and no stylesheet for Gmail to strip",
            "<style" not in html.lower())
    s.check("and no web font to fail to load",
            "fonts.googleapis" not in html and "@font-face" not in html)
    s.check("the button is a real link", 'href="https://example.invalid' in html)

    s.section("A guest called <script> is a guest")
    with m.app.test_request_context("/"):
        nasty = m.letter_html("Hello", "Hi <script>alert(1)</script>,\n\nSee you.")
    s.check("the markup in a name is escaped",
            "<script>" not in nasty and "&lt;script&gt;" in nasty,
            detail="these bodies carry guest names, and the name is whatever "
                   "somebody typed into a booking form")

    s.section("Every templated letter carries it, not the ones somebody remembered")
    source = open(os.path.join(_harness.ROOT, "app.py"),
                  encoding="utf-8").read().replace("\r\n", "\n")
    lines = source.split("\n")
    sites, missed = 0, []
    for i, line in enumerate(lines):
        if "= render_email_template(" not in line:
            continue
        sites += 1
        window = "\n".join(lines[i:i + 20])
        if "send_email(" in window and "html=letter" not in window:
            for j in range(i, -1, -1):
                if lines[j].startswith("def "):
                    missed.append(lines[j][4:].split("(")[0])
                    break
    s.check("there are letters to check", sites >= 13, detail=str(sites))
    s.check("and every one of them sends the drawn version too", not missed,
            detail="a letter that arrives drawn for the confirmation and "
                   "plain for the cancellation is the fault this house keeps "
                   "finding: " + str(missed))
    # THE REASON THE RETURN WAS WIDENED. A caller nobody updated has to break
    # loudly rather than quietly send a plainer letter.
    s.check("and the one function hands back three things",
            "return subject, body, letter_html(subject, body)" in source,
            detail="two would let a forgotten caller go on working, silently "
                   "plainer than the rest")

    s.section("And there is still only one set of words")
    # The whole reason for doing it this way. An HTML template per letter is a
    # second copy of sentences the owner edits.
    tpl = os.path.join(_harness.ROOT, "templates")
    per_letter = [f for f in os.listdir(tpl)
                  if f.startswith("email_") and f.endswith(".html")
                  and f not in ("email_letter.html",
                                "email_booking_confirmed.html")]
    s.check("no letter has an HTML copy of its own words", not per_letter,
            detail="email_letter.html draws whatever it is handed; a file per "
                   "letter is a second copy that drifts: " + str(per_letter))
    letter_tpl = open(os.path.join(tpl, "email_letter.html"),
                      encoding="utf-8").read()
    s.check("and the shell holds no wording of its own",
            "Hi " not in letter_tpl and "confirmed" not in letter_tpl.lower(),
            detail="a sentence in here is a sentence the owner cannot edit")

    s.section("The text is still what the house said")
    sent = []
    was_email = m.send_email
    m.send_email = lambda to, subj, body, **k: (
        sent.append((to, subj, body, k.get("html") or "")), True)[1]
    conn = db()
    try:
        with m.app.test_request_context("/"):
            subject, body, drawn = m.render_email_template(
                conn, "restaurant_confirmed",
                {"guest_name": "Eleanor", "dinner_date": "2026-10-01",
                 "party_size": 2, "reference_code": "ZZLD1"})
    finally:
        conn.close()
        m.send_email = was_email
    s.check("the text comes back as it always did",
            bool(subject) and bool(body) and "<table" not in body,
            detail=repr(body[:60]))
    s.check("with the drawn version beside it",
            drawn.lstrip().lower().startswith("<!doctype"),
            detail="an addition, never a replacement")
    s.check("and both are the same letter",
            "Eleanor" in body and "Eleanor" in drawn,
            detail="one set of words is the entire point")
    return s
