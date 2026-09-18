"""The assistant, and the gate between saying and doing.

Every other Claude feature in this app is tested only with the key absent,
because each of them EXTRACTS something and the fallback is the path that
must never fail. This one is different: it can approve somebody's expense
claim and decline somebody's time off, and the code that decides whether
that happens is the code under test. Testing only the disabled path would
leave the entire safety model unexercised.

So this stands in a fake client — never the real one; `_harness` refuses
`anthropic.Anthropic` at import and that stays true, this only replaces it for
the length of a check and puts it back — and drives the real loop with real
responses of the shape the Messages API returns.

What is actually being asserted, in one line: A MODEL RESPONSE CAN NEVER
CHANGE ANYTHING. A read tool runs on the spot; an action tool is written down
and waits for a person. Both halves are checked, and so is every way round the
gate I could think of — a stale proposal, a second press, somebody else's
proposal, and a tool name the model made up.
"""
import json

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPA"


# --- the fake, shaped like the Messages API ------------------------------

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Response:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return _Response([_Block(type="text", text="Nothing further.")])
        return self.script.pop(0)


class _FakeClient:
    def __init__(self, script):
        self.messages = _FakeMessages(script)


def _install(script):
    """Stand in a fake client and a key, and hand back what to restore."""
    before = (m.anthropic.Anthropic, m.ANTHROPIC_API_KEY)
    m.anthropic.Anthropic = lambda *_a, **_k: _FakeClient(script)
    m.ANTHROPIC_API_KEY = "test-not-a-real-key"
    return before


def _restore(before):
    m.anthropic.Anthropic, m.ANTHROPIC_API_KEY = before


def _cleanup(conn, owner_id):
    conn.execute("DELETE FROM assistant_messages WHERE user_id = ?", (owner_id,))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The assistant")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn, owner["id"])

    # ---- the door ------------------------------------------------------
    s.section("Who can open it")
    s.check("the owner can", oc.get("/assistant").status_code == 200)
    # Not merely @owner_required: that grants any preset covering the area.
    # This reads staff expense claims, so it is checked on role as well.
    s.check("an employee cannot", ec.get("/assistant").status_code == 403,
            detail="it shows staff expense claims and time-off requests")
    s.check("and neither can a stranger",
            m.app.test_client().get("/assistant").status_code in (302, 401, 403))

    # ---- a question, answered by looking --------------------------------
    s.section("A question it answers by looking things up")
    before = _install([
        _Response([_Block(type="tool_use", id="t1", name="get_today", input={})]),
        _Response([_Block(type="text", text="Two arrivals and nothing overdue.")]),
    ])
    try:
        ok, err = m.assistant_turn(conn, owner, "what is on today?")
    finally:
        _restore(before)
    s.check("the turn succeeds", ok, detail=str(err))
    rows = m.assistant_history(conn, owner["id"])
    kinds = [(r["role"], r["tool_name"], r["action_status"]) for r in rows]
    s.check("it read something", ("tool", "get_today", None) in kinds,
            detail=str(kinds))
    s.check("and then answered",
            any(r["role"] == "assistant" and r["content"] == "Two arrivals and nothing overdue."
                for r in rows), detail=str(kinds))
    s.check("a read tool needed no confirmation",
            not any(r["action_status"] == "pending" for r in rows),
            detail="looking at today's arrivals is not a decision")

    # ---- an action, which must NOT happen -------------------------------
    s.section("An action is written down, not carried out")
    conn.execute(
        """INSERT INTO expenses (description, amount, kind, status, submitted_at, vendor_name)
           VALUES (?, 42.00, 'supplier_invoice', 'pending', ?, ?)""",
        (TAG + " a crate of candles", m.house_today().isoformat(), TAG + " Chandler"))
    conn.commit()
    expense_id = conn.execute(
        "SELECT id FROM expenses WHERE description LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()["id"]

    before = _install([
        _Response([_Block(type="tool_use", id="t2", name="approve_expense",
                          input={"expense_id": expense_id})]),
    ])
    try:
        ok, err = m.assistant_turn(conn, owner, "approve the candles")
    finally:
        _restore(before)
    s.check("the turn succeeds", ok, detail=str(err))
    proposal = conn.execute(
        """SELECT * FROM assistant_messages WHERE user_id = ? AND action_status = 'pending'
           ORDER BY id DESC LIMIT 1""", (owner["id"],)).fetchone()
    s.check("a proposal is waiting", proposal is not None)
    # THE CHECK THIS FILE EXISTS FOR.
    still = conn.execute("SELECT status FROM expenses WHERE id = ?",
                         (expense_id,)).fetchone()["status"]
    s.check("and the expense has NOT been approved", still == "pending",
            detail="a model response changed a real record: " + str(still))
    s.check("the sentence shown is written from the database",
            proposal and "42.00" in (proposal["content"] or ""),
            detail=repr(proposal["content"] if proposal else None))

    # ---- and only a person makes it happen ------------------------------
    s.section("Only the owner pressing confirm carries it out")
    r = oc.post(f"/assistant/confirm/{proposal['id']}", data={"agreed": "yes"},
                follow_redirects=True)
    s.check("the confirm goes through", r.status_code == 200)
    now = conn.execute("SELECT status FROM expenses WHERE id = ?",
                       (expense_id,)).fetchone()["status"]
    s.check("now it is approved", now == "approved", detail=str(now))
    done = conn.execute("SELECT * FROM assistant_messages WHERE id = ?",
                        (proposal["id"],)).fetchone()
    s.check("the proposal is marked confirmed", done["action_status"] == "confirmed")
    s.check("with the time it actually ran", bool(done["executed_at"]))
    # The audit line has to say a human decided, and that it came this way.
    entry = conn.execute(
        """SELECT * FROM audit_log WHERE action = 'expense_approved'
           ORDER BY id DESC LIMIT 1""").fetchone()
    s.check("the audit line names the owner, not the machine",
            entry and entry["actor_user_id"] == owner["id"],
            detail="the owner confirming is the owner deciding")
    s.check("and says it came through the assistant",
            entry and "assistant" in (entry["details"] or ""),
            detail=repr(entry["details"] if entry else None))

    s.section("Pressing it twice does nothing the second time")
    again = oc.post(f"/assistant/confirm/{proposal['id']}", data={"agreed": "yes"},
                    follow_redirects=True)
    s.check("the second press is refused", again.status_code == 200)
    s.check("and it is still just approved once",
            conn.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'expense_approved'"
                         " AND details LIKE '%assistant%'").fetchone()["c"] == 1,
            detail="a stale tab must not decide twice")

    # ---- saying no ------------------------------------------------------
    s.section("Saying no leaves the record alone")
    conn.execute(
        """INSERT INTO expenses (description, amount, kind, status, submitted_at, vendor_name)
           VALUES (?, 99.00, 'supplier_invoice', 'pending', ?, ?)""",
        (TAG + " a thing not wanted", m.house_today().isoformat(), TAG + " Nobody"))
    conn.commit()
    refuse_id = conn.execute(
        "SELECT id FROM expenses WHERE description LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()["id"]
    before = _install([
        _Response([_Block(type="tool_use", id="t3", name="approve_expense",
                          input={"expense_id": refuse_id})]),
    ])
    try:
        m.assistant_turn(conn, owner, "approve that one too")
    finally:
        _restore(before)
    pending = conn.execute(
        """SELECT * FROM assistant_messages WHERE user_id = ? AND action_status = 'pending'
           ORDER BY id DESC LIMIT 1""", (owner["id"],)).fetchone()
    oc.post(f"/assistant/confirm/{pending['id']}", data={"agreed": "no"},
            follow_redirects=True)
    s.check("the expense is untouched",
            conn.execute("SELECT status FROM expenses WHERE id = ?",
                         (refuse_id,)).fetchone()["status"] == "pending")
    s.check("and the refusal is kept rather than deleted",
            conn.execute("SELECT action_status FROM assistant_messages WHERE id = ?",
                         (pending["id"],)).fetchone()["action_status"] == "cancelled",
            detail="what was suggested and refused is worth looking back at")

    # ---- the ways round the gate ---------------------------------------
    s.section("The ways round it")
    other = conn.execute(
        """SELECT * FROM assistant_messages WHERE user_id = ? AND action_status = 'cancelled'
           ORDER BY id DESC LIMIT 1""", (owner["id"],)).fetchone()
    ok, _msg = m.assistant_confirm(conn, owner, other["id"], True)
    s.check("a proposal already dealt with cannot be run", not ok)

    # A FRESH one, still pending. Tried first against an already-confirmed
    # proposal, which passed for the wrong reason: it was refused for being
    # spent, not for belonging to somebody else, so removing the user filter
    # entirely left this green. The negative control caught that.
    conn.execute(
        """INSERT INTO expenses (description, amount, kind, status, submitted_at, vendor_name)
           VALUES (?, 7.00, 'supplier_invoice', 'pending', ?, ?)""",
        (TAG + " someone else's to confirm", m.house_today().isoformat(), TAG + " Co"))
    conn.commit()
    mine_id = conn.execute(
        "SELECT id FROM expenses WHERE description LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()["id"]
    before = _install([
        _Response([_Block(type="tool_use", id="t4", name="approve_expense",
                          input={"expense_id": mine_id})]),
    ])
    try:
        m.assistant_turn(conn, owner, "and this one")
    finally:
        _restore(before)
    fresh = conn.execute(
        """SELECT * FROM assistant_messages WHERE user_id = ? AND action_status = 'pending'
           ORDER BY id DESC LIMIT 1""", (owner["id"],)).fetchone()
    s.check("there is a live proposal to try it on", fresh is not None)
    # Over HTTP first, because that is the way somebody would actually try
    # it: a real session, a real POST, somebody else's proposal id.
    over_http = ec.post(f"/assistant/confirm/{fresh['id']}",
                        data={"agreed": "yes"})
    s.check("an employee posting to the confirm route is refused",
            over_http.status_code == 403, detail=str(over_http.status_code))
    ok, _msg = m.assistant_confirm(conn, emp, fresh["id"], True)
    s.check("and one belonging to somebody else cannot be run either", not ok,
            detail="the row is read back by id AND user, never trusted from the form")
    s.check("and that expense is still untouched",
            conn.execute("SELECT status FROM expenses WHERE id = ?",
                         (mine_id,)).fetchone()["status"] == "pending",
            detail="confirming across users would decide somebody else's money")

    # A tool name the model invented must not reach the dispatcher.
    ok, msg = m.assistant_run_action(conn, owner, "delete_everything", {})
    s.check("a tool that does not exist does nothing", not ok, detail=str(msg))

    # The list of what needs confirming is the server's, not the model's.
    s.check("every action tool is on the server's own list",
            all(t["name"] in m.ASSISTANT_ACTION_TOOLS
                for t in m.ASSISTANT_TOOLS
                if t["name"].split("_")[0] in
                ("approve", "reject", "decline", "add", "finish")),
            detail="a mutating tool missing from ASSISTANT_ACTION_TOOLS would "
                   "run straight from a model response")
    s.check("and no read tool is on it",
            not ({"get_today", "get_waiting", "get_warnings", "find_booking"}
                 & set(m.ASSISTANT_ACTION_TOOLS)),
            detail="confirming a lookup teaches people to press yes unread")

    # ---- with no key at all --------------------------------------------
    s.section("With no key set")
    s.check("the assistant says so rather than failing",
            not m.assistant_enabled(),
            detail="ANTHROPIC_API_KEY is cleared by the harness")
    page = oc.get("/assistant").get_data(as_text=True)
    s.check("and the page says what is missing", "ANTHROPIC_API_KEY" in page)
    ok, err = m.assistant_turn(conn, owner, "anything?")
    s.check("a question gets a plain refusal, not a traceback",
            not ok and "ANTHROPIC_API_KEY" in (err or ""), detail=str(err))

    _cleanup(conn, owner["id"])
    conn.close()
    return s
