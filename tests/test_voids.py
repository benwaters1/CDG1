"""The till records every void carefully. Nothing read one back.

`pos_void_item` is one of the most careful routes in the file. It refuses a
void with no reason, records who did it, writes an audit line, appends to the
journal, puts the stock back and releases a package allowance. Its own
docstring says why: a void with no reason and no name "is the oldest hole in
any till: it is how stock walks out of a building and the numbers still
balance".

All of that RECORDS the hole. None of it CLOSES it. `void_reason`,
`voided_by_user_id` and `voided_at` appeared in no template and in no report,
so in the life of this app nobody has ever been able to look at them — and
reviewing voids is the first thing anyone does with a till.

WHAT THE REPORT HAS TO GET RIGHT, and what each check is really guarding.

  THE NIGHT, NOT THE DATE. A void keyed at half past one belongs to the night
  that is still being served. Scoped on `voided_at`, it would land on the
  following day's figures while the sale it cancels sat on the night before —
  the same fault the house day exists to prevent, in a new place.

  AGAINST WHAT THEY RANG UP. A plain count names whoever works the most
  shifts. It is the wrong answer given confidently, which is worse than no
  answer, so the share of a person's own sales is here and it is tested with
  a big voider who sells more than a small one.

  AFTER THE KITCHEN HAD IT. A line rung in error is caught in seconds and
  costs nothing. One voided after it was sent has been cooked and the food is
  gone whatever the till says. The route knows the difference at the moment
  of voiding; nothing kept it.

  AFTER THE BILL WAS SETTLED. The sharpest signal there is. Named, not
  blocked: correcting a closed order can be honest, and refusing it is the
  house's decision, not the software's.

  AND IT MUST AGREE WITH WHAT SELLS. Both value a line gross, as rung. Two
  bases would make the share of sales quietly wrong, which is exactly the
  kind of number that gets believed.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "voidtest-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Voids: who took it off, when, and why")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    night = m.service_day()
    before = night - timedelta(days=2)

    def person(name):
        conn.execute(
            """INSERT INTO users (name, email, password_hash, role, status, created_at)
               VALUES (?, ?, 'x', 'employee', 'active', ?)""",
            (TAG + name, TAG + name.lower() + "@example.invalid", now.isoformat()))
        conn.commit()
        return conn.execute("SELECT id FROM users WHERE email = ?",
                            (TAG + name.lower() + "@example.invalid",)).fetchone()["id"]

    big = person("Big")        # sells a lot, voids a lot
    small = person("Small")    # sells little, voids nearly as much

    def order(label, day, *, closed=None, status="paid"):
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_date,
                                       opened_at, closed_at, opened_by_user_id)
               VALUES (?, 2, ?, ?, ?, ?, ?)""",
            (TAG + label, status, day.isoformat(), now.isoformat(),
             closed.isoformat() if closed else None, big))
        conn.commit()
        return conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                            (TAG + label,)).fetchone()["id"]

    def line(order_id, name, price, *, added_by, voided_by=None, reason=None,
             voided_at=None, sent=False, qty=1):
        conn.execute(
            """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                       voided, added_by_user_id, voided_by_user_id, void_reason,
                       voided_at, sent_at, created_at, state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ordered')""",
            (order_id, TAG + name, price, qty, 1 if voided_by else 0, added_by,
             voided_by, reason, voided_at.isoformat() if voided_at else None,
             now.isoformat() if sent else None, now.isoformat()))
        conn.commit()

    # ---------------------------------------------------------------- fixtures
    settled_at = now - timedelta(hours=3)
    tonight = order("T1", night, closed=settled_at)
    # Big rings up 300 and voids 40 of it.
    for i in range(6):
        line(tonight, f"sold{i}", 50.0, added_by=big)
    line(tonight, "cork", 40.0, added_by=big, voided_by=big,
         reason="Corked bottle", voided_at=now - timedelta(hours=4))
    # Small rings up 50 and voids 30 — a third of the count, most of the trade.
    line(tonight, "small-sold", 50.0, added_by=small)
    line(tonight, "own", 30.0, added_by=small, voided_by=small,
         reason="Rung in error", voided_at=now - timedelta(hours=4), sent=True)
    # And one taken off after the bill was settled -- by somebody other than
    # the person who keyed it, so "voided their own line" has a False to be
    # tested against. A flag that is True in every fixture is not tested.
    #
    # Sized to be the largest such void in the window, because the panel names
    # the biggest one and this suite runs against a copy of the real database
    # after a dozen others have put their own service through the till. Read
    # rather than guessed: a fixture that ASSUMES it is the largest passes on
    # its own and fails in the run, which is how this was found.
    already = m.void_report(conn, start=night - timedelta(days=14), end=night)
    biggest = max([r["value"] for r in already["rows"] if r["after_close"]]
                  or [0.0])
    after_value = round(biggest + 120.0, 2)
    line(tonight, "after", after_value, added_by=big, voided_by=small,
         reason="Rung in error", voided_at=settled_at + timedelta(minutes=20),
         sent=True)

    earlier = order("T0", before, closed=now - timedelta(days=2))
    line(earlier, "old", 12.0, added_by=big, voided_by=big, reason="Sent back",
         voided_at=now - timedelta(days=2))

    # This runs against a copy of the real database, after a dozen suites have
    # already put their own service through the till. Exact claims are about
    # THIS suite's rows; the totals are checked for agreeing with the rows they
    # are totals of, which tests the arithmetic without asserting anything
    # about somebody else's fixtures.
    def mine(report):
        return [r for r in report["rows"] if r["what"].startswith(TAG)]

    expected = round(40.0 + 30.0 + after_value, 2)
    d = m.void_report(conn, start=night, end=night)

    s.section("It reads back what the till has always written down")
    s.check("the voided lines are found", len(mine(d)) == 3,
            detail=str([r["what"] for r in mine(d)]))
    s.check("and the ones that were sold are not",
            all("sold" not in r["what"] for r in mine(d)),
            detail=str([r["what"] for r in mine(d)]))
    s.check("with the value taken off the bills",
            abs(sum(r["value"] for r in mine(d)) - expected) < 0.01,
            detail=f"{sum(r['value'] for r in mine(d))} of an expected "
                   f"{expected}")
    s.check("the reason each one was given",
            {"Corked bottle", "Rung in error"}
            <= {r["reason"] for r in d["reasons"]},
            detail=str([r["reason"] for r in d["reasons"]])[:150])
    s.check("and the name against it",
            {r["who"] for r in mine(d)} == {TAG + "Big", TAG + "Small"},
            detail=str([r["who"] for r in mine(d)]))

    s.section("The totals are totals of what is shown")
    s.check("the count on the tile is the number of lines listed",
            d["voids"] == len(d["rows"]), detail=str(d["voids"]))
    s.check("and the money on it adds up to the lines",
            abs(d["value"] - round(sum(r["value"] for r in d["rows"]), 2)) < 0.01,
            detail=f"{d['value']} vs {sum(r['value'] for r in d['rows'])}")
    s.check("the by-reason table accounts for every one of them",
            sum(r["voids"] for r in d["reasons"]) == len(d["rows"]),
            detail=f"{sum(r['voids'] for r in d['reasons'])} vs "
                   f"{len(d['rows'])} \u2014 a row that adds up to less than "
                   "its total is the kind of number that gets believed")
    s.check("and so does the by-person one",
            sum(p["voids"] for p in d["people"]) == len(d["rows"]),
            detail=f"{sum(p['voids'] for p in d['people'])} vs {len(d['rows'])}")

    s.section("A night is a night, not a date")
    s.check("a void from two nights ago is not on tonight's figures",
            all("old" not in r["what"] for r in mine(d)),
            detail="scoped on the order's service date, so a void keyed at "
                   "01:30 stays on the night being served")
    wide = m.void_report(conn, start=before, end=night)
    s.check("and widening the window finds it", len(mine(wide)) == 4,
            detail=str([r["what"] for r in mine(wide)]))
    s.check("with the earlier night's value in the total",
            abs(sum(r["value"] for r in mine(wide)) - (expected + 12.0)) < 0.01,
            detail=str(sum(r["value"] for r in mine(wide))))

    s.section("Against what they rang up, because a count names the busiest")
    people = {p["who"]: p for p in d["people"]}
    s.check("both are named",
            {TAG + "Big", TAG + "Small"} <= set(people),
            detail=str([p for p in people if p.startswith(TAG)]))
    s.check("the busier one is not the worse one by count",
            people[TAG + "Big"]["voids"] <= people[TAG + "Small"]["voids"],
            detail=f"{people[TAG + 'Big']['voids']} vs "
                   f"{people[TAG + 'Small']['voids']}")
    s.check("but the quieter one voided a far larger share of their own sales",
            people[TAG + "Small"]["share"] > people[TAG + "Big"]["share"] * 3,
            detail=f"{people[TAG + 'Small']['share']}% of "
                   f"{people[TAG + 'Small']['sold']} vs "
                   f"{people[TAG + 'Big']['share']}% of "
                   f"{people[TAG + 'Big']['sold']} — the count says the "
                   "opposite, which is the whole reason the share is here")
    s.check("their own sales exclude what they struck off",
            abs(people[TAG + "Big"]["sold"] - 300.0) < 0.01,
            detail=f"{people[TAG + 'Big']['sold']} — a voided line was not sold")

    s.section("A share of nothing is not a share")
    ghost = person("Ghost")
    ghost_order = order("T2", night, closed=settled_at)
    line(ghost_order, "ghosted", 10.0, added_by=big, voided_by=ghost,
         reason="Rung in error", voided_at=now - timedelta(hours=2))
    g = {p["who"]: p for p in m.void_report(conn, start=night, end=night)["people"]}
    s.check("somebody who rang up nothing has no percentage at all",
            g[TAG + "Ghost"]["share"] is None,
            detail=f"{g[TAG + 'Ghost']['share']!r} — 0% reads as a good "
                   "record; None reads as no answer, which is the truth")
    s.check("and they are still on the list with what they voided",
            abs(g[TAG + "Ghost"]["value"] - 10.0) < 0.01,
            detail=str(g[TAG + "Ghost"]["value"]))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id = ?", (ghost_order,))
    conn.execute("DELETE FROM pos_orders WHERE id = ?", (ghost_order,))
    conn.execute("DELETE FROM users WHERE id = ?", (ghost,))
    conn.commit()

    s.section("The three that matter")
    d = m.void_report(conn, start=night, end=night)
    rows = {r["what"].replace(TAG, ""): r for r in d["rows"]}
    s.check("a line the kitchen already had is marked",
            rows["own"]["after_sent"] is True,
            detail="it was cooked; the food is gone whatever the till says")
    s.check("one caught before it was sent is not",
            rows["cork"]["after_sent"] is False)
    s.check("two of them were sent",
            sum(1 for r in mine(d) if r["after_sent"]) == 2,
            detail=str([r["what"] for r in mine(d) if r["after_sent"]]))
    s.check("and the tile counts every one that was",
            d["after_sent"] == sum(1 for r in d["rows"] if r["after_sent"]),
            detail=str(d["after_sent"]))

    s.check("a line taken off a settled bill is marked",
            rows["after"]["after_close"] is True,
            detail="the money had already been taken when the line went")
    s.check("one voided before the bill closed is not",
            rows["own"]["after_close"] is False,
            detail="voided_at is before closed_at, and both are moments — "
                   "comparing them as strings is how this gets read backwards")
    s.check("and it is counted once",
            sum(1 for r in mine(d) if r["after_close"]) == 1,
            detail=str([r["what"] for r in mine(d) if r["after_close"]]))
    s.check("with the tile agreeing",
            d["after_close"] == sum(1 for r in d["rows"] if r["after_close"]),
            detail=str(d["after_close"]))

    s.check("voiding your own line says so",
            rows["own"]["same_person"] is True)
    s.check("and voiding somebody else's does not",
            rows["after"]["same_person"] is False,
            detail="checked both ways round: a flag that is True in every "
                   "fixture has not been tested")
    s.check("with the name of whoever keyed it in",
            rows["after"]["added_by"] == TAG + "Big",
            detail=str(rows["after"]["added_by"]))

    s.section("It agrees with what sells about what a line was worth")
    sells = m.what_sells(conn, days=2, today=night)
    sold_names = {r["name"] for r in sells}
    s.check("what sells leaves the voided lines out",
            TAG + "cork" not in sold_names,
            detail="a line struck off was not sold")
    s.check("and counts the ones that stood", TAG + "sold0" in sold_names,
            detail="if it found nothing at all the check above proves nothing")
    took = next((r["took"] for r in sells if r["name"] == TAG + "sold0"), None)
    s.check("both value a line gross, as rung", took is not None
            and abs(took - 50.0) < 0.01
            and abs(rows["cork"]["value"] - 40.0) < 0.01,
            detail=f"sold at {took}, voided at {rows['cork']['value']} -- two "
                   "bases would make the share of sales quietly wrong")

    s.section("The page shows all of it")
    body = oc.get("/admin/restaurant/voids?from=%s&to=%s"
                  % (night.isoformat(), night.isoformat())).get_data(as_text=True)
    s.check("the voided lines are on it", TAG + "cork" in body)
    s.check("with who took them off", TAG + "Big" in body and TAG + "Small" in body)
    s.check("and why", "Corked bottle" in body)
    s.check("a sold line is not on it", TAG + "sold0" not in body,
            detail="this is the voids page, not the sales page")
    s.check("the settled-bill void is called out", "After settling" in body)
    s.check("in a banner, because it is the one worth reading first",
            "already been settled" in body)
    s.check("and the kitchen ones are marked", "Kitchen had it" in body)

    s.section("Windows and exports")
    narrow = oc.get("/admin/restaurant/voids?from=%s&to=%s"
                    % (before.isoformat(), before.isoformat())).get_data(as_text=True)
    s.check("asking for another night shows that night", TAG + "old" in narrow)
    s.check("and not this one", TAG + "cork" not in narrow,
            detail="a window that ignores its dates is a report of everything")

    r = oc.get("/admin/restaurant/voids.csv?from=%s&to=%s"
               % (night.isoformat(), night.isoformat()))
    csv = r.get_data(as_text=True)
    s.check("the export carries the same lines", TAG + "cork" in csv)
    s.check("with the reason and the name", "Corked bottle" in csv
            and TAG + "Big" in csv)
    s.check("and the flags, so a spreadsheet can sort on them",
            "after_kitchen" in csv and "after_close" in csv)
    s.check("named for the window it covers",
            night.isoformat() in (r.headers.get("Content-Disposition") or ""),
            detail=str(r.headers.get("Content-Disposition")))

    s.section("An empty window says so rather than showing nothing")
    quiet = oc.get("/admin/restaurant/voids?from=2019-01-01&to=2019-01-02")
    s.check("it answers", quiet.status_code == 200)
    s.check("and says nothing was voided",
            "Nothing was voided" in quiet.get_data(as_text=True),
            detail="an empty table with headings reads as a broken page")

    s.section("The finding comes to the owner rather than waiting to be asked")
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, m.house_today())
    hit = next((w for w in warns if "voided after the bill" in w["title"]), None)
    s.check("a line taken off a settled bill reaches the panel", hit is not None,
            detail=str([w["title"] for w in warns])[:170])
    s.check("naming who, what and how much",
            hit and TAG + "Small" in hit["detail"]
            and TAG + "after" in hit["detail"],
            detail=str(hit)[:200])
    s.check("with the reason they gave",
            hit and "Rung in error" in hit["detail"], detail=str(hit)[:200])
    s.check("as something to look at, not an emergency",
            hit and hit["severity"] == "attention",
            detail="no money has stopped moving; calling this a blocker is "
                   "how a panel teaches somebody to skim the line that is one")
    s.check("saying plainly that it can be honest",
            hit and "perfectly honest" in hit["detail"], detail=str(hit)[:200])
    s.check("and it goes to the page that says more",
            hit and "voids" in hit["href"], detail=str(hit))

    s.section("And it can be quiet, or it is furniture")
    conn.execute("UPDATE pos_order_lines SET voided_at = ? WHERE name = ?",
                 ((settled_at - timedelta(minutes=5)).isoformat(), TAG + "after"))
    conn.commit()
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, m.house_today())
    others = [r for r in m.void_report(
        conn, start=m.house_today() - timedelta(days=14),
        end=m.house_today())["rows"] if r["after_close"]]
    s.check("with nothing taken off a settled bill, the panel says nothing",
            not any("voided after the bill" in w["title"] for w in warns)
            if not others else
            all(w["count"] == len(others) for w in warns
                if "voided after the bill" in w["title"]),
            detail="a line that is always on the panel is furniture, and gets "
                   "scrolled past along with the one that matters"
                   + (f" (another suite left {len(others)} in the window, so "
                      "what is checked here is that this one's is gone)"
                      if others else ""))
    conn.execute("UPDATE pos_order_lines SET voided_at = ? WHERE name = ?",
                 ((settled_at + timedelta(minutes=20)).isoformat(), TAG + "after"))
    conn.commit()

    s.section("It is the owner's number")
    s.check("an employee cannot open it",
            _ec.get("/admin/restaurant/voids").status_code in (302, 403),
            detail="who voided what is a management figure, and the people "
                   "in it are the people it is about")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
