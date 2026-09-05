# -*- coding: utf-8 -*-
"""Meetings: notes in, minutes out, and promises that land on people's lists.

A meeting module that only records meetings is a filing cabinet. What makes
it worth opening is that what somebody AGREED to turns up on their list on
the day it is due — so an action with an account behind it writes a real row
in `tasks` with origin 'meeting', and reaches the calendar and that person's
own page with no further plumbing. That is the rule this file keeps
everywhere else and there was no reason for meetings to be the exception.

WHAT THIS HAS TO GET RIGHT, and what each check is really guarding:

  A CONTRACTOR HAS NO LOGIN. vendors are a contact record, not an account, so
  an action owned by one cannot become a task: there is no list to put it on
  and nobody to show it to. It has to be recorded anyway and it has to be
  VISIBLY different, because an action presented as though somebody has been
  reminded, when nobody has, is worse than one plainly marked "ring them".

  MINUTES MUST WORK WITHOUT CLAUDE. ANTHROPIC_API_KEY is not set on this
  deployment and readiness has said so all along. A feature that only works
  with a key ships dead and the person who asked for it finds out by pressing
  the button, so the extractor reads the notes on its own and Claude improves
  the result when there is one. The page says which wrote what it is showing:
  minutes a machine wrote and minutes a person wrote are not the same claim.

  NOTHING IS INVENTED. Every line the extractor produces is a line somebody
  typed. An empty note gives empty minutes, not a plausible account of a
  meeting nobody described.

  TICKING ONE TICKS BOTH. An action marked done while its task sits open on
  somebody's list is the same work counted twice, and the person still
  looking at it has no way to know it is settled.

  AND WRITING THEM UP TWICE MUST NOT DOUBLE THE WORK. Pressing the button
  again is the most ordinary thing anybody will do to this page.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "meetingtest-"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM meetings WHERE title LIKE ?", (TAG + "%",))]
    for mid in ids:
        conn.execute("DELETE FROM tasks WHERE meeting_id = ?", (mid,))
    conn.execute("DELETE FROM meetings WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Meetings: what was agreed, and whose list it lands on")
    oc, ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    today = m.house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    if not emp:
        s.check("there is a member of staff to put in the room", False)
        conn.close()
        return s

    conn.execute(
        "INSERT INTO vendors (name, contact_person, created_at) VALUES (?, ?, ?)",
        (TAG + "Roofer", "Jean", now))
    vendor_id = conn.execute("SELECT id FROM vendors WHERE name = ?",
                             (TAG + "Roofer",)).fetchone()["id"]
    conn.commit()

    # ---- the extractor, on its own ---------------------------------------
    s.section("Minutes read straight off the notes")
    parsed = m.extract_meeting_minutes(
        "Went through the roof.\n"
        "Decision: slate, not zinc.\n"
        "ACTION: Marie to get three quotes within 2 weeks\n"
        "ACTION: chase the electrician tomorrow\n"
        "The atelier is nearly full.",
        ["Marie Dubois", "Jean"])
    s.check("a decision is separated from the discussion",
            parsed["decisions"] == ["Decision: slate, not zinc."],
            detail=str(parsed["decisions"]))
    s.check("both actions are found", len(parsed["actions"]) == 2,
            detail=str(parsed["actions"]))
    s.check("a name in the room is attached to the action it belongs to",
            parsed["actions"][0]["owner"] == "Marie Dubois",
            detail=str(parsed["actions"][0]))
    s.check("and no name is invented for the one that has none",
            parsed["actions"][1]["owner"] is None,
            detail="a task on the wrong person's list is worse than one on "
                   "nobody's")
    s.check("'within 2 weeks' is read as a fortnight",
            parsed["actions"][0]["due_days"] == 14)
    s.check("and 'tomorrow' as a day",
            parsed["actions"][1]["due_days"] == 1)
    s.check("the discussion is what is left, not the action lines",
            "roof" in parsed["summary"] and "ACTION" not in parsed["summary"],
            detail=parsed["summary"])

    empty = m.extract_meeting_minutes("", [])
    s.check("an empty note gives empty minutes, not an invented meeting",
            not empty["decisions"] and not empty["actions"],
            detail=str(empty))

    # ---- the whole flow, through the pages -------------------------------
    s.section("Called, held, written up")
    import re
    resp = oc.post("/meetings/new", data={
        "title": TAG + "roof", "kind": "contractor",
        "meeting_date": today.isoformat(), "location": "The courtyard",
        "agenda": "The roof."})
    s.check("a meeting can be put in the diary", resp.status_code == 302)
    found = re.search(r"/meetings/(\d+)", resp.headers.get("Location") or "")
    s.check("and it lands on its own page", found is not None,
            detail=resp.headers.get("Location"))
    if not found:
        _cleanup(conn)
        conn.close()
        return s
    mid = int(found.group(1))

    s.check("whoever called it is already in the room",
            conn.execute("SELECT COUNT(*) AS c FROM meeting_attendees "
                         "WHERE meeting_id = ?", (mid,)).fetchone()["c"] == 1,
            detail="a meeting with nobody in it cannot attribute an action")

    oc.post(f"/meetings/{mid}/attendees", data={"who": f"user:{emp['id']}"})
    oc.post(f"/meetings/{mid}/attendees", data={"who": f"vendor:{vendor_id}"})
    people = m.meeting_attendees(conn, mid)
    s.check("staff and a contractor can both be in the room",
            len(people) == 3, detail=str([m.meeting_attendee_name(p)
                                          for p in people]))
    s.check("and each is named however the house knows them",
            {m.meeting_attendee_name(p) for p in people} >=
            {emp["name"], TAG + "Roofer"},
            detail=str([m.meeting_attendee_name(p) for p in people]))

    notes = (f"Talked about the roof.\n"
             f"Decision: slate, not zinc.\n"
             f"ACTION: {emp['name']} to get three quotes within 2 weeks\n"
             f"ACTION: {TAG}Roofer to send the scaffolding plan tomorrow")
    oc.post(f"/meetings/{mid}/notes", data={"notes_raw": notes})
    kept = conn.execute("SELECT notes_raw FROM meetings WHERE id = ?",
                        (mid,)).fetchone()["notes_raw"]
    s.check("the raw note is kept exactly as typed", kept == notes,
            detail="the minutes are an interpretation; the note is evidence")

    resp = oc.post(f"/meetings/{mid}/minutes")
    s.check("the minutes can be written up", resp.status_code == 302)
    meeting = conn.execute("SELECT * FROM meetings WHERE id = ?",
                           (mid,)).fetchone()
    s.check("and writing them up marks the meeting held",
            meeting["status"] == "held",
            detail="a meeting that was minuted happened")
    s.check("the note is still there afterwards", meeting["notes_raw"] == notes)
    s.check("the minutes say which of the two wrote them",
            meeting["minutes_source"] in ("claude", "extracted"),
            detail=str(meeting["minutes_source"]))
    s.check("and they contain the decision that was made",
            "slate" in (meeting["minutes"] or ""),
            detail=meeting["minutes"])

    # ---- the part that makes it worth having ------------------------------
    s.section("Promises land on the right list, or say they cannot")
    actions = m.meeting_actions(conn, mid)
    s.check("both promises are recorded", len(actions) == 2,
            detail=str([a["title"] for a in actions]))
    mine = [a for a in actions if a["owner_user_id"] == emp["id"]]
    theirs = [a for a in actions if not a["owner_user_id"]]
    s.check("the one with an account behind it found its person", len(mine) == 1,
            detail=str([dict(a) for a in actions]))
    s.check("and the contractor's is recorded too", len(theirs) == 1)

    s.check("the staff action became a real task",
            mine and mine[0]["task_id"],
            detail="an action that reaches no list is one nobody is reminded of")
    task = conn.execute("SELECT * FROM tasks WHERE id = ?",
                        (mine[0]["task_id"],)).fetchone() if mine else None
    s.check("assigned to them, dated, and marked as coming from a meeting",
            task and task["assigned_to_user_id"] == emp["id"]
            and task["origin"] == "meeting" and task["meeting_id"] == mid
            and task["due_date"] == (today + timedelta(days=14)).isoformat(),
            detail=str(dict(task)) if task else "no task")

    s.check("the contractor's action has NO task, because there is no account",
            theirs and theirs[0]["task_id"] is None,
            detail="a vendor is a contact record, not a login — a task "
                   "assigned to one would sit on a list nobody can open")
    s.check("and it still carries their name so somebody can ring them",
            theirs and (theirs[0]["owner_external"] or "").strip(),
            detail=str(dict(theirs[0])) if theirs else "")

    # It has to reach the calendar, which is the whole reason for making it a
    # task rather than a row of its own.
    with m.app.test_request_context("/"):
        cal = m.build_calendar(conn, "week", today + timedelta(days=14))
    on_cal = [e for cell in cal["cells"] for e in (cell.get("events") or [])
              if emp["name"] in (e.get("title") or "")
              and "quotes" in (e.get("title") or "")]
    s.check("and it appears on the calendar without anything else being wired",
            bool(on_cal), detail="tasks reach the calendar; that is why an "
                                 "action becomes one")

    s.section("A name, not a fragment of one")
    # WHY THIS IS HERE. The extractor matched an attendee's first name as a
    # bare substring, so "test" inside "meetingtest-Roofer" put the roofer's
    # promise on the employee's list. A promise on the wrong person's list is
    # worse than one on nobody's: the person who made it is never asked, and
    # the person who did not is. Two Maries in a room is all it takes.
    two = ["Marie Lasserre", "Marie-Claire Dupont"]
    s.check("a hyphenated name is not its shorter neighbour",
            m.whose_action("ACTION: Marie-Claire Dupont to ring the mason", two)
            == "Marie-Claire Dupont",
            detail=str(m.whose_action(
                "ACTION: Marie-Claire Dupont to ring the mason", two)))
    s.check("and the full name beats the first name",
            m.whose_action("ACTION: Marie Lasserre to ring the mason", two)
            == "Marie Lasserre")
    same_first = ["Marie Lasserre", "Marie Dupont"]
    s.check("a first name two people really do answer to names neither",
            m.whose_action("ACTION: Marie to ring the mason", same_first) is None,
            detail="guessing between two people is the same fault in a "
                   "politer form: " + str(m.whose_action(
                       "ACTION: Marie to ring the mason", same_first)))
    s.check("and Marie-Claire is not one of the two Maries",
            m.whose_action("ACTION: Marie to ring the mason", two)
            == "Marie Lasserre",
            detail="her first name is Marie-Claire; a line saying Marie "
                   "plainly means the other one")
    s.check("and a first name only one answers to still works",
            m.whose_action("ACTION: Marie to ring the mason",
                           ["Marie Lasserre", "Bruno Sabatier"])
            == "Marie Lasserre")
    one_word = ["Marie", "Marie-Claire"]
    s.check("a one-word name is not the start of a longer one",
            m.whose_action("ACTION: Marie-Claire to ring the mason", one_word)
            == "Marie-Claire",
            detail="Marie is listed first, so anything that takes the first "
                   "match rather than the longest gets this wrong: "
                   + str(m.whose_action(
                       "ACTION: Marie-Claire to ring the mason", one_word)))
    s.check("and with only the shorter one in the room, nobody is credited",
            m.whose_action("ACTION: Marie-Claire to ring the mason",
                           ["Marie"]) is None,
            detail="Marie-Claire is not Marie, and with Marie-Claire absent "
                   "there is nothing to win on length: " + str(m.whose_action(
                       "ACTION: Marie-Claire to ring the mason", ["Marie"])))
    s.check("an apostrophe is part of a name too",
            m.whose_action("ACTION: O'Brien to send the quote",
                           ["O'Brien", "Brien Dupont"]) == "O'Brien")
    s.check("and the shorter one still answers to her own name",
            m.whose_action("ACTION: Marie to ring the mason", one_word)
            == "Marie")
    s.check("a name inside another word is not that name",
            m.whose_action("ACTION: the testbed needs clearing",
                           ["Test Employee"]) is None,
            detail="this is the exact line that crashed this suite")

    # ---- ticking, and pressing the button twice ---------------------------
    s.section("Ticking it off, and doing it all again")
    oc.post(f"/meeting-actions/{mine[0]['id']}/done")
    after = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (mine[0]["task_id"],)
    ).fetchone()
    s.check("ticking the action ticks its task too",
            after and after["status"] == "done",
            detail="an action done here and open on somebody's list is the "
                   "same work counted twice")
    action_after = conn.execute("SELECT status FROM meeting_actions WHERE id = ?",
                                (mine[0]["id"],)).fetchone()
    s.check("and the action itself is done", action_after["status"] == "done")

    oc.post(f"/meetings/{mid}/minutes")
    s.check("writing the minutes again does not duplicate the actions",
            len(m.meeting_actions(conn, mid)) == 2,
            detail="pressing the button twice is the most ordinary thing "
                   "anybody will do to this page")
    s.check("nor the tasks",
            conn.execute("SELECT COUNT(*) AS c FROM tasks WHERE meeting_id = ?",
                         (mid,)).fetchone()["c"] == 1)

    # ---- adding one by hand, which is how most of them arrive -------------
    s.section("An action written straight in")
    before = len(m.meeting_actions(conn, mid))
    resp = oc.post(f"/meetings/{mid}/actions", data={
        "title": TAG + "order the slate", "owner": f"user:{emp['id']}",
        "detail": "from the yard at Tarascon"})
    s.check("an action can be written straight in", resp.status_code == 302)
    added = [a for a in m.meeting_actions(conn, mid)
             if a["title"] == TAG + "order the slate"]
    s.check("it is recorded", len(added) == 1,
            detail="%d actions now" % len(m.meeting_actions(conn, mid)))
    s.check("and it reached that person's list too",
            added and added[0]["task_id"],
            detail="the manual path and the minutes path must agree — an "
                   "action typed in by hand is the same promise")
    s.check("with the house's default timeframe, since none was given",
            added and added[0]["due_date"]
            == (today + timedelta(days=m.MEETING_ACTION_DAYS)).isoformat(),
            detail="an undated action is the one everybody forgets")

    resp = oc.post(f"/meetings/{mid}/actions", data={
        "title": TAG + "ring the roofer", "owner": TAG + "Roofer"})
    outside = [a for a in m.meeting_actions(conn, mid)
               if a["title"] == TAG + "ring the roofer"]
    s.check("one owned by somebody with no account is recorded without a task",
            outside and outside[0]["task_id"] is None
            and (outside[0]["owner_external"] or "").strip(),
            detail=str(dict(outside[0])) if outside else "not recorded")

    resp = oc.post(f"/meetings/{mid}/actions", data={"title": "  "})
    s.check("and an empty one is refused rather than recorded blank",
            len(m.meeting_actions(conn, mid)) == before + 2,
            detail="%d — a blank row in the list of what was agreed is worse "
                   "than a rejected form" % len(m.meeting_actions(conn, mid)))

    # ---- what the pages say -----------------------------------------------
    s.section("What the pages show")
    page = oc.get(f"/meetings/{mid}").get_data(as_text=True)
    s.check("the meeting page names the contractor's action as one to chase",
            "Ring them" in page,
            detail="the alternative is somebody assuming it is on a list")
    s.check("and says where the minutes came from",
            "off the notes" in page or "by Claude" in page, detail="")
    listing = oc.get("/meetings").get_data(as_text=True)
    s.check("the list shows it", TAG + "roof" in listing)

    summary = m.meeting_summary(conn, today)
    s.check("the band counts an action nobody can be reminded of",
            summary["unreminded"] >= 1,
            detail="%s — the ones that need a telephone call" % summary)

    s.check("an employee cannot open any of it",
            ec.get("/meetings").status_code == 403
            and ec.get(f"/meetings/{mid}").status_code == 403)

    _cleanup(conn)
    conn.close()
    return s
