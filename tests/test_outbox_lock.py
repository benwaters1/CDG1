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
"""
import ast
import os
import re

from _harness import Suite, clients, db
import _harness

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
    return s


if __name__ == "__main__":
    print(run().report())
