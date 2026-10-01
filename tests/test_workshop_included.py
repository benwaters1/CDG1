# -*- coding: utf-8 -*-
"""What a workshop's price carries: the transfers, and the excursions.

Two of this year's ateliers are spent entirely at the château — no van from
Toulouse, no trip to the brocantes — and the pages that sell them were
promising both. Not because anybody wrote that it was true, but because the
copy asks the question and there was nothing to answer it with, so
inc_flag() in _stay_or_workshop.html GUESSED: "a nights label starting 4, or
a session starting in 2026, counts as without".

That guess happens to be right for this year's two and is wrong for the
third one anybody adds. It is also wrong in the expensive direction. A
caption that overpromises a photograph is a copy problem; a page that
promises a guest a van from Toulouse, so they book a flight instead of a
hire car, is a guest standing at an airport.

So two columns, two tick-boxes, and this suite. What it is really checking
is that the GUESS IS NEVER REACHED once a real answer exists — because the
guess is silent, plausible, and produces a page that looks completely
normal.

  DEFAULT ON. Every atelier before this year included both, so a column
  arriving empty would put all of them back on the fallback. A new workshop
  starts ticked for the same reason.

  UNTICKED MUST SURVIVE A SAVE. A tick-box sends nothing at all when it is
  clear, so the save has to read absence as the "no". Read any other way, a
  box can be unticked on screen and tick itself again on reload — which is
  the kind of fault somebody notices three bookings later.

  AND IT HAS TO REACH THE PAGE THE GUEST READS. The flag is useless on the
  workshops table alone: the registration page and the confirmation both
  state what is included, and both build their own row. A row that does not
  carry the flags falls back to the guess, quietly, on exactly the page
  somebody is reading as they pay.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZINC"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM workshops WHERE title LIKE ?", (TAG + "%",))]
    for wid in ids:
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ?", (wid,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("What a workshop's price carries")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    s.section("The columns exist, and default to carrying both")
    conn = db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(workshops)")}
    s.check("the two columns are there",
            {"transfers_included", "excursions_included"} <= cols,
            detail="without them every page falls back to guessing from the "
                   "nights label and the year")
    cur = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, sort_order, created_at)
           VALUES (?, '', 2000, 10, 1, 90, ?)""",
        (TAG + " Plain", _harness.datetime_now()))
    wid = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchone()
    s.check("a workshop written without mentioning them carries both",
            row["transfers_included"] == 1 and row["excursions_included"] == 1,
            detail="tx=%s ex=%s — every atelier before this year included "
                   "both, so an empty column would put all of them back on "
                   "the guess" % (row["transfers_included"],
                                  row["excursions_included"]))
    conn.close()

    s.section("Unticking is a thing the owner can actually do")
    # Through the form, not the table. A tick-box sends nothing when it is
    # clear, so this is the path where "no" is expressed as silence.
    oc.post("/admin/workshops/%d/edit" % wid, data={
        "title": TAG + " Plain", "description": "",
        "price_per_person": "2000", "default_capacity": "10",
        "deposit_percent": "10", "active": "on"})
    conn = db()
    row = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchone()
    s.check("a form with neither box ticked turns both off",
            row["transfers_included"] == 0 and row["excursions_included"] == 0,
            detail="tx=%s ex=%s — a clear box sends nothing at all, so "
                   "absence has to be read as the no, or a box cannot be "
                   "unticked at all" % (row["transfers_included"],
                                        row["excursions_included"]))

    oc.post("/admin/workshops/%d/edit" % wid, data={
        "title": TAG + " Plain", "description": "",
        "price_per_person": "2000", "default_capacity": "10",
        "deposit_percent": "10", "active": "on",
        "transfers_included": "1"})
    row = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchone()
    s.check("and ticking one turns only that one back on",
            row["transfers_included"] == 1 and row["excursions_included"] == 0,
            detail="tx=%s ex=%s" % (row["transfers_included"],
                                    row["excursions_included"]))

    s.section("It reaches the page the guest is reading")
    conn.execute("UPDATE workshops SET transfers_included = 0, "
                 "excursions_included = 0 WHERE id = ?", (wid,))
    start = m.house_today() + timedelta(days=120)
    cur = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(),
         TAG + " sitting", _harness.datetime_now()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()

    anon = m.app.test_client()
    page = anon.get("/workshops/register/%d" % sid)
    s.check("the registration page opens", page.status_code == 200,
            detail=str(page.status_code))
    body = page.get_data(as_text=True)
    # The row the page builds has to carry the flags. Checked by what the
    # page SAYS rather than by the query, because a query that selects a
    # column the template never reads is no use to anybody.
    s.check("and does not promise transfers a guest will not get",
            "transfers from central Toulouse" not in body.lower()
            and "transfers from Toulouse" not in body.lower(),
            detail="the session row carries transfers_included = 0; if the "
                   "row does not carry it the page falls back to the guess, "
                   "silently, while somebody is reading it to decide")

    conn = db()
    conn.execute("UPDATE workshops SET transfers_included = 1, "
                 "excursions_included = 1 WHERE id = ?", (wid,))
    conn.commit()
    conn.close()
    body = anon.get("/workshops/register/%d" % sid).get_data(as_text=True)
    s.check("and does say so when they are included",
            "toulouse" in body.lower(),
            detail="turned back on, the same page has to change its mind — "
                   "a check that only ever sees the off state would pass "
                   "against a page that never mentions transfers at all")

    _cleanup()
    return s
