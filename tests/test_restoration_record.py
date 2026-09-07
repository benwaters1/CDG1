# -*- coding: utf-8 -*-
"""The restoration record: what was done, what it cost, and what was found.

Gudanes has been under restoration since 2013 and nothing recorded any of it.
There was a public page telling the story in hand-written prose, and prose
goes stale the moment the work moves on.

Not a task list — a task is "fix the north gutter" and it disappears the day
it is ticked. This accumulates, keyed to the BUILDING rather than the
calendar, so opening a place five years on reads everything ever done to it.

WHAT IT HAS TO GET RIGHT, and what each check is really guarding:

  THE MONEY, OR IT IS STILL JUST A STORY. Attaching invoices is what turns
  "the roof" into a figure. It is stated gross, as invoiced, because that is
  what the expense rows hold — and a REFUSED invoice must not count, because
  an expense the owner declined paid for nothing. That one is checked with a
  rejected row in the fixture, since a total that quietly includes refusals
  is the kind of number that gets believed.

  AND ATTACHING TWICE MUST NOT COST DOUBLE. Pressing a button again is the
  most ordinary thing anybody will do, and a job costed twice is worse than
  one not costed at all.

  WHERE, HOWEVER IT IS RECORDED. Most of a château is not a bedroom. A record
  that can only file work against the five rooms it sells is a record of
  almost nothing, so a named place carries equal weight.

  PLANNED WORK REACHES THE CALENDAR, finished work does not. That is the same
  rule everything else in this file keeps, and the second half matters: a
  task for something already done is a line nobody can tick, on a list that
  then gets skimmed.

  AND PUBLISHING IS A DECISION. Off by default, finished only. A record of
  the house's own work is not automatically something the house has chosen to
  put in front of guests.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "restotest-"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM restoration_works WHERE title LIKE ?", (TAG + "%",))]
    for wid in ids:
        conn.execute("DELETE FROM tasks WHERE restoration_work_id = ?", (wid,))
    conn.execute("DELETE FROM restoration_works WHERE title LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The restoration record: the work, the money, the story")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    import re

    # Two invoices that count and one the owner refused.
    for desc, amount, status in (
            (TAG + "slate", 2400.00, "paid"),
            (TAG + "scaffolding", 780.50, "approved"),
            (TAG + "quote we turned down", 5000.00, "rejected")):
        conn.execute(
            """INSERT INTO expenses (kind, description, amount, status,
                                     vendor_name, submitted_at, invoice_date,
                                     is_capital)
               VALUES ('supplier_invoice', ?, ?, ?, 'Toiture Ariège', ?,
                       '2026-08-14', 1)""",
            (desc, amount, status, now))
    conn.commit()
    ids = {r["description"]: r["id"] for r in conn.execute(
        "SELECT id, description FROM expenses WHERE description LIKE ?",
        (TAG + "%",))}
    s.check("there are invoices to cost a job with", len(ids) == 3,
            detail=str(sorted(ids)))

    # ---- recording a piece of work ---------------------------------------
    s.section("A job, keyed to the building")
    resp = oc.post("/admin/restoration/new", data={
        "title": TAG + "north gutter", "place": "The north elevation",
        "kind": "roof", "status": "in_progress", "started_on": "2026-08-01",
        "summary": "Zinc replaced in slate to match the 1740 work.",
        "found": "The original lead flashing, stamped 1747."})
    s.check("a piece of work can be recorded", resp.status_code == 302)
    found = re.search(r"/restoration/(\d+)", resp.headers.get("Location") or "")
    s.check("and it lands on its own page", found is not None,
            detail=resp.headers.get("Location"))
    if not found:
        _cleanup(conn)
        conn.close()
        return s
    wid = int(found.group(1))

    work = conn.execute("SELECT * FROM restoration_works WHERE id = ?",
                        (wid,)).fetchone()
    s.check("a place that is not a room is recorded as one",
            m.restoration_where(work) == "The north elevation",
            detail="most of a château is not a bedroom; a record that can "
                   "only file against the five rooms it sells records almost "
                   "nothing")
    s.check("and what was found doing it is kept",
            "1747" in (work["found"] or ""), detail=str(work["found"]))

    # ---- the calendar ----------------------------------------------------
    s.section("Work still to do reaches the calendar")
    task = conn.execute(
        "SELECT * FROM tasks WHERE restoration_work_id = ?", (wid,)).fetchone()
    s.check("work underway raises a task", task is not None,
            detail="anything that has to happen belongs on a list")
    s.check("dated to when it starts, and marked where it came from",
            task and task["due_date"] == "2026-08-01"
            and task["origin"] == "restoration",
            detail=str(dict(task)) if task else "")

    # ---- the money -------------------------------------------------------
    s.section("What it cost, which is the point")
    oc.post(f"/admin/restoration/{wid}/invoices", data={
        "expense_id": [str(ids[TAG + "slate"]),
                       str(ids[TAG + "scaffolding"]),
                       str(ids[TAG + "quote we turned down"])]})
    cost = m.restoration_cost(conn, wid)
    s.check("the job carries what it cost", cost["total"] == 3180.50,
            detail="%s — gross, as invoiced" % cost["total"])
    s.check("a refused invoice is not counted", cost["count"] == 2,
            detail="%d invoices — an expense the owner declined paid for "
                   "nothing, and a total that includes refusals is the kind "
                   "of number that gets believed" % cost["count"])
    s.check("and it names who was paid", cost["suppliers"] == ["Toiture Ariège"],
            detail=str(cost["suppliers"]))

    again = oc.post(f"/admin/restoration/{wid}/invoices", data={
        "expense_id": [str(ids[TAG + "slate"])]})
    s.check("attaching the same invoice again is not an error",
            again.status_code == 302,
            detail="%d — pressing a button twice is the most ordinary thing "
                   "anybody will do to this page, and it must not fail"
                   % again.status_code)
    s.check("and does not cost the job twice",
            m.restoration_cost(conn, wid)["total"] == 3180.50,
            detail="a job costed double is worse than one not costed at all")
    s.check("nor leave the invoice attached twice",
            conn.execute(
                """SELECT COUNT(*) AS c FROM restoration_work_expenses
                    WHERE work_id = ? AND expense_id = ?""",
                (wid, ids[TAG + "slate"])).fetchone()["c"] == 1,
            detail="the total is right today because the link is unique; "
                   "checking only the total would pass on a sum that happens "
                   "to be correct")

    # And taking one off again. The coverage report named this route as
    # reached but never answered, which was fair: the page has the button,
    # the suite had only ever pressed the one beside it. An invoice attached
    # to the wrong job is the ordinary mistake here -- capital spend covers
    # several jobs at once and the descriptions are a supplier's, not ours --
    # so the way back out matters as much as the way in.
    off = oc.post("/admin/restoration/%d/invoices/%d/remove"
                  % (wid, ids[TAG + "scaffolding"]))
    s.check("an invoice attached to the wrong job comes off again",
            off.status_code == 302, detail=str(off.status_code))
    s.check("and the cost falls by exactly that invoice",
            m.restoration_cost(conn, wid)["total"] == 2400.00,
            detail="%s — 3180.50 less the 780.50 that was taken off"
                   % m.restoration_cost(conn, wid)["total"])
    oc.post(f"/admin/restoration/{wid}/invoices",
            data={"expense_id": [str(ids[TAG + "scaffolding"])]})
    s.check("and goes back on when it was the right job after all",
            m.restoration_cost(conn, wid)["total"] == 3180.50)

    listed = [w for w in m.restoration_works(conn) if w["id"] == wid]
    s.check("and the list carries the cost without asking per row",
            listed and round(listed[0]["cost"], 2) == 3180.50,
            detail=str(listed[0]["cost"]) if listed else "not listed")

    # ---- finishing, and publishing ---------------------------------------
    s.section("Finished, and the decision to publish it")
    # Read as a stranger, not as the owner who just saved it: the owner's
    # own flash message ("… recorded.") renders on the next page they load,
    # which would make this check pass or fail on the wrong thing entirely.
    # It is also the honest question — the public page is for people with no
    # session at all.
    public = m.app.test_client()
    before = public.get("/restoration/record").get_data(as_text=True)
    s.check("an unfinished job is not on the public page",
            TAG + "north gutter" not in before)

    oc.post(f"/admin/restoration/{wid}/save", data={
        "title": TAG + "north gutter", "place": "The north elevation",
        "kind": "roof", "status": "done", "started_on": "2026-08-01",
        "finished_on": "2026-08-29",
        "summary": "Zinc replaced in slate to match the 1740 work.",
        "found": "The original lead flashing, stamped 1747."})
    after = public.get("/restoration/record").get_data(as_text=True)
    s.check("nor is a finished one nobody has chosen to publish",
            TAG + "north gutter" not in after,
            detail="a record of the house's own work is not automatically "
                   "something it has decided to show guests")

    task = conn.execute("SELECT status FROM tasks WHERE restoration_work_id = ?",
                        (wid,)).fetchone()
    s.check("finishing the work closes its task",
            task and task["status"] == "done",
            detail="a task for something already done is a line nobody can "
                   "tick, on a list that then gets skimmed")

    oc.post(f"/admin/restoration/{wid}/save", data={
        "title": TAG + "north gutter", "place": "The north elevation",
        "kind": "roof", "status": "done", "started_on": "2026-08-01",
        "finished_on": "2026-08-29",
        "summary": "Zinc replaced in slate to match the 1740 work.",
        "found": "The original lead flashing, stamped 1747.",
        "publishable": "1"})
    published = public.get("/restoration/record").get_data(as_text=True)
    s.check("once published, it is on the public page",
            TAG + "north gutter" in published)
    s.check("and what was found is the part it leads with",
            "stamped 1747" in published,
            detail="that is the content that sells the ateliers")

    # ---- the photographs ---------------------------------------------------
    #
    # A before is only worth keeping if there is an after beside it, and a
    # route only ever seen REFUSING is a route nobody has tested — the
    # coverage report named this one for exactly that.
    s.section("A photograph, added and taken away")
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
           b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
           b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    import io as _io
    up = oc.post(f"/admin/restoration/{wid}/photos", data={
        "kind": "before", "caption": TAG + "the gutter as found",
        "taken_on": "2026-07-30",
        "photo": (_io.BytesIO(png), "before.png")},
        content_type="multipart/form-data")
    s.check("a photograph can be added", up.status_code == 302,
            detail=str(up.status_code))
    pics = m.restoration_photos(conn, wid)
    s.check("it is filed against the work", len(pics) == 1,
            detail="%d photographs" % len(pics))
    s.check("as a before, with the caption and the day it was taken",
            pics and pics[0]["kind"] == "before"
            and pics[0]["taken_on"] == "2026-07-30"
            and TAG in (pics[0]["caption"] or ""),
            detail=str(dict(pics[0])) if pics else "")
    s.check("and it carries whoever added it",
            pics and (pics[0]["added_by_name"] or "").strip(),
            detail="three months on, nobody can tell which wall it is "
                   "without asking somebody")

    gone = oc.post("/admin/restoration/photos/%d/remove" % pics[0]["id"])
    s.check("and it can be taken away again", gone.status_code == 302,
            detail=str(gone.status_code))
    s.check("leaving none behind",
            len(m.restoration_photos(conn, wid)) == 0)

    # ---- the band and the guards -----------------------------------------
    s.section("What the pages say, and who may see them")
    summary = m.restoration_summary(conn)
    s.check("the band counts the spend nobody has ever had",
            summary["spend_all"] >= 3180.50, detail=str(summary["spend_all"]))
    s.check("and names finished work nobody has written up",
            "unwritten" in summary, detail=str(sorted(summary)))

    page = oc.get(f"/admin/restoration/{wid}").get_data(as_text=True)
    s.check("the job's own page shows the cost", "3180.50" in page)
    s.check("and says the figure is gross, as invoiced",
            "as invoiced" in page,
            detail="every figure in this app states which it is")

    s.check("an employee cannot open the record",
            ec.get("/admin/restoration").status_code == 403
            and ec.get(f"/admin/restoration/{wid}").status_code == 403)
    s.check("but anybody may read the public page",
            ec.get("/restoration/record").status_code == 200)

    _cleanup(conn)
    conn.close()
    return s
