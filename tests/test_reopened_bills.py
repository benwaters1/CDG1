"""A settled bill opened again — and the hole that left in the voids report.

Voids are the first thing anybody looks at in a till. Reopened bills are the
second, for the same reason: the money had been taken, and then the bill
changed. `pos_reopen_tab` is careful — it refuses inside a day that has been
closed off, writes an audit line and a journal entry, and stamps `reopened_at`
— and nothing has ever read `reopened_at`.

THE PART THAT MATTERS MORE THAN THE PAGE.

Reopening CLEARS `closed_at`, and settling again overwrites it with the new
time. The voids report's sharpest signal is "a line taken off a bill that had
already been settled", worked out as `voided_at > closed_at`. So:

  reopen the tab, and closed_at is NULL — the void reads as ordinary;
  settle it again, and closed_at is later than the void — ordinary again.

The one line on that page worth reading first could be switched off by
pressing Reopen, and the page would go on looking perfectly healthy. That is
tested here rather than in the voids suite because it is only reachable
through this route, and a hole nobody can reach in a test is a hole.

The fix is to stop throwing the first settlement away: `first_settled_at` and
`first_settled_total` are written once with COALESCE. `reopened_at` stays a
lower bound for rows written before those columns existed, so old bills are
not silently exonerated.
"""
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "reoptest-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Bills opened again after they were settled")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    night = m.service_day()

    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Waiter", TAG + "w@example.invalid", now.isoformat()))
    conn.commit()
    who = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "w@example.invalid",)).fetchone()["id"]

    def order(label, *, first_at, first_total, reopened, closed=None,
              total=None, status="paid"):
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_date,
                       opened_at, closed_at, reopened_at, first_settled_at,
                       first_settled_total, settled_total, opened_by_user_id)
               VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (TAG + label, status, night.isoformat(), now.isoformat(),
             closed.isoformat() if closed else None,
             reopened.isoformat() if reopened else None,
             first_at.isoformat() if first_at else None, first_total,
             total, who))
        conn.commit()
        return conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                            (TAG + label,)).fetchone()["id"]

    settled = now - timedelta(hours=4)
    # Reopened and finished for ninety euros less.
    smaller = order("SMALLER", first_at=settled, first_total=300.0,
                    reopened=settled + timedelta(minutes=25),
                    closed=settled + timedelta(minutes=40), total=210.0)
    # Reopened to add something, and finished larger. Ordinary.
    bigger = order("BIGGER", first_at=settled, first_total=100.0,
                   reopened=settled + timedelta(minutes=2),
                   closed=settled + timedelta(minutes=5), total=104.0)
    # Reopened days ago and never settled again.
    stuck = order("STUCK", first_at=settled, first_total=80.0,
                  reopened=settled + timedelta(minutes=10), closed=None,
                  total=None, status="open")
    # Written before the first-settlement columns existed.
    old = order("OLD", first_at=None, first_total=None,
                reopened=settled + timedelta(minutes=15),
                closed=settled + timedelta(minutes=30), total=50.0)

    d = m.reopen_report(conn, start=night, end=night)
    mine = {r["table"].replace(TAG, ""): r for r in d["rows"]
            if r["table"].startswith(TAG)}

    s.section("It reads back what the till has always written down")
    s.check("every reopened bill is found", len(mine) == 4,
            detail=str(sorted(mine)))
    s.check("with who opened it",
            all(r["who"] == TAG + "Waiter" for r in mine.values()),
            detail=str([r["who"] for r in mine.values()]))
    s.check("and one that was never reopened is not on it",
            not [r for r in d["rows"] if r["order"]["reopened_at"] is None],
            detail="the whole list is reopened bills, so a row without a "
                   "reopened_at means the query is not asking about them")

    s.section("The difference is the number")
    s.check("a bill that finished smaller says by how much",
            abs(mine["SMALLER"]["moved"] + 90.0) < 0.01,
            detail=str(mine["SMALLER"]["moved"]))
    s.check("and it is counted as money coming back",
            abs(d["given_back"] - 90.0) < 0.01, detail=str(d["given_back"]))
    s.check("one that finished larger is not",
            mine["BIGGER"]["moved"] > 0
            and abs(d["given_back"] - 90.0) < 0.01,
            detail=f"{mine['BIGGER']['moved']} — reopening to add a forgotten "
                   "coffee is ordinary and must not be counted as a loss")
    s.check("and only the smaller ones are counted", d["down"] == 1,
            detail=str(d["down"]))

    s.section("How long it had been settled")
    s.check("a correction two minutes later is measured in minutes",
            mine["BIGGER"]["minutes_settled"] is not None
            and mine["BIGGER"]["minutes_settled"] < 5,
            detail=str(mine["BIGGER"]["minutes_settled"]))
    s.check("and a bill opened twenty-five minutes on is a different act",
            mine["SMALLER"]["minutes_settled"] > 20,
            detail=f"{mine['SMALLER']['minutes_settled']} — somebody noticing "
                   "and somebody going back are not the same thing")

    s.section("A bill still sitting open is on no cash-up")
    s.check("it is marked", mine["STUCK"]["still_open"] is True)
    s.check("one that was settled again is not",
            mine["SMALLER"]["still_open"] is False)
    s.check("and it is counted", d["still_open"] == 1, detail=str(d["still_open"]))

    s.section("A bill it cannot answer for says so")
    s.check("no first figure means no difference, not a difference of nought",
            mine["OLD"]["moved"] is None,
            detail="nought reads as a bill that did not change, which is a "
                   "different claim from not knowing")
    s.check("and they are counted separately",
            d["unanswerable"] == 1, detail=str(d["unanswerable"]))

    s.section("THE HOLE THIS LEFT IN THE VOIDS REPORT")
    # A line struck off a bill that was settled, then reopened, then settled
    # again later. closed_at is now AFTER the void, so on closed_at alone it
    # reads as an ordinary void keyed mid-service.
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                   voided, added_by_user_id, voided_by_user_id, void_reason,
                   voided_at, created_at, state)
           VALUES (?, ?, 90.0, 1, 1, ?, ?, 'Removed', ?, ?, 'ordered')""",
        (smaller, TAG + "struck", who, who,
         (settled + timedelta(minutes=30)).isoformat(), now.isoformat()))
    conn.commit()
    v = m.void_report(conn, start=night, end=night)
    hit = next((r for r in v["rows"] if r["what"] == TAG + "struck"), None)
    s.check("the void is found at all", hit is not None)
    s.check("and it is still called a void after settling",
            hit and hit["after_close"] is True,
            detail="closed_at is now LATER than the void because the bill was "
                   "settled a second time; on closed_at alone this reads as an "
                   "ordinary mid-service void, and pressing Reopen would turn "
                   "off the one signal on that page worth reading first")

    # And while a bill sits reopened, closed_at is NULL entirely.
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                   voided, added_by_user_id, voided_by_user_id, void_reason,
                   voided_at, created_at, state)
           VALUES (?, ?, 20.0, 1, 1, ?, ?, 'Removed', ?, ?, 'ordered')""",
        (stuck, TAG + "while-open", who, who,
         (settled + timedelta(minutes=20)).isoformat(), now.isoformat()))
    conn.commit()
    v = m.void_report(conn, start=night, end=night)
    hit = next((r for r in v["rows"] if r["what"] == TAG + "while-open"), None)
    s.check("a void while the bill sits reopened counts too",
            hit and hit["after_close"] is True,
            detail="closed_at is NULL here, so nothing but the first "
                   "settlement can tell you the money had already been taken")

    s.section("Settled, struck off, THEN reopened")
    # The case first_settled_at exists for, and the only one where nothing
    # else answers. closed_at is later than the void because the bill was
    # settled again; reopened_at is later than the void as well. Only the
    # first settlement says the money had already been taken when the line
    # went.
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                   voided, added_by_user_id, voided_by_user_id, void_reason,
                   voided_at, created_at, state)
           VALUES (?, ?, 15.0, 1, 1, ?, ?, 'Removed', ?, ?, 'ordered')""",
        (smaller, TAG + "before-reopen", who, who,
         (settled + timedelta(minutes=5)).isoformat(), now.isoformat()))
    conn.commit()
    v = m.void_report(conn, start=night, end=night)
    hit = next((r for r in v["rows"] if r["what"] == TAG + "before-reopen"), None)
    s.check("it still counts as a void after settling",
            hit and hit["after_close"] is True,
            detail="the void is BEFORE the reopening and before the second "
                   "settlement, so closed_at and reopened_at both call it "
                   "ordinary; only the first settlement knows otherwise")

    s.section("Settling twice keeps the first figure")
    # Through the real route rather than by writing the answer in. Every
    # fixture above inserts first_settled_at directly, which tests the report
    # and not the mechanism -- and the mechanism is one COALESCE away from
    # throwing the original away every time.
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_date,
                   opened_at, opened_by_user_id)
           VALUES (?, 2, 'open', ?, ?, ?)""",
        (TAG + "TWICE", night.isoformat(), now.isoformat(), who))
    conn.commit()
    twice = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                         (TAG + "TWICE",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                   voided, added_by_user_id, created_at, state)
           VALUES (?, ?, 100.0, 1, 0, ?, ?, 'ordered')""",
        (twice, TAG + "dish", who, now.isoformat()))
    conn.commit()
    # log_audit reads the session, so closing a tab needs a request context.
    with m.app.test_request_context("/pos"):
        m.pos_take_payment(conn, twice, 100.0, "cash", user_id=who)
        m.pos_close_if_settled(conn, twice, "cash", user_id=who)
    conn.commit()
    first = conn.execute(
        "SELECT first_settled_at, first_settled_total, settled_total "
        "FROM pos_orders WHERE id = ?", (twice,)).fetchone()
    s.check("the first settlement is recorded",
            first["first_settled_at"] and abs(first["first_settled_total"] - 100.0) < 0.01,
            detail=str(dict(first)))

    was_at = first["first_settled_at"]
    # Reopen it, strike the dish off, and settle the empty bill again.
    conn.execute(
        "UPDATE pos_orders SET status = 'open', closed_at = NULL, reopened_at = ? "
        "WHERE id = ?", (now.isoformat(), twice))
    conn.execute("UPDATE pos_order_lines SET voided = 1, voided_by_user_id = ?, "
                 "void_reason = 'Removed', voided_at = ? WHERE order_id = ?",
                 (who, now.isoformat(), twice))
    conn.commit()
    with m.app.test_request_context("/pos"):
        m.pos_close_if_settled(conn, twice, "cash", user_id=who)
    conn.commit()
    again_row = conn.execute(
        "SELECT first_settled_at, first_settled_total, settled_total, closed_at "
        "FROM pos_orders WHERE id = ?", (twice,)).fetchone()
    s.check("settling again does not overwrite it",
            again_row["first_settled_at"] == was_at
            and abs(again_row["first_settled_total"] - 100.0) < 0.01,
            detail=f"{dict(again_row)} — one COALESCE away from throwing the "
                   "original away, which is what makes 'reopened and settled "
                   "for a hundred less' answerable at all")
    s.check("and the difference is the whole bill",
            abs(m.reopen_report(conn, start=night, end=night)["given_back"]
                - 190.0) < 0.01,
            detail="ninety off one bill and a hundred off this one")

    s.section("The page shows it")
    body = oc.get("/admin/restaurant/reopened?from=%s&to=%s"
                  % (night.isoformat(), night.isoformat())).get_data(as_text=True)
    s.check("the bills are on it", TAG + "SMALLER" in body)
    s.check("with who opened them", TAG + "Waiter" in body)
    s.check("the one still open is called out", "Still open" in body)
    s.check("in a banner, because it is on no cash-up",
            "never got settled again" in body)
    s.check("and the one it cannot answer for says why",
            "settled before this was recorded" in body,
            detail="a blank column reads as no change")

    s.section("Windows and exports")
    quiet = oc.get("/admin/restaurant/reopened?from=2019-01-01&to=2019-01-02")
    s.check("an empty window answers", quiet.status_code == 200)
    s.check("and says nothing was reopened",
            "No bill was reopened" in quiet.get_data(as_text=True))
    r = oc.get("/admin/restaurant/reopened.csv?from=%s&to=%s"
               % (night.isoformat(), night.isoformat()))
    csv = r.get_data(as_text=True)
    s.check("the export carries the difference",
            "difference" in csv and TAG + "SMALLER" in csv)
    s.check("named for the window it covers",
            night.isoformat() in (r.headers.get("Content-Disposition") or ""),
            detail=str(r.headers.get("Content-Disposition")))

    s.section("It is the owner's number")
    s.check("an employee cannot open it",
            ec.get("/admin/restaurant/reopened").status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
