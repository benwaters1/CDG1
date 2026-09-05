"""An action taken with no signal: kept, sent once, and never applied twice.

The château is a valley behind a metre of stone and the signal dies room by
room. A housekeeper on the third floor tapped a task off, the POST never left
the handset, and the page said "check your connection and try again" — which
is true and useless. They are standing in the room, the work is done, and
there is no connection to check. The tap was simply gone.

So the handset keeps it and sends it when the signal comes back. That turns
one guarantee into three, and every one of them can be got wrong quietly:

  IT MUST NOT APPLY TWICE. A queue retries by definition — a phone cannot
  tell "the house never heard me" from "it heard me and the reply was lost".
  Without a key the second is indistinguishable from the first: a clock-in
  becomes two shifts, a stock movement moves stock twice, and net_hours
  poisons a payslip. So every held action carries a key generated when the
  person TAPPED, and the second arrival gets the first one's answer back
  rather than doing the work again.

  IT MUST NOT HOLD JUST ANYTHING. Ticking a room off is a fact about work
  already done and holding it ten minutes changes nothing. Confirming a
  booking is a DECISION, and a decision taken on stale information and applied
  ten minutes later is how somebody gets a room given away in between. So an
  allowlist, and a key sent to anything not on it is refused rather than
  quietly ignored — a key that does nothing is worse than no key, because the
  handset believes it is safe to retry.

  AND THERE MUST BE ONE LIST. The list lives in app.py and is rendered into
  the page. A copy in the JavaScript would be a second list, and two lists
  that have to agree are two lists that stop agreeing — quietly, with a phone
  holding an action nothing on the other end will take.
"""
import json
import re
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOA"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM action_keys WHERE key LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _task_status(task_id):
    conn = db()
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?",
                           (task_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("An action taken with no signal")
    _cleanup()
    oc, ec, owner, emp = clients()
    conn = db()
    conn.execute(
        """INSERT INTO tasks (title, status, assigned_to_user_id, created_at)
           VALUES (?, 'open', ?, ?)""",
        (TAG + " sweep the tower stair", owner["id"],
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    tid = conn.execute("SELECT id FROM tasks WHERE title = ?",
                       (TAG + " sweep the tower stair",)).fetchone()["id"]
    conn.close()

    try:
        s.section("With no key, nothing changes")
        # The path everybody with signal takes. It has to go on working
        # exactly as before, or this feature has cost more than it gave.
        r = oc.post(f"/tasks/{tid}/complete")
        s.check("the tap still works", r.status_code == 200, detail=str(r.status_code))
        s.check("and the task is done", _task_status(tid) == "done")
        oc.post(f"/tasks/{tid}/complete")  # back to open

        s.section("The same tap, sent twice")
        key = TAG + "-key-one"
        first = oc.post(f"/tasks/{tid}/complete", headers={"X-Action-Key": key})
        s.check("the first one does the work",
                first.status_code == 200 and _task_status(tid) == "done",
                detail=_task_status(tid))
        second = oc.post(f"/tasks/{tid}/complete", headers={"X-Action-Key": key})
        s.check("the second is not refused",
                second.status_code == 200, detail=str(second.status_code))
        # THE WHOLE POINT. complete_task TOGGLES, so a replay that ran the view
        # again would untick the task -- the handset would show it done, the
        # house would show it open, and nobody would ever know which tap did it.
        s.check("and it does not undo the first",
                _task_status(tid) == "done",
                detail="complete_task toggles: a replay that ran the view "
                       "again leaves the task " + str(_task_status(tid)))
        s.check("the answer is the same answer, not a fresh one",
                second.get_data(as_text=True) == first.get_data(as_text=True),
                detail="the handset is showing what the first reply said; a "
                       "different one now makes the screen disagree with "
                       "itself: %r vs %r" % (first.get_data(as_text=True)[:60],
                                             second.get_data(as_text=True)[:60]))

        s.section("A different tap is a different key")
        r = oc.post(f"/tasks/{tid}/complete",
                    headers={"X-Action-Key": TAG + "-key-two"})
        s.check("so it is applied on its own merits",
                _task_status(tid) == "open",
                detail="the second real tap unticks it; a key is per TAP, not "
                       "per action type")

        s.section("What the house is willing to hold")
        # An allowlist, and a key on anything else is REFUSED. Quietly ignoring
        # it is the dangerous answer: the handset would believe the retry was
        # safe and send a decision twice.
        r = oc.post("/clock/in", headers={"X-Action-Key": TAG + "-not-allowed"},
                    follow_redirects=False)
        s.check("a key on something not on the list is refused",
                r.status_code == 400,
                detail="quietly ignoring it tells the handset the retry is "
                       "safe when it is not: got %s" % r.status_code)
        conn = db()
        try:
            left = conn.execute("SELECT 1 FROM action_keys WHERE key = ?",
                                (TAG + "-not-allowed",)).fetchone()
        finally:
            conn.close()
        s.check("and it is not written down either", not left,
                detail="a key filed for an action that never ran would answer "
                       "the retry with a success that never happened")

        s.section("Nothing that decides anything is on the list")
        # The rule that keeps this safe, checked rather than asserted. A held
        # action is applied minutes later against a house that has moved on,
        # so it may only be something that has ALREADY HAPPENED.
        import inspect
        dangerous = []
        for endpoint in m.OFFLINE_ACTIONS:
            view = m.app.view_functions.get(endpoint)
            if not view:
                dangerous.append("%s: no such page" % endpoint)
                continue
            body = inspect.getsource(view)
            code = "\n".join(l.split("#")[0] for l in body.splitlines())
            for call in ("send_email(", "issue_refund(", "stripe.",
                         "claim_range(", "send_sms("):
                if call in code:
                    dangerous.append("%s calls %s" % (endpoint, call))
        s.check("none of them writes to a guest, moves money or claims a room",
                not dangerous, detail=str(dangerous))
        s.check("and every one of them is a real page",
                all(e in m.app.view_functions for e in m.OFFLINE_ACTIONS),
                detail=str([e for e in m.OFFLINE_ACTIONS
                            if e not in m.app.view_functions]))

        s.section("And the list is one list")
        page = ec.get("/").get_data(as_text=True)
        block = re.search(r'id="offline-actions">(.*?)</script>', page, re.S)
        s.check("the page carries it", block is not None)
        drawn = json.loads(block.group(1)) if block else {}
        s.check("and it is the same list, not a copy of it",
                drawn == m.OFFLINE_ACTIONS,
                detail="a second list is a list that stops agreeing: %s vs %s"
                       % (sorted(drawn), sorted(m.OFFLINE_ACTIONS)))
        s.check("the script that reads it is on the page",
                "actions.js" in page)
        # THE OTHER DIRECTION. A route wearing @repeatable but missing from the
        # list refuses every key it is sent -- so the handset holds the action,
        # replays it for ever, and it is never applied.
        source = open("app.py", encoding="utf-8").read()
        wearing = set(re.findall(
            r"@repeatable\s*\ndef (\w+)\(", source.replace("\r\n", "\n")))
        s.check("and every page wearing the decorator is on it",
                wearing == set(m.OFFLINE_ACTIONS),
                detail="on the list but undecorated: %s; decorated but not "
                       "listed: %s" % (sorted(set(m.OFFLINE_ACTIONS) - wearing),
                                       sorted(wearing - set(m.OFFLINE_ACTIONS))))

        s.section("The script itself is whole")
        # NOTHING HERE RUNS JAVASCRIPT, and a broken actions.js fails in the
        # quietest way this app has: the file still answers 200, "the script
        # is on the page" above still passes, and every tap taken with no
        # signal is lost exactly as it was before any of this was written.
        #
        # This is a structural check, not a parser: it counts brackets outside
        # strings and comments, and looks for the four things the file says it
        # puts on window. It would not catch a wrong variable name. It does
        # catch a file that was truncated or left half-edited, which is the
        # failure that has actually happened to a file in this repo.
        js = open("static/actions.js", encoding="utf-8").read()
        depth, i, ok = {"(": 0, "{": 0, "[": 0}, 0, True
        closes = {")": "(", "}": "{", "]": "["}
        while i < len(js):
            c = js[i]
            if c in "'\"" or c == "`":
                quote, i = c, i + 1
                while i < len(js) and js[i] != quote:
                    i += 2 if js[i] == "\\" else 1
            elif c == "/" and js[i:i + 2] == "//":
                i = js.find(chr(10), i)
                if i < 0:
                    break
            elif c == "/" and js[i:i + 2] == "/*":
                i = js.find("*/", i) + 1
            elif c in depth:
                depth[c] += 1
            elif c in closes:
                depth[closes[c]] -= 1
                if depth[closes[c]] < 0:
                    ok = False
                    break
            i += 1
        s.check("its brackets balance",
                ok and not any(depth.values()), detail=str(depth))
        for name in ("take", "pending", "flush", "allowed"):
            s.check("it puts %s where the pages look for it" % name,
                    "window.gudanes." + name + " =" in js,
                    detail="the pages call gudanes." + name)
        s.check("and it never keeps anything the server did not allow",
                "gudanes.allowed" in js or "offline-actions" in js,
                detail="the list has to come from the page, not from here")

        s.section("The keys do not pile up for ever")
        conn = db()
        try:
            conn.execute(
                """INSERT INTO action_keys (key, endpoint, applied_at,
                           status_code, body)
                   VALUES (?, 'complete_task', ?, 200, '{}')""",
                (TAG + "-ancient",
                 (m.datetime.now(m.timezone.utc) - timedelta(days=90)).isoformat()))
            conn.commit()
            gone = m.purge_action_keys(conn)
            still = conn.execute("SELECT 1 FROM action_keys WHERE key = ?",
                                 (TAG + "-ancient",)).fetchone()
            recent = conn.execute("SELECT 1 FROM action_keys WHERE key = ?",
                                  (TAG + "-key-one",)).fetchone()
        finally:
            conn.close()
        s.check("an old one goes", gone >= 1 and not still, detail=str(gone))
        s.check("and a recent one stays",
                recent is not None,
                detail="a phone in a drawer over a holiday still has to "
                       "replay safely when it comes back")
        s.check("and the daily pass runs it",
                "purge_action_keys" in "\n".join(
                    l.split("#")[0] for l in
                    inspect.getsource(m.run_health_notes_purge_job).splitlines()),
                detail="written and never called, the table only grows")
    finally:
        _cleanup()
    return s
