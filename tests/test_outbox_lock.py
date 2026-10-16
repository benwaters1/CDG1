"""Mail that could not be sent has to be kept.

`send_email` falls back to `queue_undelivered`, which — deliberately, and it
says so — opens its own connection so that failing to file a failure cannot
lose the booking that succeeded. The cost of that choice is invisible: SQLite
will not let a second connection write while the first still holds an open
transaction, so any route that writes, then sends, then commits loses the
outbox row. The exception is swallowed into a `print`, the page renders, and
the message is neither sent nor kept.

This has been found and fixed five times: mark_booking_payment_paid,
add_extra, mark_workshop_payment_paid, confirm_booking_by_id, and then
mark_workshop_deposit_paid — which is mark_workshop_payment_paid's manual
twin, sends the identical template, and was missed when its Stripe sibling
was fixed. Two more were found the same day: the decline notice with its
refund line, and the "here is your account" link.

Five recurrences say the per-route fix is not the fix. So the check below
reads app.py rather than exercising routes: any function that writes on a
connection and then reaches send_email without committing first is reported,
including ones nothing calls yet. That is the same approach test_table_overflow
takes to `.table-wrap`, and for the same reason — a convention nothing
enforces gets you fifteen pages that ignore it.

`send_notification` is not caught by this and does not need to be: it takes
the caller's connection and writes on that, so there is no second writer.

AND STILL IT HAPPENED, eleven times in one run on 15 October 2026, because
the source check sees writes and sends in the SAME function. A paid stay that
could not confirm went out under claim_range's lock, taken two calls down in
confirm_booking_by_id; a part payment towards an atelier went out under the
line send_workshop_email writes after its own send. So queue_undelivered no
longer writes into a lock its own request holds: it waits for the write to be
committed and files the letter then. The last three sections drive that, the
second and third through the Stripe webhook with Write to guests off -- a
paid stay and an atelier deposit. The source check stays, for the other
half of the rule -- a write held open across a send is a write held open
across the network, and every other writer in the house waits on it.
"""
import ast
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db
import _harness
from test_stripe_webhook import _post

m = _harness.m
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
TAG = "ZTLOCK"

# `UPDATE\s+\w` followed by \b never matches: the \w takes the first letter of
# the table name and \b then demands a word boundary in the middle of that word.
# The first version of this check had exactly that, so it saw every INSERT and
# no UPDATE — and passed while missing two of the three bugs it was written for.
# Both were UPDATEs. Found by breaking the code and watching nothing go red.
WRITE_SQL = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.I)


def _senders(tree):
    """Every function that reaches send_email, directly or through a wrapper.

    Resolved from the source rather than listed by hand, so a new wrapper is
    covered the day it is written instead of the day somebody remembers.
    """
    calls = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        calls[fn.name] = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
    reaching = {"send_email"}
    changed = True
    while changed:                       # transitive closure over the wrappers
        changed = False
        for name, called in calls.items():
            if name not in reaching and (called & reaching):
                reaching.add(name)
                changed = True
    return reaching


def _offenders():
    """Functions that write on a connection and then send without committing.

    Walks statements in source order at every nesting level. A commit anywhere
    between the write and the send clears it — including one inside the same
    `if` the send sits in, which is how the existing fixes are written.
    """
    src = open(APP, encoding="utf-8").read()
    tree = ast.parse(src)
    senders = _senders(tree) - {"send_email_outbox", "queue_undelivered"}
    lines = src.splitlines()
    found = []

    def call_name(node):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
        return None

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name in ("send_email", "queue_undelivered"):
            continue
        # (lineno, kind) for every write / commit / send in this function.
        events = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if name == "execute":
                text = " ".join(lines[node.lineno - 1:node.end_lineno])
                if WRITE_SQL.search(text):
                    events.append((node.lineno, "write"))
            elif name == "commit":
                events.append((node.lineno, "commit"))
            elif name in senders and not _keeps_nothing(node):
                events.append((node.lineno, "send"))
        events.sort()
        open_write = False
        for lineno, kind in events:
            if kind == "write":
                open_write = True
            elif kind == "commit":
                open_write = False
            elif kind == "send" and open_write:
                found.append((fn.name, lineno, "writes, then sends, then commits"))
                break
        else:
            # Source order is not the whole story. A batch job that sends and
            # then stamps each row reads as send-before-write and looks clean,
            # but the stamp is still uncommitted when the NEXT iteration sends:
            # guest A is stamped, guest B's send fails, and B's mail is neither
            # sent nor kept. That is the shape of most jobs in here, and one was
            # sitting in this blind spot when the check first shipped.
            wrapped = _loop_wraparound(fn, senders, lines)
            if wrapped:
                found.append((fn.name, wrapped, "a loop stamps each row and "
                              "commits after the loop, so the next send is "
                              "blocked by the previous row's write"))
    return found


def _keeps_nothing(call):
    """True when this send is explicitly told not to queue.

    send_email(..., keep=False) never reaches queue_undelivered, so it opens no
    second connection and cannot be blocked by anything. The three waitlist
    notifiers pass it deliberately: with no provider configured, each
    cancellation would otherwise queue another "a room may have opened up"
    notice, and they would all arrive together the day email is switched on for
    dates resold weeks earlier. Making them commit would bring that back, so
    the check has to understand the flag rather than the flag having to change.
    """
    for kw in call.keywords:
        if kw.arg == "keep" and isinstance(kw.value, ast.Constant):
            return kw.value.value is False
    return False


def _loop_wraparound(fn, senders, lines):
    """Line of a send that a previous iteration's uncommitted write would block.

    A loop body that writes without committing before it ends carries that
    write into the next iteration, where it sits open across that iteration's
    send. Order within the body does not matter for this — only whether the
    write is still open when the body comes round again.
    """
    for loop in [n for n in ast.walk(fn) if isinstance(n, (ast.For, ast.While))]:
        events = []
        for stmt in loop.body:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if name == "execute":
                    text = " ".join(lines[node.lineno - 1:node.end_lineno])
                    if WRITE_SQL.search(text):
                        events.append((node.lineno, "write"))
                elif name == "commit":
                    events.append((node.lineno, "commit"))
                elif name in senders and not _keeps_nothing(node):
                    events.append((node.lineno, "send"))
        events.sort()
        kinds = [k for _, k in events]
        if "write" not in kinds or "send" not in kinds:
            continue
        last_write = max(ln for ln, k in events if k == "write")
        # A commit after the last write closes it before the body ends.
        if any(ln > last_write for ln, k in events if k == "commit"):
            continue
        return next(ln for ln, k in events if k == "send")
    return None


def run():
    s = Suite("Held mail survives the write lock")
    oc, _ec, _owner, _emp = clients()

    s.section("No route sends while it still holds a write lock")
    bad = _offenders()
    s.check("nothing writes, then sends, then commits", not bad,
            detail="; ".join(f"{n} (app.py:{ln}) — {why}" for n, ln, why in bad)
                   or "")

    s.section("The check can actually find one")
    # Without this the section above passes just as happily if the walker is
    # broken, which is the failure mode a source-reading test has.
    probe = ast.parse(
        "def r():\n"
        "    conn.execute('UPDATE bookings SET status = 1')\n"
        "    send_email('a', 'b', 'c')\n"
        "    conn.commit()\n")
    fn = probe.body[0]
    seen = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            seen.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", None))
    s.check("the walker sees a write, a send and a commit in one function",
            {"execute", "send_email", "commit"} <= set(seen), detail=str(seen))
    s.check("and send_email's wrappers are resolved rather than listed by hand",
            "send_workshop_email" in _senders(ast.parse(open(APP, encoding="utf-8").read())))

    s.section("The three found today actually hold their mail now")
    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()

    # The account link. This page answers "sent" whether or not the address is
    # known, so a dropped message leaves no trace at either end -- the only
    # place it can be observed is the outbox.
    # The route lowercases the address before storing it, so look it up the
    # same way — comparing against the typed form finds nothing and reads
    # exactly like the dropped-message bug this is here to catch.
    ref = (TAG + "acct@example.invalid").lower()
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Linkwanter', ?, '2026-10-01', '2026-10-03', 2,
           'confirmed', 400, ?)""",
        (room, TAG + "BK", TAG + "tok", ref, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    anon = m.app.test_client()
    anon.post("/my-account", data={"email": ref}, follow_redirects=True)
    conn = db()
    held = conn.execute("SELECT * FROM email_outbox WHERE to_address = ?", (ref,)).fetchall()
    conn.close()
    s.check("the account link is kept rather than dropped", len(held) == 1,
            detail=f"{len(held)} held — a lost one leaves nothing anywhere")
    s.check("and it is the link, not an empty shell",
            held and "/my-account/" in (held[0]["body"] or ""),
            detail=(held[0]["body"] or "")[:60] if held else "")

    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_sessions WHERE email = ?", (ref,))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()

    conn = db()
    room = conn.execute(
        "SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    arrival = m.service_day() + timedelta(days=230)
    departure = arrival + timedelta(days=2)
    _cleanup_letters()
    try:
        _letter_under_an_open_write(s, room, arrival, departure)
        _paid_stay_that_could_not_confirm(s, room, arrival, departure)
        _atelier_deposit_by_card(s)
    finally:
        _switch(True)
        _cleanup_letters()
    return s


def _switch(live):
    """Write to guests on or off. run.py names any suite that leaves it off."""
    conn = db()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('guest_mail_live', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if live else "0",))
    conn.commit()
    conn.close()


def _held(where, *params):
    """What the outbox holds, read on a connection of the suite's own."""
    conn = db()
    rows = conn.execute(f"SELECT * FROM email_outbox WHERE {where} ORDER BY id",
                        params).fetchall()
    conn.close()
    return rows


def _paid_by_card(session):
    """Post one paid checkout to the webhook with Write to guests off.

    Returns the response and the subject of every letter that left while the
    request still held a write: a send under an open write is a send over the
    network with the house's other writers queued behind it, whatever then
    happens to the letter.
    """
    under_a_write = []
    real_send = m.send_email

    def watching(*a, **kw):
        if m.request_holds_a_write():
            under_a_write.append(str(a[1] if len(a) > 1 else kw.get("subject")))
        return real_send(*a, **kw)

    _switch(False)
    m.send_email = watching
    try:
        r = _post(m.app.test_client(), "checkout.session.completed", session)
    finally:
        m.send_email = real_send
        _switch(True)
    return r, under_a_write


def _logged(outbox_id):
    """The mail log's line for one held letter, if it has one."""
    conn = db()
    row = conn.execute("SELECT * FROM mail_log WHERE outbox_id = ?", (outbox_id,)).fetchone()
    conn.close()
    return row


def _cleanup_letters():
    conn = db()
    refs = [r["reference_code"] for r in conn.execute(
        "SELECT reference_code FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))]
    for table in ("email_outbox", "mail_log", "guest_messages"):
        conn.execute(f"DELETE FROM {table} WHERE to_address LIKE ? OR subject LIKE ? "
                     "OR body LIKE ?", (TAG + "%", "%" + TAG + "%", "%" + TAG + "%"))
        for ref in refs:
            conn.execute(f"DELETE FROM {table} WHERE subject LIKE ?", ("%" + ref + "%",))
    for ref in refs:
        conn.execute("DELETE FROM audit_log WHERE target = ?", (ref,))
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG + "%",))
    ateliers = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    for table in ("workshop_transactions", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ateliers}",
                     (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _letter_under_an_open_write(s, room, arrival, departure):
    s.section("A letter sent while the request still holds a write is kept")
    # The shape the source check cannot see: the write is taken in a helper --
    # claim_range, exactly as a refused confirm_booking_by_id leaves it -- and
    # the letter goes before anybody commits. It used to wait five seconds on
    # its own request's lock and then be thrown away.
    kept = (TAG + "kept@example.invalid").lower()
    undone = (TAG + "undone@example.invalid").lower()
    _switch(False)
    with m.app.test_request_context("/"):
        conn = m.get_db()
        m.claim_range(conn, room["id"], arrival, departure)
        m.send_email(kept, TAG + " sent under the booking lock", "Body.")
        conn.commit()
        on_commit = _held("to_address = ?", kept)
        m.claim_range(conn, room["id"], arrival, departure)
        m.send_email(undone, TAG + " sent under a write that is undone", "Body.")
        conn.rollback()
        conn.close()
    _switch(True)
    s.check("it is in the outbox as soon as the write it waited on is committed",
            len(on_commit) == 1,
            detail=f"{len(on_commit)} held — before, it waited out busy_timeout on its "
                   "own request's lock and was dropped with a print line")
    s.check("and kept once, not again as the request ends",
            len(_held("to_address = ?", kept)) == 1)
    s.check("one whose write is rolled back is still kept, by the end of the request",
            len(_held("to_address = ?", undone)) == 1)
    line = _logged(on_commit[0]["id"]) if on_commit else None
    s.check("and the mail log calls it held, against the row it became",
            bool(line) and line["status"] == "held" and line["to_address"] == kept,
            detail=dict(line) if line else "no line points at the held row")


def _paid_stay_that_could_not_confirm(s, room, arrival, departure):
    s.section("A paid stay that could not confirm keeps both its letters")
    # The room went to somebody else while the guest was on the card page, so
    # the webhook records the booking (the money has moved) and the confirm
    # refuses. The two letters that follow -- the guest's "Payment received"
    # and the house's "Needs you" -- went out under claim_range's lock, and
    # with Write to guests off or the provider down, both were lost.
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room["id"], TAG + "RIVAL", TAG + "rivaltok", TAG + " Rival",
         (TAG + "rival@example.invalid").lower(), arrival.isoformat(),
         departure.isoformat(), m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    guest = (TAG + "paid@example.invalid").lower()
    sid = TAG + "-paid-stay"
    session = {
        "id": sid, "payment_status": "paid", "payment_intent": sid + "-pi",
        "customer_email": guest, "amount_total": 40000,
        "metadata": {
            "room_id": str(room["id"]), "guest_name": TAG + " Paid", "guest_email": guest,
            "guest_phone": "", "arrival_date": arrival.isoformat(),
            "departure_date": departure.isoformat(), "party_size": "2",
            "special_requests": "", "extra_ids": "", "promo_code": "",
            "total_price": "400.00", "nights": "2",
        },
    }
    r, under_a_write = _paid_by_card(session)
    conn = db()
    booking = conn.execute("SELECT * FROM bookings WHERE stripe_session_id = ?",
                           (sid,)).fetchone()
    conn.close()
    s.check("the webhook takes the payment", r.status_code == 200, detail=str(r.status_code))
    s.check("and records the stay, waiting on the house because the room went",
            bool(booking) and booking["status"] == "pending",
            detail=dict(booking) if booking else "no booking")
    if not booking:
        return
    to_guest = _held("to_address = ?", guest)
    s.check("the guest's 'Payment received' is held, not lost",
            len(to_guest) == 1 and to_guest[0]["subject"] == f"Payment received — {room['name']}",
            detail=[r["subject"] for r in to_guest] or "nothing held for the guest")
    s.check("held because the house is not writing to guests yet",
            bool(to_guest) and "not writing to guests" in (to_guest[0]["reason"] or ""),
            detail=to_guest[0]["reason"] if to_guest else "")
    line = _logged(to_guest[0]["id"]) if to_guest else None
    s.check("and its record says held, against the row",
            bool(line) and line["status"] == "held",
            detail=dict(line) if line else "no mail log line points at it")
    needs_you = _held("subject = ?",
                      f"Needs you — {room['name']} ({booking['reference_code']})")
    s.check("the house's 'Needs you' is held too", len(needs_you) == 1,
            detail=f"{len(needs_you)} held")
    s.check("and no letter on the way went out while a write was still open",
            not under_a_write, detail="; ".join(under_a_write))


def _atelier_deposit_by_card(s):
    s.section("An atelier deposit paid by card keeps the house's notice")
    # The commonest of the eleven. The guest's receipt goes, send_workshop_email
    # writes its line in the registration's history, and the house's "Payment
    # received from ..." went straight out after it with that line uncommitted.
    conn = db()
    now = m.datetime.now(m.timezone.utc).isoformat()
    workshop = conn.execute(
        """INSERT INTO workshops (title, description, instructor_name, price_per_person,
           default_capacity, active, sort_order, created_at, deposit_percent)
           VALUES (?, 'Test', 'Tutor', 900.0, 8, 1, 99, ?, 30)""",
        (TAG + " Atelier", now)).lastrowid
    start = m.house_today() + timedelta(days=150)
    sitting = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           notes, created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (workshop, start.isoformat(), (start + timedelta(days=4)).isoformat(),
         TAG + " sitting", now)).lastrowid
    guest = (TAG + "atelier@example.invalid").lower()
    registration = conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price, deposit_amount)
           VALUES (?, ?, ?, 2, 'confirmed', ?, ?, ?, 1800, 540)""",
        (sitting, TAG + " Painter", guest, TAG + "WS", TAG + "wstok", now)).lastrowid
    conn.commit()
    conn.close()

    r, under_a_write = _paid_by_card({
        "id": TAG + "-deposit", "payment_status": "paid",
        "payment_intent": TAG + "-deposit-pi", "amount_total": 54000,
        "metadata": {"kind": "workshop_deposit",
                     "workshop_booking_id": str(registration)},
    })
    s.check("the webhook takes the deposit", r.status_code == 200, detail=str(r.status_code))
    s.check("the guest's receipt is held", len(_held("to_address = ?", guest)) == 1,
            detail=[x["subject"] for x in _held("to_address = ?", guest)] or "nothing")
    notice = _held("subject = ?", f"Payment received from {TAG} Painter")
    s.check("and so is the house's 'Payment received from ...'", len(notice) == 1,
            detail=f"{len(notice)} held")
    s.check("and nothing on the way went out while a write was still open",
            not under_a_write, detail="; ".join(under_a_write))


if __name__ == "__main__":
    print(run().report())
