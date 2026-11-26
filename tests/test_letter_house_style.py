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

THE STORED LETTERS (25 November 2026). The owner said yes to all nine of the
review's changes to the letters' own wording, which ship as data: enquiry, a
place, sorry and a way on when a room is refused, "Hi", "Reference code:", the
payment sentence in every stay or atelier letter about money, the
confirmation's room count, times and signature, and the three 2027 retreats
campaigns. They go in through one table, read twice -- for the shipped wording
and, once, for the letters stored on the live site -- and the swap is checked
here on the wording the live site held that day, word for word. Check-out had
moved to 11am two days before, and the owner's own line in the confirmation
still said 10am.
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
    _words_of_25_november(s)
    _retreat(s)
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
            w("corporate") == "private retreat" and w("other") == "event",  # the form's words since 25 Nov
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
    s.check("so a client reads 'your private retreat', never 'your corporate'",
            ctx["event_type"] == "private retreat", detail=ctx["event_type"])

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


# What the live site held on 25 November 2026, read from its editors that
# morning, word for word. The confirmation is the owner's own edit of it; the
# others were as shipped.
LIVE_CONFIRMED = (
    "Your stay is booked, {first_name}\n\nFive guest rooms are open, and one of them is yours "
    "for the dates below. Everything here is also on your booking page, so there is nothing you "
    "need to keep.\n\n{stay_details}\n\nCheck-in is from 3pm, and check-out is by 10am.\n\n"
    "The walls of the house stay as the centuries left them; the floors and the bathrooms are "
    "new, and the rooms are furnished from the brocantes. If anything at all needs saying before "
    "you arrive, reply to this message; it reaches us.\n\nPayment for this booking is only ever "
    "taken by card, on chateaugudanes.com. If anyone asks you to pay another way, please call us "
    "first on +33 6 28 06 97 76.")
LIVE_AS_SHIPPED = {
    "room_reserved": (
        "Your room is reserved — {room_name}",
        "Hello {guest_name},\n\nAs agreed, we are holding {room_name} for you.\n\n{stay_lines}\n\n"
        "To secure it, add your details and pay here — it takes a couple of minutes:\n"
        "{finish_url}\n\nWe hold the room until {held_until}; after that it goes back on sale."
        "\n\nReference: {reference_code}\n\n— Château de Gudanes"),
    "event_quote": (
        "Your quote for Château de Gudanes",
        "Dear {contact_name},\n\nYour quote for {event_type} at Château de Gudanes comes to "
        "{quote_total}.\n\nEverything it covers and the terms are on the quote, with a button to "
        "accept it.\nSee the quote:\n{quote_url}\n\nNothing is agreed until you accept it, and we "
        "are glad to talk any of it through first.\n\n— Château de Gudanes"),
    "workshop_statement": (
        "Your statement — {workshop_title}",
        "Hi {guest_name},\n\nYour statement for {workshop_title} ({dates}) is below.\n\n"
        "{balance_line}\n\nIt is always online, with a way to settle anything outstanding.\n"
        "Your statement:\n{statement_url}\n\nReference code: {reference_code}\n\n{company_block}"
        "\n\n— Château de Gudanes"),
    "event_inquiry_received": (
        "Event inquiry received — Château de Gudanes",
        "Hi {contact_name},\n\nThank you for your interest in hosting a {event_type} at Château de "
        "Gudanes. Your inquiry has been received and we will be in touch shortly to discuss "
        "availability and pricing.\n\nReference code: {reference_code}\nYour inquiry:\n"
        "{manage_url}\n\n— Château de Gudanes"),
}
LIVE_CAMPAIGN = (
    "Dear {{first_name}},\n\nThe 2027 retreats at Château de Gudanes are now open for booking."
    "\n\nThere is news from the château too. We are now open for overnight stays, so the house no "
    "longer depends on the retreats alone, and that has let us bring the price of the retreats "
    "down significantly.\n\nYou will find the dates, programmes and new prices on our website: "
    "https://www.chateaugudanes.com\n\nAnd if you would simply like to come and stay for a few "
    "nights in the Pyrenees, you can now book a room directly.\n\nWe would love to welcome you to "
    "the château.\n\nChâteau de Gudanes")
CAMPAIGN_AFTER = LIVE_CAMPAIGN.replace(
    "on our website: https://www.chateaugudanes.com\n",
    "on our website.\nSee the 2027 retreats:\nhttps://www.chateaugudanes.com/workshops\n",
).replace("the château.\n\nChâteau de Gudanes", "the château.\n\n— Château de Gudanes")
# The campaign editor stores what the browser posts, so on the live site the
# campaigns' line breaks are "\r\n". The first swap was tested on "\n" alone,
# passed, and on the live site changed every letter and none of the three.
def crlf(text):
    return text.replace("\n", "\r\n")


FLAG = "letter_words_2026_11_25"
CAMPAIGN_FLAG = "campaign_words_2026_11_25"


def _guest_letters():
    """The shipped letters a guest reads: not the house's own notices."""
    return [(k, sj, bd) for k, label, sj, bd in m.DEFAULT_EMAIL_TEMPLATES
            if bd.strip() and not label.startswith("House:")]


def _words_of_25_november(s):
    s.section("The stored letters' words of 25 November, as shipped")
    shipped = {k: (sj, bd) for k, _l, sj, bd in m.DEFAULT_EMAIL_TEMPLATES}
    letters = _guest_letters()
    said = [k for k, sj, bd in letters if INQUIRY.search(sj + "\n" + bd)]
    s.check("1. no letter says 'inquiry'", not said, detail=said)
    s.check("2. a client reads 'your quote for your wedding'",
            "Your quote for your {event_type} at" in shipped["event_quote"][1])
    said = [k for k, sj, bd in letters if re.search(r"\ba spot\b", sj + "\n" + bd, re.I)]
    s.check("3. no letter says 'a spot'", not said, detail=said)
    declined, cancelled = shipped["room_declined"], shipped["room_cancelled"]
    s.check("4. a room refused says sorry, and how to go on",
            "We are sorry" in declined[1] and "try other dates" in declined[1]
            and "declined" not in declined[0], detail=declined)
    s.check("   and a stay cancelled by the house says how to talk about it",
            "reply to this message" in cancelled[1])
    greetings = [(k, bd.split("\n", 1)[0]) for k, sj, bd in letters
                 if re.match(r"(?:Hello|Dear)\b", bd)]
    s.check("5. every letter that greets a guest opens 'Hi'", not greetings, detail=greetings)
    said = [k for k, sj, bd in letters if "Reference: {" in bd]
    s.check("6. 'Reference code:' in every letter, never 'Reference:'", not said, detail=said)
    missing = sorted(set(m.PAYMENT_LINE_LETTERS_2026_11_25) - m.LETTERS_WITH_PAYMENT_LINE)
    s.check("7. the six letters about money are held to the payment sentence", not missing,
            detail=missing)
    without = sorted(k for k in m.LETTERS_WITH_PAYMENT_LINE
                     if m.PAYMENT_SAFETY_LINE not in shipped.get(k, ("", ""))[1])
    s.check("   and every one held to it carries it", not without, detail=without)
    events = sorted(k for k in m.LETTERS_WITH_PAYMENT_LINE if m.template_area(k) == "Events")
    s.check("   and none of them is an event's, which is paid by bank transfer", not events,
            detail=events)
    body = shipped["room_confirmed"][1]
    s.check("8. the confirmation counts its rooms from the rooms on sale",
            "{Room_count_word} guest rooms are open" in body and "Five guest" not in body)
    s.check("   gives the times the house keeps, from the one place they are kept",
            m.CHECK_TIMES_LINE in body and m.CHECK_OUT_WORDS in m.CHECK_TIMES_LINE
            and m.CHECK_OUT_WORDS == "11am", detail=m.CHECK_TIMES_LINE)
    s.check("   and is signed, as every other letter is", body.rstrip().endswith(SIGNOFF))
    with m.app.test_request_context("/"):
        ctx = m.drawn_context(m.sample_merge_context("room_confirmed"))
        blocks = m.dress_blocks(m.letter_blocks(m.merge_email_text(body, ctx)))
    signed = [b for b in blocks if b["kind"] == "signoff"]
    s.check("   once: the design does not sign it a second time", len(signed) == 1,
            detail=[b["kind"] for b in blocks])

    s.section("The same words put into the letters the live site holds, once")
    conn = _harness.db()
    keys = ["room_confirmed", "room_declined", "room_payment_received", *LIVE_AS_SHIPPED]
    marks = ",".join("?" * len(keys))
    restore = _harness.keep_rows(conn, "email_templates", f"template_key IN ({marks})", keys)
    restore_flag = _harness.keep_rows(conn, "app_settings", "key IN (?, ?)", (FLAG, CAMPAIGN_FLAG))
    tag = "ZZLH25 "
    try:
        conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (tag + "%",))

        def put(k, sj, bd):
            conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = ?",
                         (sj, bd, k))

        put("room_confirmed", "Booking confirmed — {room_name}", LIVE_CONFIRMED)
        for k, (sj, bd) in LIVE_AS_SHIPPED.items():
            put(k, sj, bd)
        # Two the owner has written their own way: the sentences they hold
        # word for word change, and nothing else does.
        owners_declined = ("Hi {guest_name},\n\nI am afraid {room_name} is taken on those "
                           "nights. {refund_note}\n\n— Château de Gudanes")
        put("room_declined", "Booking request declined — {room_name}", owners_declined)
        put("room_payment_received", "Payment received — {room_name}",
            "Hi {guest_name},\n\nThank you, {amount} has arrived.")
        now = _harness.datetime_now()
        another = crlf("Hello,\n\nA note.\n\nChâteau de Gudanes")
        for name, text in ((tag + "2027 retreats", crlf(LIVE_CAMPAIGN)), (tag + "another", another)):
            conn.execute("INSERT INTO campaign_templates (name, area, subject, body, created_at) "
                         "VALUES (?, 'workshops', 'News', ?, ?)", (name, text, now))
        conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)", (FLAG, CAMPAIGN_FLAG))
        conn.commit()

        changed = m.apply_letter_words_2026_11_25(conn)
        untouched = conn.execute("SELECT body FROM campaign_templates WHERE name = ?",
                                 (tag + "2027 retreats",)).fetchone()[0] == crlf(LIVE_CAMPAIGN)
        # As on the live site: the letters' flag is set before the campaigns
        # are put right, and that must not stop them.
        changed_campaigns = m.apply_campaign_words_2026_11_25(conn)

        def row(k):
            return tuple(conn.execute("SELECT subject, body FROM email_templates "
                                      "WHERE template_key = ?", (k,)).fetchone())

        def camp(n):
            return conn.execute("SELECT body FROM campaign_templates WHERE name = ?",
                                (tag + n,)).fetchone()[0]

        s.check("the owner's confirmation, as the live site held it, comes out as shipped",
                row("room_confirmed") == shipped["room_confirmed"],
                detail=row("room_confirmed")[1][-260:])
        s.check("   so its check-out is 11am, not the 10am it said",
                "by 10am" not in row("room_confirmed")[1])
        off = [k for k in LIVE_AS_SHIPPED if row(k) != shipped[k]]
        s.check("each letter stored as shipped comes out as shipped now", not off,
                detail=[(k, row(k)) for k in off][:1])
        s.check("a letter the owner wrote their own way keeps their words",
                row("room_declined")[1] == owners_declined)
        s.check("   and takes the sentence it held word for word, its subject",
                row("room_declined")[0] == "Booking request update — {room_name}")
        s.check("one with no signature to go above gets no payment sentence pushed in",
                m.PAYMENT_SAFETY_LINE not in row("room_payment_received")[1])
        drift = {d["key"]: [b["rule"] for b in d["breaches"]] for d in m.stored_letter_drift(conn)}
        s.check("   and is named instead, where the owner looks",
                "payment_line" in drift.get("room_payment_received", []), detail=drift)
        s.check("   while every letter it put right is named nowhere",
                not set(drift) & ({"room_confirmed"} | set(LIVE_AS_SHIPPED)), detail=drift)
        s.check("the letters' swap leaves the campaigns to their own", untouched)
        s.check("the retreats campaign, stored with \\r\\n as the live site stores it, gets its "
                "button to the retreats and its signature",
                camp("2027 retreats") == CAMPAIGN_AFTER, detail=repr(camp("2027 retreats")[-220:]))
        s.check("   even with the letters' flag already set, as it is on the live site",
                changed_campaigns == ["campaign: " + tag + "2027 retreats"], detail=changed_campaigns)
        s.check("   and a campaign without that sentence is left exactly as it was",
                camp("another") == another)
        s.check("what changed is said by name",
                {"room_confirmed", "room_declined"} <= set(changed)
                and "room_payment_received" not in changed, detail=changed)
        blocks = m.dress_blocks(m.letter_blocks(camp("2027 retreats")))
        buttons = [(b.get("label"), b.get("href")) for b in blocks if b["kind"] == "button"]
        s.check("   the campaign draws one button, to the retreats page, saying what it is",
                buttons == [("See the 2027 retreats", "https://www.chateaugudanes.com/workshops")],
                detail=buttons)
        s.check("   and ends on its signature", blocks[-1]["kind"] == "signoff",
                detail=[b["kind"] for b in blocks])

        put("room_confirmed", "Booking confirmed — {room_name}", LIVE_CONFIRMED)
        conn.execute("UPDATE campaign_templates SET body = ? WHERE name = ?",
                     (crlf(LIVE_CAMPAIGN), tag + "2027 retreats"))
        conn.commit()
        s.check("once: a second start changes nothing",
                m.apply_letter_words_2026_11_25(conn) == []
                and row("room_confirmed")[1] == LIVE_CONFIRMED)
        s.check("   and nor does the campaigns' second start",
                m.apply_campaign_words_2026_11_25(conn) == []
                and camp("2027 retreats") == crlf(LIVE_CAMPAIGN))
        conn.execute("UPDATE campaign_templates SET body = ? WHERE name = ?",
                     (CAMPAIGN_AFTER, tag + "2027 retreats"))
        conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)", (FLAG, CAMPAIGN_FLAG))
        conn.commit()
        m.apply_letter_words_2026_11_25(conn)
        m.apply_campaign_words_2026_11_25(conn)
        again = [k for k in LIVE_AS_SHIPPED if row(k) != shipped[k]]
        s.check("and run again over its own work, it changes nothing twice",
                not again and camp("2027 retreats") == CAMPAIGN_AFTER, detail=again)
    finally:
        conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (tag + "%",))
        conn.commit()
        restore()
        restore_flag()
        conn.close()


# "workshop" or "atelier" as a word a guest reads: not a tag ({workshop_title}),
# not an address (/workshops), not an identifier (workshop_manage).
GUEST_WORKSHOP = re.compile(r"(?<![/\w{.\-])(?:workshops?|ateliers?)(?![\w}/(\-])", re.I)
# The newsletter welcome as shipped until 25 November, and as the live site held it.
WELCOME_UNTIL_25NOV = (
    "Thank you for subscribing to news from Château de Gudanes.\n\nWe will write when there is "
    "something to tell: the restoration as it goes, the house as it opens, and new dates for the "
    "workshops.\n\n{workshops_block}\n\nAll the workshops:\n{workshops_url}\n\n— Château de Gudanes")
RETREAT_FLAG = "letter_retreat_2026_11_25"


def _retreat(s):
    s.section("'Retreat' in the letters (the owner's answer, 25 November)")
    said = [(k, GUEST_WORKSHOP.findall(sj + "\n" + bd)) for k, sj, bd in _guest_letters()
            if GUEST_WORKSHOP.search(sj + "\n" + bd)]
    s.check("no letter a guest reads says workshop or atelier", not said, detail=said)
    said = [k for k, (sj, bd) in m.CODE_LETTERS_TO_GUESTS.items()
            if GUEST_WORKSHOP.search(sj + "\n" + bd)]
    s.check("   nor any the code writes whole", not said, detail=said)
    # Written in room_confirmation_context, which sends nothing itself, into
    # its list of details (details += [...]).
    portal = [c.value for c in ast.walk(_functions()["room_confirmation_context"])
              if isinstance(c, ast.Constant) and isinstance(c.value, str)
              and "Everything you have with us" in c.value]
    s.check("   nor the confirmation's line about the guest's own page",
            portal and not any(GUEST_WORKSHOP.search(t) for t in portal), detail=portal)
    seen = []
    for name in ("_email.html", "email_booking_confirmed.html", "_stay_bits.html"):
        with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as fh:
            words = re.sub(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}|<[^>]*>", " ", fh.read(), flags=re.S)
        seen += [f"{name}: {w}" for w in GUEST_WORKSHOP.findall(words)]
    s.check("   nor the designs the letters are drawn in", not seen, detail=seen)
    welcome = {k: b for k, _l, _s, b in m.DEFAULT_EMAIL_TEMPLATES}["newsletter_welcome"]
    s.check("the tags and the address keep their names: only the words changed",
            "{workshops_block}" in welcome and "{workshops_url}" in welcome)
    with m.app.test_request_context("/"):
        blocks = m.dress_blocks(m.letter_blocks(m.merge_email_text(
            welcome, m.drawn_context(m.sample_merge_context("newsletter_welcome")))))
    labels = [b.get("label") for b in blocks if b["kind"] == "button"]
    s.check("the welcome's button reads 'All the retreats'", "All the retreats" in labels,
            detail=labels)

    # The guest's own pages, which the confirmation points to ("on your
    # page"): the words they show and the words they hand to t() to translate.
    seen = []
    for name in ("guest_portal.html", "guest_portal_statement.html"):
        with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as fh:
            src = re.sub(r"\{#.*?#\}", " ", fh.read(), flags=re.S)
        said = re.findall(r"""\bt\(\s*['"]([^'"]+)['"]""", src)
        said.append(re.sub(r"\{\{.*?\}\}|\{%.*?%\}|<[^>]*>", " ", src, flags=re.S))
        seen += [f"{name}: {w}" for text in said for w in GUEST_WORKSHOP.findall(text)]
    s.check("the guest's own page and statement say retreat too", not seen, detail=seen)
    s.check("   and so does each line of their statement, wherever it is read",
            m.STATEMENT_KINDS["workshop"] == "Retreat", detail=m.STATEMENT_KINDS)
    import translations
    gaps = [f"{lang}: {k}" for lang, table in (("fr", translations.FR), ("es", translations.ES))
            for k in ("Your retreats", "Retreat", "The retreats", "Pay for this retreat")
            if k not in table]
    s.check("   in French and Spanish as well, not falling back to English", not gaps, detail=gaps)

    conn = _harness.db()
    restore = _harness.keep_rows(conn, "email_templates", "template_key = ?",
                                 ("newsletter_welcome",))
    restore_flag = _harness.keep_rows(conn, "app_settings", "key = ?", (RETREAT_FLAG,))
    try:
        def put(body):
            conn.execute("UPDATE email_templates SET body = ? WHERE template_key = "
                         "'newsletter_welcome'", (body,))
            conn.execute("DELETE FROM app_settings WHERE key = ?", (RETREAT_FLAG,))
            conn.commit()

        def stored():
            return conn.execute("SELECT body FROM email_templates WHERE template_key = "
                                "'newsletter_welcome'").fetchone()[0]

        put(WELCOME_UNTIL_25NOV)
        changed = m.apply_letter_retreat_2026_11_25(conn)
        s.check("the welcome as the live site held it comes out as shipped now",
                stored() == welcome and changed == ["newsletter_welcome"],
                detail=(changed, stored()[-120:]))
        put(crlf(WELCOME_UNTIL_25NOV))
        m.apply_letter_retreat_2026_11_25(conn)
        s.check("   and so does one stored with \\r\\n, as a form posts it", stored() == welcome,
                detail=repr(stored()[-120:]))
        own = crlf("Thank you for subscribing.\n\nNew workshops soon.\n\n— Château de Gudanes")
        put(own)
        s.check("one the owner wrote their own way is left exactly as it was",
                m.apply_letter_retreat_2026_11_25(conn) == [] and stored() == own)
        conn.execute("UPDATE email_templates SET body = ? WHERE template_key = "
                     "'newsletter_welcome'", (WELCOME_UNTIL_25NOV,))
        conn.commit()
        s.check("once: a second start changes nothing",
                m.apply_letter_retreat_2026_11_25(conn) == [] and stored() == WELCOME_UNTIL_25NOV)
    finally:
        restore()
        restore_flag()
        conn.close()
