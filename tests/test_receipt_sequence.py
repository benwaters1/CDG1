"""The receipt number promises to be gapless, and nothing checked it.

`pos_allocate_receipt_number` says so in its own first line — "A gapless
number per year" — and the care taken to make it true is real: the number is
allocated at the first payment rather than at open, so a table that sits down
and changes its mind does not eat one and leave a hole nobody can explain.

Then nothing ever looked. The journal has `pos_journal_verify`, which
recomputes the whole chain and answers "has anything been altered since it was
written" with arithmetic rather than trust. The receipt sequence — which is
the thing a French inspector actually asks about — had no equivalent, and
`first_sequence`/`last_sequence` on every closure went into a CSV export and
were never compared with anything.

Having one check on the page and not the other is worse than having neither,
because the page reads as though the till has been checked.

AND THE YEAR CAME FROM UTC.

    year = datetime.now(timezone.utc).year

CLAUDE.md says that expression is never right, and here it was inside the
fiscal numbering. Between midnight and 01:00 in the Ariège on New Year's Day
the house is in January and UTC is still in December, so a receipt printed at
half past midnight would be numbered into a sequence the accounts had already
closed — on a bill dated this year. One receipt a year at most, on the one
night of the year when the numbering matters most, which is exactly the shape
of thing that is never noticed and impossible to explain afterwards.
"""
import re as _re
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "seqtest-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The receipt numbering, checked at last")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    night = m.service_day()

    def order(label, receipt):
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_date,
                       opened_at, receipt_number)
               VALUES (?, 2, 'paid', ?, ?, ?)""",
            (TAG + label, night.isoformat(), now.isoformat(), receipt))
        conn.commit()

    s.section("An unbroken run has no holes in it")
    before = m.receipt_sequence_gaps(conn)
    s.check("the real database's numbering is unbroken", before == [],
            detail=str(before)[:200])

    year = "2099"
    for n in (1, 2, 3):
        order(f"ok{n}", f"{year}-{n:06d}")
    mine = [g for g in m.receipt_sequence_gaps(conn) if g["year"] == year]
    s.check("and three in a row from one is still unbroken", mine == [],
            detail=str(mine))

    s.section("A hole is found, and named as a range")
    order("after-hole", f"{year}-000012")
    mine = [g for g in m.receipt_sequence_gaps(conn) if g["year"] == year]
    s.check("the gap is found", len(mine) == 1, detail=str(mine))
    s.check("it says where it starts", mine and mine[0]["from"] == f"{year}-000004",
            detail=str(mine))
    s.check("and where it ends", mine and mine[0]["to"] == f"{year}-000011",
            detail=str(mine))
    s.check("and how many are missing", mine and mine[0]["missing"] == 8,
            detail=f"{mine} — reported as a range because eight lines saying "
                   "one number each is a page nobody reads to the bottom of")

    s.section("A year that does not start at one is a hole too")
    other = "2098"
    order("late-start", f"{other}-000047")
    mine = [g for g in m.receipt_sequence_gaps(conn) if g["year"] == other]
    s.check("starting at forty-seven is forty-six missing receipts",
            mine and mine[0]["missing"] == 46, detail=str(mine))
    s.check("counted from the first number of the year",
            mine and mine[0]["from"] == f"{other}-000001", detail=str(mine))

    s.section("Years do not run into one another")
    s.check("the two years are separate findings",
            len({g["year"] for g in m.receipt_sequence_gaps(conn)
                 if g["year"] in (year, other)}) == 2,
            detail="the sequence restarts each year, so a run measured across "
                   "the boundary would report one enormous false gap every "
                   "January")

    s.section("A number that is not a number is its own problem")
    order("nonsense", "not-a-receipt")
    bad = [g for g in m.receipt_sequence_gaps(conn) if g["kind"] == "malformed"]
    s.check("it is reported", bad, detail=str(bad))
    s.check("as malformed rather than as a gap",
            bad and bad[0]["missing"] == 0,
            detail="a number that will not parse and a number that is absent "
                   "need different answers, and skipping it silently would "
                   "hide the first one entirely")

    s.section("The page shows both halves of the question")
    # Flattened: the template wraps its sentences, so a substring with a
    # single space in it never matches text that is really there. Third time
    # that has cost a check in this session.
    body = " ".join(oc.get("/admin/pos/journal").get_data(as_text=True).split())
    s.check("the chain check is still there", "Chain" in body)
    s.check("and the numbering beside it", "Receipt numbers" in body,
            detail="one on the page and the other nowhere reads as though "
                   "the till has been checked")
    # What the cell SAYS, not that it exists. A version with the answer
    # hard-coded to "Unbroken" kept the label and passed, which is the cell
    # lying quietly rather than being absent.
    s.check("and it says there are gaps when there are",
            _re.search(r"Gaps\s*<?[^>]*>?\s*\d+ missing", body)
            or ("Gaps" in body and "missing" in body),
            detail=f"{body[body.find('Receipt numbers') - 60:body.find('Receipt numbers') + 90]!r}")
    s.check("the holes are listed", f"{year}-000004" in body)
    s.check("with a sentence saying what a hole means",
            "was paid for and is no longer here" in body)

    _cleanup(conn)

    s.section("THE YEAR IS THE HOUSE'S, NOT THE SERVER'S")
    # 00:30 on the first of January in the Ariège, which is 23:30 on the
    # thirty-first of December in UTC. The house is in the new year; UTC is
    # not. The number has to follow the house.
    new_year = datetime(2031, 1, 1, 0, 30, tzinfo=m.LOCAL_TZ)
    was_datetime, was_today = m.datetime, None
    try:
        was_today = m.house_today

        class _FrozenDatetime(m.datetime):
            @classmethod
            def now(cls, tz=None):
                return new_year.astimezone(tz) if tz else new_year

        m.datetime = _FrozenDatetime
        m.house_today = lambda: new_year.date()
        s.check("the house is in the new year",
                m.house_today() == date(2031, 1, 1),
                detail=str(m.house_today()))
        s.check("and UTC is still in the old one",
                m.datetime.now(timezone.utc).year == 2030,
                detail=str(m.datetime.now(timezone.utc)),
                )
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_date,
                       opened_at) VALUES (?, 2, 'open', ?, ?)""",
            (TAG + "newyear", "2031-01-01", new_year.isoformat()))
        conn.commit()
        oid = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                           (TAG + "newyear",)).fetchone()["id"]
        number = m.pos_allocate_receipt_number(conn, oid)
        conn.commit()
        s.check("the receipt is numbered into the new year",
                number.startswith("2031-"),
                detail=f"{number} — on UTC it would be numbered into 2030, a "
                       "sequence the accounts have already closed, on a bill "
                       "dated the first of January")
    finally:
        m.datetime = was_datetime
        if was_today is not None:
            m.house_today = was_today

    _cleanup(conn)
    s.check("and the clock is put back",
            m.house_today() != date(2031, 1, 1),
            detail="every suite after this one reads the same module")

    s.section("It is the owner's")
    s.check("an employee cannot open the journal",
            ec.get("/admin/pos/journal").status_code in (302, 403))

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
