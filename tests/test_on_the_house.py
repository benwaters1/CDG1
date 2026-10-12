"""A meal on the house is not money taken, and the cash-up's figures agree.

WHAT WENT WRONG. A tab settled "On the house" is recorded as a payment, so the
ticket is on the record at its value -- which the law wants. Every page that
added up payments then counted it as money:
  - the cash-up put €25 of comped dinner inside "Total taken";
  - the cost-of-taking-money page put it inside "Taken", the base the
    processor's share is quoted against;
  - and the cash-up's "7 tabs, averaging €29.50 each" divided the menu-price
    total before discounts, deposits and packages, under a total of €134.50
    -- €19.21 a tab -- so the two figures could not be got from each other.
The cash-up's warning that takings and tabs disagree also left out deposits
and drinks packages, so any table that paid to book raised it falsely.

On the way: the cost-of-taking-money and cash-banking pages took the period's
end -- the day AFTER it -- as the last day, so every month counted the 1st of
the next one and a single day counted two.

WHAT THIS PINS. On a day with a discounted tab, a tab with a deposit and a tab
on the house: what was taken, what was given, the average as the total over
the tabs, no false warning, and a real one when a payment is missing. The
frozen closure still holds the comp (it is hashed and only read). Both
period pages count exactly the days asked for.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOTH-"
DAY, NEXT = "2019-03-12", "2019-03-13"


def _tab(conn, label, day, covers, price, method, paid, discount=0.0, deposit=0.0):
    at = f"{day}T19:30:00+00:00"
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state, service_date,
           settled_total, payment_method, discount_amount, deposit_credit, opened_at, closed_at)
           VALUES (?, ?, 'paid', 'bill', ?, ?, ?, ?, ?, ?, ?)""",
        (TAG + label, covers, day, paid, method, discount, deposit, at, at))
    oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
           state, vat_rate, created_at) VALUES (?, ?, ?, 1, 'main', 'served', 10, ?)""",
        (oid, TAG + label + " dish", price, at))
    conn.execute("INSERT INTO pos_payments (order_id, amount, method, created_at) "
                 "VALUES (?, ?, ?, ?)", (oid, paid, method, at))
    return oid


def _cleanup(conn):
    for table in ("pos_payments", "pos_order_lines"):
        conn.execute(f"DELETE FROM {table} WHERE order_id IN "
                     "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_closures WHERE kind = 'day' AND period IN (?, ?)", (DAY, NEXT))
    conn.execute("DELETE FROM cash_bankings WHERE reference LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("On the house")
    oc, _ec, owner, _e = clients()
    conn = db()
    _cleanup(conn)

    # Gross €125 at menu prices. €10 off the first, €20 paid at booking on the
    # second, the third given away: €70 of money, €25 given, €95 settled.
    _tab(conn, "disc", DAY, 2, 60.0, "cash", 50.0, discount=10.0)
    _tab(conn, "dep", DAY, 2, 40.0, "card_terminal", 20.0, deposit=20.0)
    _tab(conn, "comp", DAY, 1, 25.0, "comp", 25.0)
    # The next day: a €30 tab with only €13.13 recorded against it, by a method
    # the 12th does not use, so a page that strays into the 13th shows a row.
    _tab(conn, "short", NEXT, 2, 30.0, "card_link", 13.13)
    conn.commit()

    s.section("The cash-up counts money, and keeps what was given apart")
    r = m.pos_day_report(conn, DAY)
    s.check("three tabs, five covers", (r["tabs"], r["covers"]) == (3, 5),
            detail=f"{r['tabs']} tabs, {r['covers']} covers")
    s.check("taken is the money: cash and card, not the comp", r["taken"] == 70.0,
            detail=str(r["taken"]))
    s.check("what was given is its own figure", r["comped"] == 25.0, detail=str(r["comped"]))
    s.check("and the two together are what settled the tabs",
            r["settled"] == r["owed"] == 95.0, detail=f"{r['settled']} vs {r['owed']}")
    s.check("the average per tab is the total over the tabs",
            r["average_per_tab"] == round(70.0 / 3, 2), detail=str(r["average_per_tab"]))
    s.check("and per cover the same way", r["average_per_cover"] == 14.0,
            detail=str(r["average_per_cover"]))

    page = oc.get(f"/pos/day?date={DAY}").get_data(as_text=True)
    total_row = page[page.find("Total taken"):page.find("Total taken") + 120]
    s.check("the page's total taken is the money", m.money(70) in total_row, detail=total_row)
    above = page[page.find("Takings by method"):page.find("Total taken")]
    s.check("the comp is not among the methods that make up the total",
            m.POS_PAYMENT_METHODS["comp"] not in above and m.money(25) not in above,
            detail=above[-300:])
    s.check("it sits beneath it, said to be given, not taken",
            "given, not taken" in page and m.money(25) in page)
    s.check("the average divides the total shown",
            f"averaging {m.money(70.0 / 3)} taken each" in page,
            detail=page[page.find("averaging"):page.find("averaging") + 60])
    s.check("the tiles are to the cent, as the table is",
            m.money(14) in page, detail="average per cover tile")
    s.check("a deposit paid at booking raises no warning",
            "settled against them" not in page and "do not match" not in page)

    s.section("A missing payment still raises the warning")
    short = oc.get(f"/pos/day?date={NEXT}").get_data(as_text=True)
    s.check("the tab that came to €30 with €13.13 against it is named as wrong",
            "settled against them" in short and m.money(30) in short
            and m.money(13.13) in short,
            detail=short[short.find("alert-band"):short.find("alert-band") + 200])

    s.section("A closed day reads the same, and its record is not touched")
    closure, created = m.pos_close_period(conn, "day", DAY, owner["id"])
    conn.commit()
    s.check("the day closes", created)
    s.check("the hashed record still holds every ticket at its value, comp included",
            closure["taken_total"] == 95.0, detail=str(closure["taken_total"]))
    closed = oc.get(f"/pos/day?date={DAY}").get_data(as_text=True)
    s.check("while the closed page says the money taken",
            f"fixed — {m.money(70)} taken" in closed,
            detail=closed[closed.find("fixed"):closed.find("fixed") + 60])
    s.check("and its average agrees with it",
            f"averaging {m.money(70.0 / 3)} taken each" in closed)

    s.section("The cost of taking money leaves the comp out")
    cost = m.cost_of_taking_money(conn, m.parse_date(DAY), m.parse_date(DAY))
    s.check("no row for it", not any(x["method"] == "comp" for x in cost["rows"]),
            detail=str([x["method"] for x in cost["rows"]]))
    s.check("taken is the money", cost["taken"] == 70.0, detail=str(cost["taken"]))
    s.check("and the comp is counted apart",
            cost["comped"] == {"amount": 25.0, "count": 1}, detail=str(cost["comped"]))
    page = oc.get(f"/management/cost-of-taking-money?period=day&date={DAY}").get_data(as_text=True)
    s.check("the page says what was left out and why", "settled on the house" in page)
    s.check("a money column holds money, not the word nothing", ">nothing<" not in page)

    s.section("A period's pages count the days asked for")
    s.check("the next day's payment is not on this day's cost page",
            m.money(13.13) not in page, detail="the €13.13 of the 13th")
    nxt = oc.get(f"/management/cost-of-taking-money?period=day&date={NEXT}").get_data(as_text=True)
    s.check("and is on its own", m.money(13.13) in nxt)
    conn.execute("INSERT INTO cash_bankings (banked_on, amount, reference, created_at) "
                 "VALUES (?, 777.77, ?, ?)", (NEXT, TAG + "bank", _harness.datetime_now()))
    conn.commit()
    banked = oc.get(f"/management/cash-banking?period=day&date={DAY}").get_data(as_text=True)
    s.check("cash banked on the 13th is not on the 12th's page", m.money(777.77) not in banked)
    banked = oc.get(f"/management/cash-banking?period=day&date={NEXT}").get_data(as_text=True)
    s.check("and is on the 13th's", m.money(777.77) in banked)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
