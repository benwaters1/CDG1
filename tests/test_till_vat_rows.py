"""The till bill's VAT rows add up to the bill.

A French bill shows its VAT by rate, and the receipt prints one line per rate
under Total TTC. `pos_bill` shares the discount and the service charge out over
the rates, and it used to round each rate's share on its own, so the rows
could come to a cent more or less than the bill. The brief's example:

    181.47 at 5.5%, 192.51 at 0%, 60.23 at 20%, discount 3.75, service 16.35
    rows 446.82, bill 446.81

How often depends on how many rates. The till charges 10% on food and 20% on
drink, and with only those two the old split almost never missed: two shares
that add up to whole cents round to whole cents unless both sit exactly on a
half (2 bills in 5,000). A third rate is what does it -- a line rung up with no
VAT rate reads as 0% -- and then about one bill in four with a discount or a
service charge was a cent out. Merging tabs now carries a discount across, so
discounted bills are the ordinary case. The split is now done in whole cents
by largest remainder, so the rows add up by construction.

Two smaller holes in the same arithmetic are covered here too. A drinks
package that covers the whole bill left nothing to weigh a service charge by,
so the service fell out of the VAT altogether (rows 0.00, bill 5.00). And a
discount bigger than the bill gave negative VAT rows on a bill whose total is
nothing.

THE HISTORY. A settled bill's `tab_settled` journal entry hashes its VAT rows,
and the period closures add the rows up. A bill settled under the old split
may carry a row a cent away from what the new one gives, so a reprint would
no longer match the note handed over and hashed. So a settled bill reads its
VAT back from its journal entry -- the rows it was issued with -- as long as
that entry still describes the bill. Bills settled from now on journal rows
that add up, so for them that is the same thing.
"""
import random
import re

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "vatrows-"
PAST_DAY = "2019-03-14"     # a day nothing else in the suite is settled on


def _cleanup(conn):
    # Lines and payments go with the order (ON DELETE CASCADE). Journal rows
    # stay: deleting from the middle of the chain is the thing it exists to
    # catch, and AUTOINCREMENT means no later tab ever takes these ids.
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()


def _old_split(by_rate, charged):
    """The split as it was before: every rate rounded on its own.

    Kept here, word for word in effect, so the suite can settle a bill the way
    the till did before the fix and see what happens to it afterwards.
    """
    nets = {rate: b["gross"] - b.get("covered", 0.0) for rate, b in by_rate.items()}
    base = round(sum(nets.values()), 2)
    adjustment = charged - base
    return {rate: round(net + adjustment * (net / base if base else 0), 2)
            for rate, net in nets.items()}


def run():
    s = Suite("The till bill's VAT rows add up to the bill")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    night = m.service_day().isoformat()
    made = [0]

    def ring(lines, *, discount=0.0, service=0.0, deposit=0.0):
        """A tab with these lines: (unit_price, quantity, vat_rate, covered[, voided])."""
        made[0] += 1
        oid = conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_date, opened_at,
                   discount_amount, service_charge, deposit_credit)
               VALUES (?, 2, 'open', ?, ?, ?, ?, ?)""",
            (f"{TAG}{made[0]}", night, now, discount, service, deposit)).lastrowid
        for price, qty, rate, covered, *rest in lines:
            conn.execute(
                """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                       vat_rate, package_covered, voided, course, created_at)
                   VALUES (?, 'x', ?, ?, ?, ?, ?, ?, ?)""",
                (oid, price, qty, rate, covered, 1 if rest and rest[0] else 0,
                 "wine" if rate == 20 else "main", now))
        return oid

    def rows_sum(bill):
        return round(sum(b["gross"] for b in bill["vat_by_rate"].values()), 2)

    def before_deposit(bill):
        # What the meal comes to before any deposit: what the rows must add to.
        return round(max(0.0, bill["gross"] - bill["package"] - bill["discount"]
                         + bill["service"]), 2)

    def settle(oid):
        bill = m.pos_bill(conn, oid)
        # log_audit reads the session, so closing a tab needs a request context.
        with m.app.test_request_context("/pos"):
            if bill["outstanding"] > 0.005:
                m.pos_take_payment(conn, oid, bill["outstanding"], "cash")
            after = m.pos_close_if_settled(conn, oid, "cash")
        conn.commit()
        return after

    def journalled(oid):
        row = conn.execute(
            "SELECT payload FROM pos_journal WHERE order_id = ? AND event_type = 'tab_settled' "
            "ORDER BY sequence DESC LIMIT 1", (oid,)).fetchone()
        payload = m.json.loads(row["payload"]) if row else {}
        return {float(k): v for k, v in (payload.get("vat") or {}).items()}

    # ------------------------------------------------------------------
    s.section("The brief's example")
    oid = ring([(181.47, 1, 5.5, 0), (192.51, 1, 0, 0), (60.23, 1, 20, 0)],
               discount=3.75, service=16.35)
    bill = m.pos_bill(conn, oid)
    s.check("the bill is 446.81 before any deposit",
            abs(before_deposit(bill) - 446.81) < 0.005, detail=str(before_deposit(bill)))
    s.check("and its three rate rows add up to 446.81, not 446.82",
            abs(rows_sum(bill) - 446.81) < 0.005,
            detail=str({k: v["gross"] for k, v in bill["vat_by_rate"].items()}))
    s.check("the old split really did make it 446.82 (so this example tests something)",
            abs(round(sum(_old_split(
                {5.5: {"gross": 181.47}, 0.0: {"gross": 192.51}, 20.0: {"gross": 60.23}},
                446.81).values()), 2) - 446.82) < 0.005)

    # ------------------------------------------------------------------
    s.section("Thousands of generated bills, every one adding up")
    rng = random.Random(16102026)
    trials = 2500
    off, negative, inexact, own_rate, ht, old_off = [], [], [], [], [], 0
    for n in range(trials):
        rates = rng.sample([0, 5.5, 10, 20], rng.randint(2, 4))
        lines = []
        for rate in rates:
            for _ in range(rng.randint(1, 3)):
                price = round(rng.uniform(0.5, 120), 2)
                if rng.random() < 0.05:
                    price = round(rng.uniform(0.5, 30), 3)   # a supplier's odd price
                qty = rng.randint(1, 4)
                covered = 0.0
                if rate == 20 and rng.random() < 0.3:
                    covered = round(rng.uniform(0, price * qty), 2)   # a drinks package
                lines.append((price, qty, rate, covered, rng.random() < 0.08))
        if all(l[4] for l in lines):
            lines[0] = lines[0][:4] + (False,)
        kind = rng.random()
        discount = round(rng.uniform(0.01, 60), 2) if kind < 0.75 else 0.0
        service = round(rng.uniform(0.01, 40), 2) if kind > 0.45 else 0.0
        if rng.random() < 0.04:
            discount = round(rng.uniform(400, 900), 2)          # bigger than the bill
        deposit = round(rng.uniform(5, 80), 2) if rng.random() < 0.2 else 0.0
        oid = ring(lines, discount=discount, service=service, deposit=deposit)
        bill = m.pos_bill(conn, oid)

        want = round(max(0.0, bill["gross"] - bill["package"] - discount + service), 2)
        got = rows_sum(bill)
        label = f"#{n} rows {got} bill {want}"
        if abs(got - want) > 0.005:
            off.append(label)
        if any(b["gross"] < 0 or b["vat"] < 0 for b in bill["vat_by_rate"].values()):
            negative.append(label)
        if any(abs(round(b["net"] + b["vat"], 2) - b["gross"]) > 0.005
               for b in bill["vat_by_rate"].values()):
            ht.append(label)

        # Every row within a cent of its exact share: largest remainder, not
        # a heap of cents dumped on one row.
        served = {}
        for price, qty, rate, covered, voided in lines:
            if not voided:
                b = served.setdefault(float(rate), [0.0, 0.0])
                b[0] += price * qty
                b[1] += covered
        weights = {r: round(g - c, 2) for r, (g, c) in served.items()}
        whole = sum(weights.values())
        if whole > 0.005:
            for r, w in weights.items():
                exact = want * w / whole
                if abs(bill["vat_by_rate"][r]["gross"] - exact) > 0.0101:
                    inexact.append(f"{label} rate {r} {bill['vat_by_rate'][r]['gross']} "
                                   f"vs {exact:.4f}")
                    break
            if not discount and not service:
                # A package comes off the rate its drinks carry, and nothing else moves.
                if any(abs(bill["vat_by_rate"][r]["gross"] - round(w, 2)) > 0.0101
                       for r, w in weights.items()):
                    own_rate.append(label)
            old = _old_split({r: {"gross": g, "covered": c} for r, (g, c) in served.items()},
                             want)
            if abs(round(sum(old.values()), 2) - want) > 0.005:
                old_off += 1

    s.check(f"all {trials} bills' rate rows add up to the bill before the deposit",
            not off, detail=f"{len(off)} off, e.g. {off[:3]}")
    s.check("no rate row is ever negative, even under a discount bigger than the bill",
            not negative, detail=f"{len(negative)}, e.g. {negative[:3]}")
    s.check("every row's HT and TVA add up to the row",
            not ht, detail=f"{len(ht)}, e.g. {ht[:3]}")
    s.check("every row is within a cent of its exact share of the bill",
            not inexact, detail=f"{len(inexact)}, e.g. {inexact[:3]}")
    s.check("a package with no discount or service comes off its own rate, to the cent",
            not own_rate, detail=f"{len(own_rate)}, e.g. {own_rate[:3]}")
    s.check("and the generator does make the bills the old split got wrong",
            old_off > trials // 20, detail=f"only {old_off} of {trials}")

    # ------------------------------------------------------------------
    s.section("A package that covers the whole bill, with a service charge")
    oid_all = ring([(40.0, 1, 20, 40.0)], service=5.0)
    bill = m.pos_bill(conn, oid_all)
    s.check("the service is still in the VAT rows",
            abs(rows_sum(bill) - 5.0) < 0.005 and bill["total"] == 5.0,
            detail=str({k: v["gross"] for k, v in bill["vat_by_rate"].items()}))
    s.check("on the rate of the drinks it was served with",
            abs(bill["vat_by_rate"][20.0]["gross"] - 5.0) < 0.005
            and bill["vat_by_rate"][20.0]["vat"] > 0)
    oid_mix = ring([(40.0, 1, 20, 40.0), (6.0, 1, 10, 6.0)], service=4.60)
    bill = m.pos_bill(conn, oid_mix)
    s.check("over two covered rates, it is shared the way the drinks were (4.00 and 0.60), "
            "not heaped on one",
            abs(bill["vat_by_rate"][20.0]["gross"] - 4.00) < 0.005
            and abs(bill["vat_by_rate"][10.0]["gross"] - 0.60) < 0.005,
            detail=str({k: v["gross"] for k, v in bill["vat_by_rate"].items()}))
    oid_two = ring([(40.0, 1, 20, 40.0), (20.0, 1, 10, 0)], discount=20.0, service=6.0)
    bill = m.pos_bill(conn, oid_two)
    s.check("a discount that takes the rest leaves the service on what was left to pay",
            abs(rows_sum(bill) - 6.0) < 0.005
            and abs(bill["vat_by_rate"][10.0]["gross"] - 6.0) < 0.005
            and bill["vat_by_rate"][20.0]["gross"] == 0,
            detail=str({k: v["gross"] for k, v in bill["vat_by_rate"].items()}))

    s.section("A bill with nothing priced on it")
    oid = ring([(0.0, 1, 10, 0), (0.0, 1, 20, 0)], service=3.0)
    bill = m.pos_bill(conn, oid)
    s.check("a service charge goes on the highest rate, which cannot under-declare",
            abs(bill["vat_by_rate"][20.0]["gross"] - 3.0) < 0.005
            and bill["vat_by_rate"][10.0]["gross"] == 0,
            detail=str({k: v["gross"] for k, v in bill["vat_by_rate"].items()}))

    s.section("A discount bigger than the bill")
    oid = ring([(20.0, 1, 10, 0), (12.0, 1, 20, 0)], discount=50.0)
    bill = m.pos_bill(conn, oid)
    s.check("the bill is nothing, and so is every rate row",
            bill["total"] == 0 and before_deposit(bill) == 0
            and all(b["gross"] == 0 and b["vat"] == 0 for b in bill["vat_by_rate"].values()),
            detail=str({k: (v["gross"], v["vat"]) for k, v in bill["vat_by_rate"].items()}))
    oid = ring([(30.0, 1, 10, 0)], deposit=50.0)
    bill = m.pos_bill(conn, oid)
    s.check("a deposit is not a reduction: an over-paid deposit leaves the VAT on the meal",
            bill["total"] == 0 and abs(rows_sum(bill) - 30.0) < 0.005,
            detail=f"total {bill['total']} rows {rows_sum(bill)}")
    conn.commit()

    # ------------------------------------------------------------------
    s.section("Settled from now on: the journal holds rows that add up")
    rng = random.Random(1610)
    settled_off = []
    for n in range(40):
        oid = ring([(round(rng.uniform(5, 90), 2), rng.randint(1, 3), 10, 0),
                    (round(rng.uniform(5, 90), 2), rng.randint(1, 2), 20, 0)],
                   discount=round(rng.uniform(0.5, 20), 2),
                   service=round(rng.uniform(0, 12), 2))
        after = settle(oid)
        issued = journalled(oid)
        if abs(round(sum(b["gross"] for b in issued.values()), 2)
               - before_deposit(after)) > 0.005:
            settled_off.append(f"#{n} {issued} vs {before_deposit(after)}")
    s.check("forty settled bills, every journal entry's rows add up to the bill",
            not settled_off, detail=f"{len(settled_off)}, e.g. {settled_off[:2]}")
    bill = m.pos_bill(conn, oid)
    s.check("and the settled bill reads its VAT back from that entry",
            bill["vat_by_rate"] == dict(sorted(journalled(oid).items())),
            detail=f"bill {bill['vat_by_rate']} journal {journalled(oid)}")

    # ------------------------------------------------------------------
    s.section("Settled before the fix: the bill keeps the rows it was issued with")
    # Find a bill the old split got wrong -- wrong enough that the VAT owed
    # moves too, not just the HT -- and settle it the way the till did before.
    # Food, wine and a line rung up with no VAT rate (which reads as 0%): two
    # rates alone almost never missed, because two shares that add up to whole
    # cents round to whole cents unless both sit on a half.
    def tva(rows):
        return round(sum(round(g - g / (1 + r / 100), 2) for r, g in rows.items()), 2)

    rng = random.Random(14)
    found = False
    for _ in range(5000):
        food, wine = round(rng.uniform(20, 160), 2), round(rng.uniform(20, 160), 2)
        other = round(rng.uniform(2, 30), 2)
        disc, serv = round(rng.uniform(1, 25), 2), round(rng.uniform(0, 15), 2)
        charged = round(food + wine + other - disc + serv, 2)
        served = {10.0: {"gross": food}, 20.0: {"gross": wine}, 0.0: {"gross": other}}
        old = _old_split(served, charged)
        if (abs(round(sum(old.values()), 2) - charged) > 0.005
                and tva(old) != tva(m.pos_split_by_rate(served, charged))):
            found = True
            break
    s.check("(a bill the old split got wrong, VAT and all, was found to settle)", found)
    oid = ring([(food, 1, 10, 0), (wine, 1, 20, 0), (other, 1, None, 0)],
               discount=disc, service=serv)
    real_split = m.pos_split_by_rate
    m.pos_split_by_rate = _old_split
    try:
        settle(oid)
    finally:
        m.pos_split_by_rate = real_split
    issued = journalled(oid)
    s.check("(the bill settled under the old split, a cent out)",
            abs(round(sum(b["gross"] for b in issued.values()), 2) - charged) > 0.005,
            detail=f"journal {issued} for {charged}")

    bill = m.pos_bill(conn, oid)
    s.check("its rows are the journal's, cent for cent, not today's arithmetic",
            bill["vat_by_rate"] == dict(sorted(issued.items())),
            detail=f"bill {bill['vat_by_rate']} journal {issued}")
    s.check("and its VAT total is the journal's too",
            abs(bill["vat_total"] - round(sum(b["vat"] for b in issued.values()), 2)) < 0.005)
    s.check("the bill's own figures are unchanged by the fix",
            abs(bill["total"] - charged) < 0.005, detail=str(bill["total"]))

    page = oc.get(f"/pos/{oid}/receipt")
    printed = {int(pct): (ht, tva) for pct, ht, tva in re.findall(
        r"TVA (\d+)% \(HT ([^)]*)\)</span>\s*<span>([^<]*)</span>",
        page.get_data(as_text=True))}
    wanted = {int(r): (m.money(b["net"]), m.money(b["vat"])) for r, b in issued.items()}
    s.check("the reprinted receipt prints the HT and TVA it was issued with, rate by rate",
            page.status_code == 200 and printed == wanted,
            page, detail=f"printed {printed} issued {wanted}")
    fresh = {}
    for r, g in real_split(served, charged).items():
        tva = round(g - g / (1 + r / 100), 2)
        fresh[int(r)] = (m.money(round(g - tva, 2)), m.money(tva))
    s.check("(which is not what today's split would print, so the check above means it)",
            fresh != wanted, detail=str(fresh))

    # The closure and the day report of the day it was settled add up the
    # journalled rows. A far-off day, closed and then rolled back, so nothing
    # this suite does is left frozen for anything after it.
    conn.execute("UPDATE pos_orders SET closed_at = ?, service_date = ? WHERE id = ?",
                 (f"{PAST_DAY}T20:00:00+00:00", PAST_DAY, oid))
    conn.commit()
    report = m.pos_day_report(conn, PAST_DAY)
    s.check("the day report's VAT is the journal's",
            abs(report["vat"] - round(sum(b["vat"] for b in issued.values()), 2)) < 0.005,
            detail=f"{report['vat']} vs {issued}")
    closure, created = m.pos_close_period(conn, "day", PAST_DAY)
    vat = m.json.loads(closure["vat_json"]) if closure else {}
    s.check("the day's closure adds up the rows the journal holds",
            created and all(abs(vat.get(str(k), {}).get("gross", -1) - b["gross"]) < 0.005
                            and abs(vat.get(str(k), {}).get("vat", -1) - b["vat"]) < 0.005
                            for k, b in issued.items()),
            detail=f"closure {vat} journal {issued}")
    conn.rollback()
    s.check("(and the closure was rolled back)",
            not conn.execute("SELECT 1 FROM pos_closures WHERE kind = 'day' AND period = ?",
                             (PAST_DAY,)).fetchone())

    s.section("Reopened, it is a bill again, and settles with rows that add up")
    resp = oc.post(f"/pos/{oid}/reopen")
    bill = m.pos_bill(conn, oid)
    s.check("reopened, its rows are worked out afresh and add up",
            resp.status_code in (302, 303)
            and bill["vat_by_rate"] != dict(sorted(issued.items()))
            and abs(rows_sum(bill) - charged) < 0.005,
            resp, detail=f"rows {rows_sum(bill)} for {charged}")
    after = settle(oid)
    bill = m.pos_bill(conn, oid)
    s.check("settled again, the new journal entry is what it reads, and it adds up",
            journalled(oid) != issued
            and bill["vat_by_rate"] == dict(sorted(journalled(oid).items()))
            and abs(rows_sum(bill) - charged) < 0.005,
            detail=f"rows {rows_sum(bill)} for {charged}")

    s.section("A journal entry that no longer describes the bill is not used")
    oid = ring([(55.0, 1, 10, 0), (38.0, 1, 20, 0)], discount=7.5)
    settle(oid)
    conn.execute("UPDATE pos_order_lines SET unit_price = 61.0 WHERE order_id = ? "
                 "AND vat_rate = 10", (oid,))
    conn.commit()
    bill = m.pos_bill(conn, oid)
    s.check("a line changed under a settled bill: its own arithmetic, which adds up",
            abs(rows_sum(bill) - before_deposit(bill)) < 0.005
            and bill["vat_by_rate"] != dict(sorted(journalled(oid).items())),
            detail=f"rows {rows_sum(bill)} for {before_deposit(bill)}")
    oid = ring([(55.0, 1, 10, 0), (38.0, 1, 20, 0)], discount=7.5)
    conn.execute("UPDATE pos_orders SET status = 'paid' WHERE id = ?", (oid,))
    conn.commit()
    bill = m.pos_bill(conn, oid)
    s.check("a paid tab with no journal entry (settled before the journal): its own arithmetic",
            abs(rows_sum(bill) - 85.5) < 0.005)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
