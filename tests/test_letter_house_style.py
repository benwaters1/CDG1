"""The house's voice in every letter, held where the code writes it.

THE REVIEW. On 23 November 2026 the owner asked for every communication to be
checked for style before more guest mail is switched back on. The stored
letters were already held to the payment rules (test_confirmed_facts,
test_stored_letter_drift) and drawn one way (test_letter_design). What broke
was everything around them -- the words the CODE writes into a letter, and the
letters it writes whole -- because nothing read those as a guest would:

  - ONE GUEST, TWO NAMES. The confirmation said "Your stay is booked, Marie",
    the feedback request "Hi Marie,", and the statement, the refund, the
    decline and every atelier letter "Hi Marie Dubois," -- whichever the code
    that wrote it happened to pass. "Mr & Mrs Martin" was greeted "Hi Mr,".
  - CONTRACTIONS, which the house does not use (5 October): "We'll be in touch
    about your refund" went into the room and dinner refusals, "didn't" into
    the account link and the reset code.
  - BUTTONS THAT DID NOT SAY WHAT THEY DO. The Booking.com guest's one link,
    to check in, was a button reading "Your booking page" -- they have no
    booking with us. The account link and the staff invitation read "Open the
    page". The card that failed asked the guest to pay with a button reading
    "Your registration".
  - A BALANCE "DUE AT CHECK-IN" for an atelier with no due date of its own --
    paid online and in advance, nothing at the door -- and, in the reminder,
    "due by at check-in".
  - THE AUTOCHARGE LETTERS acknowledged and asked for money without the
    sentence about how the house takes payment, and the Guest Emails page
    showed a copy of them typed out again by hand.
  - THE ACCOUNT LINK was held by Guest emails, though it is the way in and
    lapses long before a held letter goes; and it had no greeting and no name
    at the foot.
  - "INQUIRY" in the house's notices, where every public page says enquiry;
    "a spot" where the letters say a place.
  - THE FORM'S WORDS IN A SENTENCE. An event's kind went into its letters as
    the drop-down's value: "hosting a corporate", "towards your other".
  - THE FACTS: the directions said miles where the site's map says "4 km,
    unlit", and left out the A61; the editor's preview showed a room by the
    name it had before 16 October; the Booking.com message the owner pastes
    into the extranet was the one letter not signed.

Each is checked here against the source or the function that writes it, so
the next letter written in code is held to the same rules without anybody
remembering to.
"""
import ast
import os
import re

from _harness import Suite
import _harness

m = _harness.m
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNOFF = "— Château de Gudanes"

# A contraction a letter would use. Possessives ("the château's") are not.
CONTRACTION = re.compile(
    r"\b(?:[A-Za-z]+n[’']t|(?:we|you|they|it|that|there|here|what|let|i|he|she)"
    r"[’'](?:re|ve|ll|d|s|m))\b", re.I)
# The American spelling, as a word: never inside an identifier such as
# event_inquiry_received, where "_" keeps \b from matching.
INQUIRY = re.compile(r"\binquir(?:y|ies|ed|e|ing)\b", re.I)

# What writes into a letter: the senders, and the functions that build the
# words a stored letter's tags are filled with.
SENDERS = {"render_email_template", "email_template_text", "write_about_stay", "send_email"}
_TREE = None


def _tree():
    global _TREE
    if _TREE is None:
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            _TREE = ast.parse(fh.read())
    return _TREE


def _functions():
    return {n.name: n for n in _tree().body if isinstance(n, ast.FunctionDef)}


def _calls(fn, names):
    return any(isinstance(c, ast.Call) and getattr(c.func, "id", None) in names
               for c in ast.walk(fn))


def _letter_writers():
    return {name: fn for name, fn in _functions().items()
            if _calls(fn, SENDERS) or name.endswith(("_email_context", "_payment_context"))}


def _letter_strings(fn):
    """The string constants in this function that reach a letter: the subject
    and body handed to a sender, the context it fills a letter from, and
    anything kept in a name a letter is built from (refund_note, balance_line,
    price_block ...). Docstrings, log lines and flashes are not letters."""
    out = []

    def consts(node):
        for c in ast.walk(node):
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                out.append((c.lineno, c.value))

    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in SENDERS:
            for a in node.args[1:3]:
                consts(a)
            for a in node.args[2:]:
                if isinstance(a, ast.Dict):
                    consts(a)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and re.search(
                        r"(_note|_line|_lines|_block|_how|^subject|^body|^opening|^closing)$", t.id):
                    consts(node.value)
        if isinstance(node, ast.Return) and fn.name.endswith("_context") and node.value is not None:
            consts(node.value)
    return out


def _guest_texts():
    """(where, text) for every letter to a guest whose words the code holds:
    the shipped wording and the letters written in code."""
    out = [(k, s + "\n" + b) for k, _l, s, b in m.DEFAULT_EMAIL_TEMPLATES
           if b.strip() and not k.startswith("house_")]
    out += [(k, s + "\n" + b) for k, (s, b) in m.CODE_LETTERS_TO_GUESTS.items()]
    return out


def run():
    s = Suite("Letter house style")
    _one_name(s)
    _no_contractions(s)
    _buttons(s)
    _paid_online(s)
    _money_letters(s)
    _account_link(s)
    _words_and_facts(s)
    return s


def _one_name(s):
    s.section("One name per guest: the first, greeted the same in every letter")
    g = m.greeting_name
    s.check("a guest is greeted by their first name", g("Marie Dubois") == "Marie",
            detail=g("Marie Dubois"))
    s.check("a hyphenated first name is kept whole, spaces squeezed",
            g("  Anne-Sophie   Leroy ") == "Anne-Sophie", detail=g("  Anne-Sophie   Leroy "))
    s.check("a title is greeted with the whole name, never 'Hi Mr,'",
            g("Mr & Mrs Martin") == "Mr & Mrs Martin" and g("Dr. Jane Smith") == "Dr. Jane Smith"
            and g("Mme Durand") == "Mme Durand")
    s.check("two people in one name are greeted as written",
            g("Jean et Marie Dupont") == "Jean et Marie Dupont")
    s.check("no name, or an address standing in for one, is 'there'",
            g("") == "there" and g(None) == "there" and g("jean@example.com") == "there")

    merged = m.merge_email_text("Hi {guest_name},\n\nBooked under {guest_name}.",
                                {"guest_name": "Marie Dubois"})
    s.check("a stored letter's greeting gets the first name",
            merged.startswith("Hi Marie,"), detail=merged)
    s.check("and the same tag anywhere else is still the whole name",
            "Booked under Marie Dubois." in merged, detail=merged)
    for opener, tag in (("Dear", "contact_name"), ("Hello", "name")):
        got = m.merge_email_text(f"{opener} {{{tag}}},\n\nx", {tag: "Claire Moreau"})
        s.check(f"'{opener} {{{tag}}},' is greeted by the first name too",
                got.startswith(f"{opener} Claire,"), detail=got)
    odd = m.merge_email_text("Hi {guest_name},\n\n{reference_code}",
                             {"guest_name": "Ann {x}", "reference_code": "GUD-1"})
    s.check("a brace in a guest's name cannot break the letter",
            odd.startswith("Hi Ann,") and "GUD-1" in odd, detail=odd)

    # No context dict a letter is filled from builds a first name by hand:
    # that is how five sites said "Hi Mr," and the rest said the whole name.
    adhoc = []
    for name, fn in _letter_writers().items():
        for node in ast.walk(fn):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value in
                        ("guest_name", "first_name", "contact_name", "name", "organiser")):
                    continue
                if any(isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "split"
                       for c in ast.walk(value)):
                    adhoc.append(f"{name}:{key.lineno}")
    s.check("no letter makes a first name with .split() instead of greeting_name()",
            not adhoc, detail=adhoc)


def _no_contractions(s):
    s.section("No contractions, in anything a letter says (5 October)")
    shipped = [f"{k}: {m_.group(0)}" for k, text in _guest_texts()
               for m_ in CONTRACTION.finditer(text)]
    s.check("not in the shipped wording or the letters written in code",
            not shipped, detail=shipped[:6])
    found = []
    for name, fn in _letter_writers().items():
        for line, text in _letter_strings(fn):
            for m_ in CONTRACTION.finditer(text):
                found.append(f"{name} line {line}: {m_.group(0)}")
    s.check("not in any words the code writes into a letter",
            not found, detail=found[:6])
    s.check("the scan reads what it claims to: the refusal's refund note is in it",
            any("We will be in touch about your refund" in t
                for t in (text for _l, text in _letter_strings(_functions()["decline_booking_by_id"]))))


def _buttons(s):
    s.section("Every button says what it does")
    # The pages a letter's buttons open, built from the app's own routes.
    rules = {r.endpoint: r.rule for r in m.app.url_map.iter_rules()}
    wants = {
        "channel_checkin": "Check in online", "stay_checkin": "Check in online",
        "workshop_checkin": "Check in online", "guest_account": "Your bookings",
        "onboard": "Choose your password", "guest_checkin": "Your booking page",
        "manage_booking": "Your booking page", "workshop_manage": "Your registration",
        "booking_statement": "Your statement", "workshop_statement": "Your statement",
        "pay_share": None, "guest_portal": None, "guest_feedback": None,
        # The come-back letters (a year on, an anniversary, a birthday) and the
        # two waiting lists end on these, in the site's own words for them.
        "book_rooms": "Check dates", "restaurant_book": "Reserve a table",
    }
    unnamed, wrong = [], []
    for endpoint, want in wants.items():
        rule = rules.get(endpoint)
        if not rule:
            unnamed.append(f"{endpoint}: no such page")
            continue
        href = "https://www.chateaugudanes.com" + re.sub(r"<[^>]+>", "t0k3n", rule)
        label = m._letter_label_for_link(href)
        if not label:
            unnamed.append(f"{endpoint} ({rule})")
        elif want and label != want:
            wrong.append(f"{endpoint}: {label!r}, not {want!r}")
    s.check("every page a letter links to has a button label of its own",
            not unnamed, detail=unnamed)
    s.check("and the label says what the page is for", not wrong, detail=wrong)
    site = "https://www.chateaugudanes.com"
    whole = {site + "/book?arrival=2027-05-01": "Check dates", site + "/book/": "Check dates",
             site + "/book/pay/t0k3n": "Pay now", site + "/booking/t0k3n/finish": "Finish and pay",
             site + "/restaurant/book?date=2027-05-01": "Reserve a table"}
    off = [f"{h}: {m._letter_label_for_link(h)!r}" for h, want in whole.items()
           if m._letter_label_for_link(h) != want]
    s.check("/book is matched whole: the pages under it keep their own labels", not off, detail=off)

    blank = []
    with m.app.test_request_context("/"):
        for key, _label, subject, body in m.DEFAULT_EMAIL_TEMPLATES:
            if not body.strip() or key == "room_confirmed":   # its own design
                continue
            ctx = m.drawn_context(m.sample_merge_context(key), workshops=True)
            blocks = m.dress_blocks(m.letter_blocks(m.merge_email_text(body, ctx)))
            blank += [f"{key}: {b['href']}" for b in blocks
                      if b["kind"] == "button" and b["label"] == "Open the page"]
        for key, (_s, body) in m.CODE_LETTERS_TO_GUESTS.items():
            text = body.replace("{manage_link}", "https://www.chateaugudanes.com"
                                + rules["workshop_manage"].replace("<manage_token>", "t"))
            blocks = m.dress_blocks(m.letter_blocks(text))
            blank += [f"{key}: {b['href']}" for b in blocks
                      if b["kind"] == "button" and b["label"] == "Open the page"]
    s.check("no letter has a button reading 'Open the page'", not blank, detail=blank[:5])

    with m.app.test_request_context("/"):
        failed = m.AUTOCHARGE_FAILED_LETTER[1].format(
            guest_name="Marie", amount="€500.00", workshop="Noël", reference="GUD-1",
            manage_link="https://www.chateaugudanes.com/workshops/manage/t")
        labels = [b["label"] for b in m.dress_blocks(m.letter_blocks(failed))
                  if b["kind"] == "button"]
    s.check("a card that failed asks for the money with a button that pays it",
            labels == ["Pay the balance"], detail=labels)


def _paid_online(s):
    s.section("An atelier is paid online and in advance, never at check-in")
    row = {"guest_name": "Marie Dubois", "title": "Noël at Gudanes",
           "start_date": "2026-12-18", "end_date": "2026-12-22", "party_size": 1,
           "reference_code": "GUD-ZZLH", "total_price": 3200.0, "deposit_amount": 960.0,
           "balance_amount": 2240.0, "balance_due_date": None, "manage_token": "zzlh",
           "excursions_included": 0, "transfers_included": 0}

    class Row(dict):
        def keys(self):
            return list(super().keys())

    with m.app.test_request_context("/"):
        ctx = m.workshop_email_context(Row(row))
    said = " | ".join(str(ctx.get(k, "")) for k in ("balance_line", "price_block", "balance_due_date"))
    s.check("with no due date of its own, nothing says check-in or arrival",
            not re.search(r"check[- ]?in|arrival|at the door", said, re.I), detail=said)
    s.check("the balance falls due by the day the atelier starts",
            ctx["balance_due_date"] == m.format_date_human("2026-12-18")
            and "due by " + m.format_date_human("2026-12-18") in ctx["balance_line"],
            detail=said)
    reminder = next(b for k, _l, _s, b in m.DEFAULT_EMAIL_TEMPLATES
                    if k == "workshop_balance_reminder")
    s.check("and the reminder reads as English: 'due by <a date>'",
            "due by at" not in m.merge_email_text(reminder, ctx), detail=ctx["balance_due_date"])


def _money_letters(s):
    s.section("The autocharge letters: one copy of the words, and how the house takes payment")
    for key in ("code:send_autocharge_taken_email", "code:send_autocharge_failed_email"):
        _subj, body = m.CODE_LETTERS_TO_GUESTS[key]
        s.check(f"{key[5:]} says how the house takes payment",
                m.PAYMENT_SAFETY_LINE in body)
        s.check(f"{key[5:]} writes its money as money() does, with no second euro sign",
                "€{" not in body)
    fns = _functions()
    for name, const in (("send_autocharge_taken_email", "AUTOCHARGE_TAKEN_LETTER"),
                        ("send_autocharge_failed_email", "AUTOCHARGE_FAILED_LETTER")):
        uses = {n.id for n in ast.walk(fns[name]) if isinstance(n, ast.Name)}
        s.check(f"{name} sends the words Guest emails shows, not a copy",
                const in uses and m.CODE_LETTERS_TO_GUESTS["code:" + name]
                is getattr(m, const))


def _account_link(s):
    s.section("The link to a guest's bookings is a key, and a letter")
    fn = _functions()["guest_account_request"]
    sends = [c for c in ast.walk(fn)
             if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "send_email"]
    s.check("it is sent from one place", len(sends) == 1, detail=len(sends))
    if not sends:
        return
    call = sends[0]
    s.check("it goes while Guest emails is off, and says why",
            any(k.arg == "despite_switch" and isinstance(k.value, ast.Constant)
                and k.value.value for k in call.keywords))
    words = "".join(c.value for c in ast.walk(call.args[2])
                    if isinstance(c, ast.Constant) and isinstance(c.value, str))
    s.check("it opens with a greeting", words.startswith("Hello,"), detail=words[:40])
    s.check("its link sits under the words its button says",
            "Your bookings:\n" in words, detail=words[:120])
    s.check("and it is signed as every letter is", words.rstrip().endswith(SIGNOFF),
            detail=words[-60:])


def _words_and_facts(s):
    s.section("The house's words, and the facts the site states")
    for key, (_s, body) in m.CODE_LETTERS_TO_GUESTS.items():
        s.check(f"{key[5:]} is signed as every letter is", body.rstrip().endswith(SIGNOFF),
                detail=body[-50:])
        first = body.split("\n", 1)[0]
        s.check(f"{key[5:]} opens 'Hi', as the house's letters do",
                first.startswith("Hi {"), detail=first)

    found = []
    for name, fn in _letter_writers().items():
        for line, text in _letter_strings(fn):
            if INQUIRY.search(text):
                found.append(f"{name} line {line}: {text[:60]!r}")
    s.check("'enquiry', as every public page spells it, in the words the code writes",
            not found, detail=found[:5])
    spot = [f"{name} line {line}" for name, fn in _letter_writers().items()
            for line, text in _letter_strings(fn) if re.search(r"\ba spot\b", text)]
    s.check("'a place', never 'a spot', on an atelier or a table", not spot, detail=spot)

    s.section("An event is called what it is, not the value off the form")
    w = m.event_type_words
    s.check("the form's 'corporate' and 'other' are said as nouns",
            w("corporate") == "corporate event" and w("other") == "event",
            detail=(w("corporate"), w("other")))
    s.check("a kind that is a noun already is said as written, in lower case",
            w("Wedding") == "wedding" and w("elopement") == "elopement")
    s.check("and no kind at all is 'event'", w(None) == "event" and w("  ") == "event")
    raw = []
    for name, fn in _letter_writers().items():
        for node in ast.walk(fn):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "event_type"
                        and not (isinstance(value, ast.Call)
                                 and getattr(value.func, "id", None) == "event_type_words")):
                    raw.append(f"{name}:{key.lineno}")
    s.check("every event letter is filled through event_type_words()", not raw, detail=raw)
    with m.app.test_request_context("/"):
        ctx = m.event_email_context({"contact_name": "Claire Moreau", "event_type": "corporate",
                                     "reference_code": "EVT-ZZLH", "quoted_price": None,
                                     "manage_token": "zzlh"})
    s.check("so a client reads 'your corporate event', never 'your corporate'",
            ctx["event_type"] == "corporate event", detail=ctx["event_type"])

    s.section("The directions, the rooms and the signatures")
    with open(os.path.join(ROOT, "templates", "_email.html"), encoding="utf-8") as fh:
        shell = fh.read()
    s.check("the directions are in kilometres, as the site's map says '4 km, unlit'",
            not re.search(r"\bmiles?\b", shell) and "kilometres" in shell)
    s.check("and name the A61, as the route map does",
            "the A61, the A66 and the N20" in shell)

    # Rooms by the names they have now. Every name a room has had and lost --
    # the ones before 16 October and the ones before those -- read from the
    # app's own rename tables, less the names rooms carry today.
    current = set(m.HOME_ROOM_ORDER)
    retired = ({old for old, _new, *_ in m.ROOM_NAMES_2026_10_16}
               | {new for _old, new, *_ in m.ROOM_IDENTITY}) - current
    samples = " ".join(str(v) for v in m.SAMPLE_MERGE_VALUES.values())
    stale = sorted(n for n in retired if n in samples
                   or any(n in text for _k, text in _guest_texts()))
    s.check("no letter, and no preview of one, names a room by a name it no longer has",
            not stale, detail=stale)

    with open(os.path.join(ROOT, "templates", "channel_stays.html"), encoding="utf-8") as fh:
        page = fh.read()
    pasted = page.split('class="share-link-box"', 1)[-1].split("</p>", 1)[0]
    s.check("the message the owner pastes into Booking.com is signed",
            pasted.rstrip().endswith(SIGNOFF), detail=pasted[-80:])

    with open(os.path.join(ROOT, "templates", "email_booking_confirmed.html"),
              encoding="utf-8") as fh:
        confirmed = fh.read()
    s.check("the confirmation does not ask a guest to keep a link it says they need not keep",
            "Keep this link" not in confirmed)
