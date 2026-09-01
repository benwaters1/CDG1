"""What day it is AT THE HOUSE, on the pages that ask.

The breakfast checklist was a day behind for the first hours of every day.
Ticking an item wrote `datetime.now(timezone.utc).date()` against it, and
Paris runs one or two hours AHEAD of UTC -- so from midnight until 01:00 in
winter, or 02:00 in summer, the house was already on the new day and UTC was
still on the old one. Somebody closing down after a late service ticked off
the croissants for the morning; the tick went to yesterday; at seven the
list was blank again.

Nothing looked wrong because every part of it agreed with itself — the writer
and both readers used the same wrong day. That is the shape this file guards:
consistency is not correctness, and a test written with the same expression as
the code passes for ever.

So this does not compare the app's answer to the machine's clock at whatever
hour the suite happens to run. It moves the clock to the hours where the two
differ and asks the app what day it is. Those are:

    winter (UTC+1)   00:00–00:59 local
    summer (UTC+2)   00:00–01:59 local

and the same instants read as the previous day in UTC. A run at three in the
afternoon would never see it, which is why the original bug survived
everything.

The first check in each section says out loud that the two clocks disagree
at the instant being tested. It is not decoration: I first wrote these as
23:30, which is 21:30 UTC and the SAME day, and without that check the
section would have reported four cheerful passes while proving nothing.
"""
from _harness import Suite, clients, db, house_today

import inspect

import _harness

m = _harness.m
TAG = "ZZHOUSEDAY"


class _FrozenDatetime(m.datetime):
    """datetime.now() pinned to one instant, tz-aware, for both zones."""

    _at = None

    @classmethod
    def now(cls, tz=None):
        assert cls._at is not None, "nothing pinned"
        return cls._at.astimezone(tz) if tz else cls._at.replace(tzinfo=None)


def _cleanup(conn):
    conn.execute(
        "DELETE FROM breakfast_checklist_log WHERE item_id IN "
        "(SELECT id FROM breakfast_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM breakfast_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the day at the house")
    conn = db()
    oc, _ec, _owner, _emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO breakfast_items (name, category, active, low_stock,
                                        created_at)
           VALUES (?, 'bakery', 1, 0, ?)""",
        (TAG + " Croissants", m.datetime.now(m.timezone.utc).isoformat()))
    item = conn.execute("SELECT id FROM breakfast_items WHERE name LIKE ?",
                        (TAG + "%",)).fetchone()["id"]
    conn.commit()

    def ticks_on(day):
        return conn.execute(
            "SELECT COUNT(*) FROM breakfast_checklist_log "
            "WHERE item_id = ? AND checklist_date = ?",
            (item, day.isoformat())).fetchone()[0]

    # Half past midnight, on a summer date and a winter one. Both are inside
    # the window where the house has turned over and UTC has not.
    for label, local_iso in (("in summer, at 00:30", "2026-07-16T00:30:00+02:00"),
                             ("in winter, at 00:30", "2026-01-16T00:30:00+01:00")):
        at = m.datetime.fromisoformat(local_iso)
        local_day = at.astimezone(m.LOCAL_TZ).date()
        utc_day = at.astimezone(m.timezone.utc).date()

        s.section("Ticking off the breakfast list " + label)
        s.check("the two clocks really do disagree at this instant",
                local_day != utc_day,
                detail=f"{local_day} at the house, {utc_day} in UTC — if these "
                       "ever match, this section is proving nothing")

        _FrozenDatetime._at = at
        real = m.datetime
        m.datetime = _FrozenDatetime
        try:
            oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
            page = oc.get("/breakfast").get_data(as_text=True)
        finally:
            m.datetime = real

        s.check("the tick is written against today at the house",
                ticks_on(local_day) == 1,
                detail="somebody closing down after a late service ticks "
                       "off the croissants for the morning, and it lands on "
                       "the wrong day")
        s.check("and not against yesterday", ticks_on(utc_day) == 0,
                detail=f"a row on {utc_day} is a tick nobody will see in the "
                       "morning")

        # The reader has to agree with the writer. Them disagreeing is a
        # checklist that ticks in one place and reads back unticked in the
        # other, which is worse than either being wrong on its own.
        # The row for THIS item, and the class the template actually puts on
        # a ticked one. The first version of this check was
        # `row not in page or "checked" in page`, whose left half is true the
        # moment the markup changes -- so it would have gone on passing after
        # the thing it was looking at stopped existing.
        at = page.find(f'id="view-breakfast-{item}"')
        row = page[page.rfind("<div", 0, at):page.find(">", at) + 1] if at >= 0 else ""
        s.check("and the page reads it back as ticked", "task-done" in row,
                detail="the writer and the reader have to agree about what "
                       "day it is, or the list ticks in one place and reads "
                       "back blank in the other")

        conn.execute("DELETE FROM breakfast_checklist_log WHERE item_id = ?",
                     (item,))
        conn.commit()

    s.section("And at a normal hour it still works")
    # The half that stops this from being a test only about midnight.
    today = house_today()
    oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
    s.check("a tick now lands on today", ticks_on(today) == 1,
            detail=str(today))
    oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
    s.check("and unticking removes it", ticks_on(today) == 0)

    s.section("The pages that ask what day it is ask the house")
    # Named, because these three moved together and a later edit that puts
    # one of them back on UTC would leave the other two disagreeing with it.
    for fn_name, why in (
            ("toggle_breakfast_item", "writes the checklist"),
            ("breakfast", "reads it back"),
            ("today_sheet", "prints who is in the house today")):
        src = inspect.getsource(m.app.view_functions[fn_name])
        s.check(f"{fn_name} ({why}) uses the local day",
                "datetime.now(LOCAL_TZ).date()" in src
                and "today = datetime.now(timezone.utc).date()" not in src,
                detail="a UTC calendar date where a local one belongs")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
