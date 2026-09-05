# -*- coding: utf-8 -*-
"""Every page rendered, then read the way a browser and a screen reader do.

Source sweeps miss what a template only assembles at render time — a label
whose `for` is built from a loop variable, a control whose text comes out of
a macro, an id duplicated because the same partial was included twice. So
this walks the real pages and reads the HTML that came out of them.

Four things it found, none of which errored and none of which any test saw:

  THE SEARCH BOX AT THE TOP OF EVERY PAGE HAD NO NAME. It lives in base.html,
  so it was on all 237 of them, announced as "edit text" and nothing more. A
  placeholder is not a label: it is gone the moment somebody types.

  THE TICK HAD NOTHING IN IT. The control staff use most — the morning list,
  the breakfast sheet, the shopping list, the onboarding checklist — renders
  a tick when done and the EMPTY STRING when not. So the unticked ones, the
  ones somebody is about to press, were a button with no name at all.

  SIX LINKS OPENED A NEW TAB AND HANDED IT window.opener, which lets the
  opened page navigate the one that opened it. All six point somewhere
  internal today, which is exactly why nobody would notice the day one does
  not.

  AND NOTHING WAS DUPLICATED, UNLABELLED-BY-IMAGE, OR OUTSIDE table-wrap —
  checked here too, on the output, because the source rules that enforce
  those cannot see a table built inside a macro.

A field WRAPPED in its own label counts as labelled: <label><input> Gluten
</label> is the implicit form and is perfectly accessible. Counting those as
faults buried the real ones — 665 of the first sweep's findings were checkbox
grids using exactly that shape.

A field inside a table cell takes its meaning from the column header above
it and the subject at the start of the line — and a screen reader reads
neither for the input, announcing "edit text, 3". Thirty of those were found
and all thirty are named, so it is a rule here rather than a ceiling, and it
is checked on the SOURCE: the rendered count moved with the database, because
a table only draws an editable row when it has something to edit.

What remains is the row editors built from divs rather than tables — roughly
eleven hundred fields across ninety pages, each taking its meaning from a
heading nothing reads out. Not a bug, not silent, and a piece of work with a
size rather than a number pinned in a constant that would drift the same way
the page count did.
"""
import collections
import glob
import io
import os
import re

from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m

TAG = re.compile(r"<(\w+)([^>]*)>", re.S)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
SCRIPTY = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)

# A field inside a <td> takes its meaning from the column header above it and
# the subject at the start of the line, and a screen reader reads neither for
# the input — it announces "edit text, 3". Thirty of these were found and all
# thirty are named, so this is a rule rather than a ceiling.
#
# Checked on the SOURCE, not the render. The rendered count moved with the
# database — 84 pages alone, 90 after the other suites had left their
# fixtures behind, because a table only draws an editable row when it has
# something to edit — and a bound that drifts for reasons nobody controls
# gets raised until it means nothing.
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
FIELD = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S | re.I)

# GET rules whose arguments are not ids of anything this can look
# up — a filename, a slug, a month. A ceiling, so a new page
# behind an unknown argument is noticed rather than skipped.
#
# It did that: uploaded_file was added for the photograph intake and takes a
# stored filename, which is the same shape as room_photo and mirrored_photo
# beside it. Six now, and the six are export_report_csv, mirrored_photo,
# room_ics_feed, room_photo, set_language and uploaded_file.
NO_RULE = 6

# What the sweep fetched and could not read.
#
# NAMED RATHER THAN COUNTED, and that change was forced. It was a ceiling of
# seven -- reasonable, and the reasoning above it was right -- but the number
# turned out to depend on the ORDER the suites run in: six of these are file
# views that answer 404 only while the run's scratch upload directory is still
# empty, so a suite that happens to upload something first takes them off the
# list. Adding two suites earlier in the run moved it from seven to thirteen
# with nothing broken, and a bound that moves when unrelated work lands is a
# bound that gets raised without being read.
#
# Every one of these is unreadable for a reason that is stated, and a
# fourteenth -- or a different one -- reds the run and says which.
UNREADABLE = {
    # A Stripe return needs a real session id, and nothing here calls Stripe:
    # _harness pins the key off and asserts it. These can never answer in a test.
    "booking_stripe_success": "no Stripe session",
    "event_stripe_success": "no Stripe session",
    "restaurant_stripe_success": "no Stripe session",
    "stripe_success": "no Stripe session",
    "share_payment_success": "no Stripe session",
    "workshop_stripe_success": "no Stripe session",
    # A file view answers 404 until something has been uploaded, and the run
    # gets a fresh upload directory on purpose so suites cannot inherit each
    # other's leftovers.
    "download_company_document": "no file in the run's uploads",
    "download_document": "no file in the run's uploads",
    "download_expense_file": "no file in the run's uploads",
    "view_company_document": "no file in the run's uploads",
    "view_document": "no file in the run's uploads",
    "view_expense_file": "no file in the run's uploads",
    # Answers 400 without an id, which is the correct answer to no id.
    "data_request_export": "needs an id",
}

# The pages somebody who does not work here has to get through on their own.
GUEST_FACING = [
    ("guest_portal", "a guest's own portal is read"),
    ("guest_account", "and the account page they are let into"),
    ("guest_feedback", "the form asking how the stay was"),
    ("pay_share", "the page one of a party pays their share on"),
    ("event_quote", "the quote a client opens"),
    ("workshop_feedback", "what somebody says about an atelier"),
    ("instructor_page", "the sheet whoever is teaching is sent"),
    ("supplier_invoice_submit", "a supplier's own upload form"),
    ("onboard", "and a new colleague's first screen"),
    ("newsletter_confirm", "confirming a newsletter"),
    ("newsletter_unsubscribe", "and getting out of one"),
    ("campaign_unsubscribe", "and out of the campaign email as well"),
    # The other three kinds of booking have their own confirmation and manage
    # screens, and every one of them was being fetched with a ROOM booking's
    # manage token, answering 404, and dropped.
    ("restaurant_confirmation", "the confirmation for a table"),
    ("restaurant_manage", "and the page they change it on"),
    ("workshop_confirmation", "the confirmation for an atelier place"),
    ("workshop_manage", "and the page they change that on"),
    ("event_confirmation", "the confirmation for an event"),
    ("event_manage", "the page the client runs it from"),
    # DELIBERATELY NOT the three pay pages — event_pay, workshop_pay_deposit
    # and workshop_pay_balance. With Stripe pinned off, which _harness.py
    # enforces at import and which must never stop being true here, they
    # correctly redirect to the manage page rather than offering a button
    # that cannot work. They are unread because of the harness, and the only
    # way to change that would be to let a test run touch real payments.
]


def _attrs(raw):
    return dict(ATTR.findall(raw))


def _read(page, html, found):
    body = SCRIPTY.sub(" ", html)

    ids = re.findall(r'\bid="([^"]+)"', body)
    for i, n in collections.Counter(ids).items():
        if n > 1:
            found["duplicate id"].append("%s: %s" % (page, i))

    labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', body))
    wrapped = set()
    for lab in re.finditer(r"<label\b[^>]*>(.*?)</label>", body, re.S | re.I):
        for tag, raw in TAG.findall(lab.group(1)):
            if tag.lower() in ("input", "select", "textarea"):
                wrapped.add(raw)

    for tag, raw in TAG.findall(body):
        a = _attrs(raw)
        low = tag.lower()
        if low in ("input", "select", "textarea"):
            if a.get("type") in ("hidden", "submit", "button", "reset", "image"):
                continue
            if not (a.get("id") in labelled or a.get("aria-label")
                    or a.get("aria-labelledby") or a.get("title")
                    or raw in wrapped):
                found["field with no label"].append(
                    "%s: %s %s" % (page, low, a.get("name", "?")))
        if low == "img" and "alt" not in a:
            found["image with no alt"].append("%s: %s" % (page, a.get("src", "?")))
        if a.get("target") == "_blank" and "noopener" not in a.get("rel", ""):
            found["_blank with no noopener"].append(
                "%s: %s" % (page, a.get("href", "?")))

    for match in re.finditer(r"<(button|a)\b([^>]*)>(.*?)</\1>", body, re.S | re.I):
        tag, raw, inner = match.group(1), match.group(2), match.group(3)
        a = _attrs(raw)
        if " ".join(re.sub(r"<[^>]+>", "", inner).split()):
            continue
        if a.get("aria-label") or a.get("title") or a.get("aria-labelledby"):
            continue
        if tag.lower() == "a" and not a.get("href"):
            continue
        found["control with no name"].append(
            "%s: <%s class=%s>" % (page, tag, a.get("class", "")))

    for match in re.finditer(r"<table\b", body):
        if "table-wrap" not in body[max(0, match.start() - 400):match.start()]:
            found["table outside table-wrap"].append(page)
            break


# What each url argument is an id OF. Without this the sweep reads only the
# 237 pages that take no arguments — a quarter of the app — and the negative
# control proved it: the window.opener hole was put back into the till
# receipt link and went unnoticed, because /pos/order/<id> was never asked
# for.
ID_TABLES = {
    "booking_id": ("bookings", "id"),
    "user_id": ("users", "id"),
    "order_id": ("pos_orders", "id"),
    "room_id": ("rooms", "id"),
    "guest_id": ("guests", "id"),
    "event_id": ("event_inquiries", "id"),
    "inquiry_id": ("event_inquiries", "id"),
    "expense_id": ("expenses", "id"),
    "workshop_id": ("workshops", "id"),
    "session_id": ("workshop_sessions", "id"),
    "menu_id": ("menus", "id"),
    "doc_id": ("documents", "id"),
    "asset_id": ("assets", "id"),
    "absence_id": ("absences", "id"),
    "voucher_id": ("gift_vouchers", "id"),
    "visit_id": ("maintenance_visits", "id"),
    "template_id": ("email_templates", "id"),
    "code_id": ("promo_codes", "id"),
    "manage_token": ("bookings", "manage_token"),
    "share_token": ("bookings", "share_token"),
    # A plain string is a literal rather than a (table, column) to read one
    # from — for an argument that is not an id of anything. The history page
    # takes the SORT of record before the record itself, and there is no
    # table to read the word "booking" out of.
    "kind": "booking",
    "record_id": ("bookings", "id"),
    "party_id": ("booking_parties", "id"),
    "vehicle_id": ("vehicles", "id"),
}

# THE GUEST-FACING SITE, which ID_TABLES cannot reach on its own. Ten routes
# take an argument called `token` and every one of them reads a different
# table -- so one rule per NAME could never serve them, and the twelve pages
# an outsider actually opens were the twelve this sweep never read. Their
# portal, their bill, the feedback form, the quote, the share to pay, the
# teaching sheet: none of it.
#
# A sweep that reads every staff page and no guest page is the green-over-half
# failure this house has a rule about, and it is worse here than most because
# these are the pages people who do not work here have to get through.
ENDPOINT_ARGS = {
    ("campaign_unsubscribe", "token"): ("campaign_sends", "unsubscribe_token"),
    ("event_quote", "token"): ("event_quotes", "token"),
    ("guest_account", "token"): ("guest_sessions", "token"),
    ("guest_feedback", "token"): ("bookings", "manage_token"),
    ("guest_portal", "token"): ("guests", "portal_token"),
    ("instructor_page", "token"): ("workshop_sessions", "instructor_token"),
    ("newsletter_confirm", "token"): ("newsletter_subscribers", "token"),
    ("newsletter_unsubscribe", "token"): ("newsletter_subscribers", "token"),
    ("onboard", "token"): ("users", "invite_token"),
    ("pay_share", "token"): ("booking_shares", "token"),
    ("supplier_invoice_submit", "token"): ("vendors", "upload_token"),
    ("workshop_feedback", "token"): ("workshop_bookings", "manage_token"),
    ("chat_channel", "slug"): ("chat_channels", "slug"),
    ("event_confirmation", "manage_token"): ("event_inquiries", "manage_token"),
    ("event_manage", "manage_token"): ("event_inquiries", "manage_token"),
    ("event_pay", "manage_token"): ("event_inquiries", "manage_token"),
    ("event_stripe_success", "manage_token"): ("event_inquiries", "manage_token"),
    ("restaurant_confirmation", "manage_token"): ("restaurant_bookings", "manage_token"),
    ("restaurant_manage", "manage_token"): ("restaurant_bookings", "manage_token"),
    ("workshop_confirmation", "manage_token"): ("workshop_bookings", "manage_token"),
    ("workshop_manage", "manage_token"): ("workshop_bookings", "manage_token"),
    ("workshop_pay_balance", "manage_token"): ("workshop_bookings", "manage_token"),
    ("workshop_pay_deposit", "manage_token"): ("workshop_bookings", "manage_token"),
    ("guest_booking_history", "email"): ("bookings", "guest_email"),
    # Reports are keyed by a slug that is a key of REPORT_BUILDERS rather than
    # a row anywhere, so it is a literal.
    ("admin_report", "slug"): "financial",
    ("pay_statement_page", "year"): "2026",
    ("pay_statement_page", "month"): "1",
}

# Deliberately NOT taught: room_photo, mirrored_photo, room_ics_feed,
# export_report_csv and set_language. The first four serve a file and the last
# redirects, so teaching the sweep to reach them would raise the count of
# pages "read" without reading a byte of markup -- which is the opposite of
# what this file is for.


def _one(conn, table, column):
    """A real value for this argument, or None if there is nothing to read."""
    try:
        row = conn.execute(
            "SELECT %s AS v FROM %s WHERE %s IS NOT NULL ORDER BY id DESC "
            "LIMIT 1" % (column, table, column)).fetchone()
    except Exception:
        return None
    return row["v"] if row else None


def _reachable(conn):
    """Every GET page, with a real id filled in wherever one is known.

    Returns (rules, unreachable) so the count of what could NOT be read is
    reported rather than silently shrinking the sweep.
    """
    rules, unreachable, no_rule = [], [], []
    for r in m.app.url_map.iter_rules():
        if "GET" not in (r.methods or set()):
            continue
        rule = str(r.rule)
        if rule.startswith("/static") or r.endpoint in ("logout", "login"):
            continue
        if not r.arguments:
            rules.append((r.endpoint, rule))
            continue
        values, missing_rule = {}, False
        for arg in r.arguments:
            known = ENDPOINT_ARGS.get((r.endpoint, arg), ID_TABLES.get(arg))
            if known is None:
                missing_rule = True
                break
            value = known if isinstance(known, str) else _one(conn, *known)
            if value is None:
                break
            values[arg] = value
        if missing_rule:
            no_rule.append(r.endpoint)
        if len(values) != len(r.arguments):
            unreachable.append(r.endpoint)
            continue
        try:
            with m.app.test_request_context("/"):
                rules.append((r.endpoint, m.url_for(r.endpoint, **values)))
        except Exception:
            unreachable.append(r.endpoint)
    rules.sort()
    return rules, sorted(set(unreachable)), sorted(set(no_rule))


# Not TAG: that is the tag-matching regex at the top of this file.
SEEDED = "ZZRM"


def _clear(conn):
    for sql in (
        "DELETE FROM booking_shares WHERE token LIKE ?",
        "DELETE FROM bookings WHERE guest_name LIKE ?",
        "DELETE FROM guests WHERE name LIKE ?",
        "DELETE FROM guest_sessions WHERE token LIKE ?",
        "DELETE FROM newsletter_subscribers WHERE token LIKE ?",
        "DELETE FROM campaign_sends WHERE unsubscribe_token LIKE ?",
        "DELETE FROM chat_channels WHERE slug LIKE ?",
        "DELETE FROM workshop_bookings WHERE manage_token LIKE ?",
        "DELETE FROM restaurant_bookings WHERE manage_token LIKE ?",
        "DELETE FROM event_quotes WHERE token LIKE ?",
        "DELETE FROM event_inquiries WHERE reference_code LIKE ?",
        "DELETE FROM vendors WHERE upload_token LIKE ?",
        "DELETE FROM users WHERE invite_token LIKE ?",
        "UPDATE workshop_sessions SET instructor_token = NULL "
        "WHERE instructor_token LIKE ?",
    ):
        try:
            conn.execute(sql, (SEEDED + "%",))
        except Exception:
            pass
    conn.commit()


def _seed(conn):
    """One row wherever a guest-facing page needs a token to exist.

    WITHOUT THIS THE SWEEP READS WHATEVER THE OTHER SUITES LEFT BEHIND. Every
    one of these tables is empty in a fresh copy of the database, so the pages
    an outsider actually opens were reachable in principle and unread in fact
    -- which is the same "it depends what is in the database" the bound on
    this file already had to be rescued from.

    Deliberately minimal, and taken away again at the end: this suite reads
    pages, it does not own the database.
    """
    now = m.datetime.now(m.timezone.utc).isoformat()
    _clear(conn)
    room = conn.execute("SELECT * FROM rooms ORDER BY id").fetchone()
    if room:
        arrival = m.house_today() + timedelta(days=280)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', ?)""",
            (room["id"], SEEDED + "REF", SEEDED + "manage", SEEDED + " Guest",
             "zzrm.guest@example.invalid", arrival.isoformat(),
             (arrival + timedelta(days=2)).isoformat(), now))
        conn.commit()
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (SEEDED + "REF",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO booking_shares (booking_id, name, email, amount,
                       token, status, created_at)
               VALUES (?, ?, ?, 100.0, ?, 'open', ?)""",
            (bid, SEEDED + " Sharer", "zzrm.share@example.invalid",
             SEEDED + "share", now))
    conn.execute(
        "INSERT INTO guests (name, email, portal_token, created_at) VALUES (?, ?, ?, ?)",
        (SEEDED + " Guest", "zzrm.guest@example.invalid", SEEDED + "portal", now))
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        ("zzrm.guest@example.invalid", SEEDED + "session", now,
         (m.datetime.now(m.timezone.utc) + timedelta(hours=6)).isoformat()))
    conn.execute(
        """INSERT INTO newsletter_subscribers (email, token, created_at)
           VALUES (?, ?, ?)""",
        ("zzrm.news@example.invalid", SEEDED + "news", now))
    conn.execute(
        """INSERT INTO campaign_sends (recipient_email, unsubscribe_token, created_at)
           VALUES (?, ?, ?)""",
        ("zzrm.campaign@example.invalid", SEEDED + "unsub", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token,
                   guest_name, guest_email, party_size, dinner_date, status,
                   created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (SEEDED + "RREF", SEEDED + "rmanage", SEEDED + " Guest",
         "zzrm.guest@example.invalid",
         (m.house_today() + timedelta(days=281)).isoformat(), now))
    conn.execute(
        "INSERT INTO chat_channels (slug, name, created_at) VALUES (?, ?, ?)",
        (SEEDED + "-channel", SEEDED + " Channel", now))
    # A supplier with a live upload link, a newcomer mid-invitation, and an
    # event quote a client would open. All three are pages somebody OUTSIDE
    # the house is sent and has to get through on their own.
    conn.execute(
        """INSERT INTO vendors (name, upload_token, active, created_at)
           VALUES (?, ?, 1, ?)""",
        (SEEDED + " Supplier", SEEDED + "upload", now))
    # account_claimed = 0 because onboard is the page somebody uses BEFORE
    # they have an account, and it refuses anybody who has already claimed one.
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, invite_token,
                   account_claimed, status, created_at)
           VALUES (?, 'x', 'employee', ?, ?, 0, 'active', ?)""",
        ("zzrm.new@example.invalid", SEEDED + " Newcomer", SEEDED + "invite", now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                   contact_name, contact_email, status, created_at)
           VALUES (?, ?, 'wedding', ?, ?, 'new', ?)""",
        (SEEDED + "EREF", SEEDED + "emanage", SEEDED + " Client",
         "zzrm.client@example.invalid", now))
    conn.commit()
    event_row = conn.execute(
        "SELECT id FROM event_inquiries WHERE reference_code = ?",
        (SEEDED + "EREF",)).fetchone()
    if event_row:
        conn.execute(
            """INSERT INTO event_quotes (event_id, token, quoted_price, created_at)
               VALUES (?, ?, 4000.0, ?)""",
            (event_row["id"], SEEDED + "quote", now))
    session_row = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id").fetchone()
    if session_row:
        # The teaching sheet: a read-only link sent to whoever is running the
        # atelier, and the one page in this set that shows somebody's guests.
        conn.execute(
            "UPDATE workshop_sessions SET instructor_token = ? WHERE id = ?",
            (SEEDED + "teach", session_row["id"]))
    if session_row:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code,
                       manage_token, guest_name, guest_email, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'confirmed', ?)""",
            (session_row["id"], SEEDED + "WREF", SEEDED + "wmanage", SEEDED + " Guest",
             "zzrm.guest@example.invalid", now))
    conn.commit()


# What a page that was not read is, and whether anybody should do anything
# about it. A redirect is a redirect and a CSV is a CSV; neither is markup and
# neither is a gap. An HTML page that did not answer 200 is the only one worth
# a bound.
def _why_not(resp):
    if resp.status_code in (301, 302, 303, 307, 308):
        return "redirect"
    if "html" not in resp.headers.get("Content-Type", ""):
        return "not a page"
    return "did not answer"


def run():
    s = Suite("Rendered markup, read as a browser reads it")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _seed(conn)
    rules, unreachable, no_rule = _reachable(conn)
    conn.close()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = collections.defaultdict(list)
    pages = 0
    skipped = collections.defaultdict(list)
    read_endpoints = set()
    for endpoint, path in rules:
        try:
            resp = oc.get(path)
        except Exception:
            skipped["did not answer"].append("%s (threw)" % endpoint)
            continue
        if resp.status_code != 200 or "html" not in resp.headers.get(
                "Content-Type", ""):
            skipped[_why_not(resp)].append(
                "%s %s" % (endpoint, resp.status_code))
            continue
        pages += 1
        read_endpoints.add(endpoint)
        _read(endpoint, resp.get_data(as_text=True), found)

    s.check("there are pages to read", pages > 250, detail="%d read" % pages)
    # Named rather than quietly skipped: a sweep that reads fewer pages
    # than it appears to is the failure this whole suite exists to stop.
    s.check("and the ones nobody taught it to reach are named",
            len(no_rule) <= NO_RULE,
            detail="%d: %s — add the argument to ID_TABLES if it is an "
                   "id of something" % (len(no_rule), no_rule[:6]))
    # THIS number does not move with the database. It is the count of GET
    # pages with an argument ID_TABLES has no rule for — a token, a slug, a
    # filename — and it changes only when somebody adds a route or closes a
    # gap. A ceiling on it is a bound worth having.
    #
    # The wider `unreachable` list is reported rather than checked, because
    # most of it is tables that happen to be empty in this copy: 53 of them
    # here, none of which is anything to fix. Bounding that was bounding
    # whatever the other 320 suites had left behind, and a bound like that
    # gets raised until it means nothing.
    s.check("an argument that is a word rather than an id still resolves",
            any(e == "record_history_page" for e, _p in rules),
            detail="the history page takes the SORT of record before the "
                   "record; if the literal stops resolving it drops off this "
                   "sweep and nothing else notices")
    s.check("and it says how much of the site it could not read",
            len(unreachable) >= len(no_rule),
            detail="%d page(s) not read: %d have no rule for an argument, "
                   "the rest have a rule and an empty table"
                   % (len(unreachable), len(no_rule)))

    # AND THE ONES IT FETCHED AND DID NOT READ. Until now a page that answered
    # 404 was dropped as silently as one it could not build a URL for: the
    # sweep said "247 read" either way, and 247 of what was never stated. A
    # redirect is a redirect and a CSV is not markup; neither is a gap. An
    # HTML page that did not answer is the only one worth a bound.
    did_not = sorted(skipped["did not answer"])
    # Compared by name. A page that starts answering still comes off for
    # free -- it simply stops appearing -- and one that stops answering is
    # named rather than absorbed into a number that was already too big.
    unexpected = sorted(e.rsplit(" ", 1)[0] for e in did_not
                        if e.rsplit(" ", 1)[0] not in UNREADABLE)
    s.check("and the pages it fetched but could not read are named too",
            not unexpected,
            detail="not on the list, and each needs a reason or a fix: %s"
                   % unexpected[:6])
    s.check("with the redirects and the downloads told apart from them",
            skipped["redirect"] and skipped["not a page"],
            detail="%d redirect(s), %d not-a-page — counting either as a gap "
                   "is how a bound stops meaning anything"
                   % (len(skipped["redirect"]), len(skipped["not a page"])))

    # THE GUEST-FACING PAGES, by name. The reason this suite grew fixtures:
    # every one of these lives behind a token, every token table is empty in a
    # fresh copy, and so the pages an outsider actually has to get through
    # were the pages that were never read. Named individually rather than
    # counted, because "16 guest pages read" goes on being true while any one
    # of them quietly stops answering.
    for endpoint, what in GUEST_FACING:
        s.check(what, endpoint in read_endpoints,
                detail="%s was not read: %s" % (
                    endpoint,
                    "no rule for its argument" if endpoint in no_rule
                    else "nothing in its table" if endpoint in unreachable
                    else "it answered, but not with a page"))

    for kind in ("control with no name", "_blank with no noopener",
                 "duplicate id", "image with no alt",
                 "table outside table-wrap"):
        rows = found[kind]
        s.check("no %s" % kind, not rows,
                detail="%d — e.g. %s" % (len(rows), rows[:3]))

    # And the source rule, which does not move with the database.
    unlabelled = []
    for path in sorted(glob.glob(os.path.join(root, "templates", "*.html"))):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        for cell in CELL.findall(src):
            # A field wrapped in its own label is labelled.
            stripped = re.sub(r"<label\b.*?</label>", " ", cell,
                              flags=re.S | re.I)
            for tag, raw in FIELD.findall(stripped):
                if re.search(r'type\s*=\s*"(hidden|submit|button|reset|image)"',
                             raw, re.I):
                    continue
                if re.search(r"aria-label\s*=|aria-labelledby\s*=|\btitle\s*=",
                             raw, re.I):
                    continue
                unlabelled.append("%s: <%s%s>"
                                  % (os.path.basename(path), tag, raw[:40]))
    s.check("no field sits unnamed in a table cell",
            not unlabelled,
            detail="%d — the column header names it for a person looking at "
                   "the screen and for nobody else: %s"
                   % (len(unlabelled), unlabelled[:3]))

    # And the chrome, which is on EVERY page. The command palette at the top
    # of base.html had a placeholder and no name, so all 259 pages carried a
    # box announced as "edit text" — a placeholder is not a label, it is gone
    # the moment somebody types. A fault here is 259 faults, so the shared
    # files are held to zero rather than counted.
    chrome = []
    for name in ("base.html", "public_base.html", "_list_toolbar.html",
                 "_period_selector.html"):
        path = os.path.join(root, "templates", name)
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8", errors="replace").read()
        # Jinja comments first — base.html explains in one why the language
        # picker is links "rather than a <select>", and the word alone read
        # as an unnamed field.
        src = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)
        # A label pointing AT a field names it even though it does not wrap
        # it, which is how the public date pickers are written.
        named_by_for = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', src))
        wrapped_src = re.sub(r"<label\b.*?</label>", " ", src, flags=re.S | re.I)
        for tag, raw in FIELD.findall(wrapped_src):
            if re.search(r'type\s*=\s*"(hidden|submit|button|reset|image)"',
                         raw, re.I):
                continue
            if re.search(r"aria-label\s*=|aria-labelledby\s*=|\btitle\s*=",
                         raw, re.I):
                continue
            own_id = re.search(r'\bid="([^"]+)"', raw)
            if own_id and own_id.group(1) in named_by_for:
                continue
            chrome.append("%s: <%s%s>" % (name, tag, " ".join(raw.split())[:50]))
    s.check("and nothing in the chrome every page carries is unnamed",
            not chrome, detail="%d: %s" % (len(chrome), chrome[:3]))

    conn = db()
    _clear(conn)
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE guest_name LIKE ?",
        (SEEDED + "%",)).fetchone()["c"]
    conn.close()
    s.check("and the pages it built itself are taken away again",
            left == 0,
            detail="every suite after this one reads the same tables, and a "
                   "seeded stay left behind is a night somebody counts")

    # What is left, said plainly rather than pinned at a number that would
    # drift the same way the page count did: the row editors built from divs
    # rather than tables. Roughly eleven hundred fields across ninety pages,
    # each taking its meaning from a heading a screen reader does not read
    # for it. Not a bug and not silent — a piece of work with a size.
    return s
