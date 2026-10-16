"""A drinks package's own price reaches the bill.

A package can carry a price: "Accord mets et vins, 40 EUR per person" covers
the wines on its list and is charged instead of them. Until 16 Oct 2026 the
price was worked out, netted against what the package covered into
pos_orders.package_credit, and read by nothing, so a 40 EUR pairing covering
one 30 EUR bottle billed nothing at all, and the flash said "-10.00 covered".

What this holds:
  - the price is a row of its own on the bill, the till page, the printed
    note and the emailed copy, and the total includes it;
  - per cover or per table as the package says, for both shapes, and a
    per-cover price follows the covers when the party changes;
  - it is taxed at the highest rate among the drinks the package can cover,
    and the bill's VAT rows still add up to its total;
  - it is stored on the tab, so a reprint after the price changes still says
    what was charged;
  - package_set and tab_settled journal it, and the cash-up, the day's
    closure, the archive and what goes to the accountant all count it.
"""
import csv
import io
import json
import re
import zipfile
from datetime import date

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "pkgp-"
# A service day nothing else in the suite will settle a tab on, so the
# closure counts only what this file put there.
DAY = "2096-05-14"


def _cleanup(conn):
    orders = "(SELECT id FROM pos_orders WHERE table_label LIKE ?)"
    conn.execute(f"DELETE FROM pos_payments WHERE order_id IN {orders}", (TAG + "%",))
    conn.execute(f"DELETE FROM pos_order_lines WHERE order_id IN {orders}", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_closures WHERE period = ?", (DAY,))
    conn.execute("DELETE FROM drink_package_items WHERE package_id IN "
                 "(SELECT id FROM drink_packages WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM drink_packages WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _item(conn, name, price, course, now):
    """A drink or dish on the standing list. No vat_rate of its own, so the
    till gives it the rate its course carries: wine 20%, soft drinks and
    food 10%."""
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, always_available, sort_order, created_at)
           VALUES (?, ?, ?, ?, 1, 1, 1, 1, 0, ?)""",
        (name, "main" if course == "main" else "drink", course, price, now))
    conn.commit()
    return conn.execute("SELECT id FROM menu_items WHERE name = ?", (name,)).fetchone()["id"]


def _package(oc, conn, name, kind, price, per="cover", allowance=None, items=()):
    data = {"name": name, "kind": kind, "price": str(price), "per": per,
            "disclosure": "Boissons comprises"}
    if allowance is not None:
        data["allowance_amount"] = str(allowance)
    oc.post("/admin/restaurant/packages/new", data=data)
    row = conn.execute("SELECT * FROM drink_packages WHERE name = ?", (name,)).fetchone()
    for item_id in items:
        oc.post(f"/admin/restaurant/packages/{row['id']}/item", data={"menu_item_id": item_id})
    return row


def _tab(oc, conn, label, covers):
    oc.post("/pos/open", data={"table_label": label, "covers": str(covers)})
    return conn.execute("SELECT * FROM pos_orders WHERE table_label = ?", (label,)).fetchone()


def _ring(oc, order_id, item_id, quantity=1):
    oc.post(f"/pos/{order_id}/add", data={"menu_item_id": item_id, "quantity": str(quantity)})


def _rows_add_up(bill):
    """The bill's VAT rows against its total. A deposit is money already paid
    against the same meal, so it is inside the rows and outside the total."""
    rows = round(sum(b["gross"] for b in bill["vat_by_rate"].values()), 2)
    return abs(rows - round(bill["total"] + bill["deposit"], 2)) < 0.011, rows


def _till_rows(page):
    """The till page's bill as (label, amount) pairs, whitespace closed up."""
    return [(" ".join(label.split()), " ".join(amount.split())) for label, amount in
            re.findall(r'<div class="till-total-row[^"]*">\s*<span>(.*?)</span>\s*'
                       r'<span>(.*?)</span>', page, re.S)]


def _package_entries(conn, order_id):
    return [json.loads(r["payload"]) for r in conn.execute(
        "SELECT payload FROM pos_journal WHERE event_type = 'package_set' AND order_id = ? "
        "ORDER BY sequence", (order_id,)).fetchall()]


def run():
    s = Suite("A drinks package's own price reaches the bill")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    _cleanup(conn)
    alcohol = m.tax_rate(conn, "vat_alcohol")
    food = m.tax_rate(conn, "vat_food")

    bottle = _item(conn, TAG + "Madiran", 30.0, "wine", now)
    juice = _item(conn, TAG + "Jus de pomme", 4.0, "drink", now)
    magret = _item(conn, TAG + "Magret", 32.0, "main", now)

    # ------------------------------------------------------------------
    s.section("A EUR 40 pairing covering a EUR 30 bottle bills EUR 40, not nothing")
    pairing = _package(oc, conn, TAG + "Accord mets et vins", "fixed", 40, items=[bottle])
    t1 = _tab(oc, conn, TAG + "1", 1)
    _ring(oc, t1["id"], bottle)
    r = oc.post(f"/pos/{t1['id']}/package", data={"package_id": pairing["id"]},
                follow_redirects=True)
    bill = m.pos_bill(conn, t1["id"])
    s.check("the bottle is covered by the pairing", bill["package"] == 30.0,
            detail=str(bill["package"]))
    s.check("the pairing's own price is on the bill", bill["package_charge"] == 40.0,
            detail=str(bill["package_charge"]))
    s.check("so the table owes EUR 40", bill["total"] == 40.0, detail=str(bill["total"]))
    s.check("the price is stored on the tab, not read off the package at print time",
            conn.execute("SELECT package_charge FROM pos_orders WHERE id = ?",
                         (t1["id"],)).fetchone()["package_charge"] == 40.0)
    s.check("package_credit now holds what was covered, not covered less the price",
            conn.execute("SELECT package_credit FROM pos_orders WHERE id = ?",
                         (t1["id"],)).fetchone()["package_credit"] == 30.0)
    flashes = " ".join(_harness.flashes(r))
    s.check("the till says what the package costs and what it covered",
            m.money(40) in flashes and m.money(30) in flashes and "−" not in flashes,
            detail=flashes)

    s.section("Taxed at the wine's rate, and the VAT rows add up to the total")
    s.check("a pairing of wine is billed at the alcohol rate",
            bill["package_charge_rate"] == alcohol, detail=str(bill["package_charge_rate"]))
    s.check("the whole EUR 40 is in the alcohol band",
            round(bill["vat_by_rate"].get(alcohol, {}).get("gross", 0), 2) == 40.0,
            detail=str(bill["vat_by_rate"]))
    ok, rows = _rows_add_up(bill)
    s.check("the VAT rows add up to the total", ok, detail=f"rows {rows} vs {bill['total']}")

    s.section("On the till page and on the printed note, as a row of its own")
    page = oc.get(f"/pos/{t1['id']}").get_data(as_text=True)
    # Row by row: the package list and its hint quote the price too, the total
    # is also EUR 40, and the covered row carries the name, so a check that
    # read the page as one string passed with the price row taken out.
    rows = _till_rows(page)
    s.check("the till's bill has the package's price as a row of its own",
            (TAG + "Accord mets et vins", "€40.00") in rows, detail=str(rows))
    s.check("and the package list says it is charged before anyone picks it",
            "charged €40.00 per cover" in page)
    note = oc.get(f"/pos/{t1['id']}/receipt").get_data(as_text=True)
    totals = note.split("Sous-total", 1)[-1]
    s.check("the note has the price as a row in its totals",
            TAG + "Accord mets et vins" in totals and m.money(40) in totals.split("compris")[0],
            detail=totals[:400])
    s.check("and still says what the package covered", "compris" in totals)

    s.section("Journalled when the package is put on")
    entries = _package_entries(conn, t1["id"])
    last = entries[-1] if entries else {}
    s.check("package_set records the price", last.get("charge") == 40.0, detail=str(last))
    s.check("and what was covered, as covered", last.get("covered") == 30.0, detail=str(last))
    s.check("and what the price was before", last.get("charge_was") == 0, detail=str(last))

    # ------------------------------------------------------------------
    s.section("A per-cover price follows the covers")
    oc.post(f"/pos/{t1['id']}/adjust", data={"kind": "covers", "covers": "3"})
    bill = m.pos_bill(conn, t1["id"])
    s.check("three covers owe three pairings", bill["package_charge"] == 120.0,
            detail=str(bill["package_charge"]))
    s.check("and the total moves with it", bill["total"] == 120.0, detail=str(bill["total"]))
    last = (_package_entries(conn, t1["id"]) or [{}])[-1]
    s.check("the change of price is journalled, with the covers it was sized on",
            last.get("charge") == 120.0 and last.get("charge_was") == 40.0
            and last.get("covers") == 3, detail=str(last))
    note = oc.get(f"/pos/{t1['id']}/receipt").get_data(as_text=True)
    s.check("the note says how the price is made up",
            f"3 × {m.money(40)}" in note, detail=note.split("Sous-total", 1)[-1][:300])
    oc.post(f"/pos/{t1['id']}/adjust", data={"kind": "covers", "covers": "2"})
    s.check("and back down when the party shrinks",
            m.pos_bill(conn, t1["id"])["package_charge"] == 80.0)
    before = len(_package_entries(conn, t1["id"]))
    oc.post(f"/pos/{t1['id']}/adjust", data={"kind": "covers", "covers": "2"})
    s.check("covers set to what they already were writes nothing to the journal",
            len(_package_entries(conn, t1["id"])) == before)

    s.section("Taking the package off takes its price off")
    oc.post(f"/pos/{t1['id']}/package", data={"package_id": ""})
    bill = m.pos_bill(conn, t1["id"])
    s.check("nothing charged for a package the table no longer has",
            bill["package_charge"] == 0.0 and bill["total"] == bill["gross"],
            detail=f"{bill['package_charge']} / {bill['total']} vs {bill['gross']}")
    s.check("and nothing on the till page for it",
            "on the bill for the package" not in oc.get(f"/pos/{t1['id']}").get_data(as_text=True))

    # ------------------------------------------------------------------
    s.section("An allowance with a price charges it per cover too")
    formule = _package(oc, conn, TAG + "Formule boisson", "allowance", 20, allowance=25)
    t2 = _tab(oc, conn, TAG + "2", 2)
    _ring(oc, t2["id"], bottle)
    _ring(oc, t2["id"], juice)
    _ring(oc, t2["id"], magret)
    oc.post(f"/pos/{t2['id']}/package", data={"package_id": formule["id"]})
    bill = m.pos_bill(conn, t2["id"])
    # EUR 50 of allowance covers the bottle and the juice; the magret is food.
    s.check("the allowance covers the drinks", bill["package"] == 34.0,
            detail=str(bill["package"]))
    s.check("the price is per cover, as the admin page says: 2 x EUR 20",
            bill["package_charge"] == 40.0, detail=str(bill["package_charge"]))
    s.check("so the table owes the magret and the package",
            bill["total"] == 72.0, detail=str(bill["total"]))
    s.check("an allowance can go on wine, so it carries the alcohol rate",
            bill["package_charge_rate"] == alcohol, detail=str(bill["package_charge_rate"]))
    s.check("the juice it covered still comes off the soft-drink rate it was rung at",
            round(bill["vat_by_rate"].get(food, {}).get("gross", 0), 2) == 32.0,
            detail=str(bill["vat_by_rate"]))
    ok, rows = _rows_add_up(bill)
    s.check("the VAT rows add up to the total", ok, detail=f"rows {rows} vs {bill['total']}")

    s.section("A per-table price is charged once")
    per_table = _package(oc, conn, TAG + "Bouteille pour la table", "allowance", 35,
                         per="table", allowance=30)
    oc.post(f"/pos/{t2['id']}/package", data={"package_id": per_table["id"]})
    s.check("once, whatever the covers", m.pos_bill(conn, t2["id"])["package_charge"] == 35.0,
            detail=str(m.pos_bill(conn, t2["id"])["package_charge"]))
    oc.post(f"/pos/{t2['id']}/adjust", data={"kind": "covers", "covers": "4"})
    s.check("and still once when the party grows",
            m.pos_bill(conn, t2["id"])["package_charge"] == 35.0)

    s.section("A discount reduces the package's price in proportion, and the rows add up")
    t3 = _tab(oc, conn, TAG + "3", 1)
    _ring(oc, t3["id"], bottle)
    _ring(oc, t3["id"], magret)
    oc.post(f"/pos/{t3['id']}/package", data={"package_id": pairing["id"]})
    oc.post(f"/pos/{t3['id']}/adjust",
            data={"kind": "discount", "amount": "10", "reason": TAG + "Long wait"})
    bill = m.pos_bill(conn, t3["id"])
    s.check("30 + 32, less the covered bottle, plus the pairing, less 10",
            bill["total"] == 62.0, detail=str(bill["total"]))
    ok, rows = _rows_add_up(bill)
    s.check("the VAT rows add up to the total", ok, detail=f"rows {rows} vs {bill['total']}")
    s.check("the discount is shared with the package, not all taken off the food",
            round(bill["vat_by_rate"].get(food, {}).get("gross", 0), 2) == 27.56,
            detail=str(bill["vat_by_rate"]))

    # ------------------------------------------------------------------
    s.section("The rate follows what the package can cover")
    softs = _package(oc, conn, TAG + "Accord sans alcool", "fixed", 12, per="table",
                     items=[juice])
    mixed = _package(oc, conn, TAG + "Accord complet", "fixed", 15, per="table",
                     items=[juice, bottle])
    s.check("a list of soft drinks is a soft-drink supply",
            m.package_vat_rate(conn, softs) == food, detail=str(m.package_vat_rate(conn, softs)))
    s.check("one price over wine and juice takes the higher rate",
            m.package_vat_rate(conn, mixed) == alcohol,
            detail=str(m.package_vat_rate(conn, mixed)))
    t4 = _tab(oc, conn, TAG + "4", 2)
    _ring(oc, t4["id"], juice, 2)
    oc.post(f"/pos/{t4['id']}/package", data={"package_id": softs["id"]})
    bill = m.pos_bill(conn, t4["id"])
    s.check("so a juice pairing bills its price at the soft-drink rate",
            bill["package_charge_rate"] == food
            and round(bill["vat_by_rate"].get(food, {}).get("gross", 0), 2) == 12.0
            and alcohol not in bill["vat_by_rate"], detail=str(bill["vat_by_rate"]))

    s.section("A package with no price of its own adds nothing")
    # "Leave at 0 when it is bundled into the menu price", says the admin page.
    bundled = _package(oc, conn, TAG + "Vin compris", "fixed", 0, items=[bottle])
    t6 = _tab(oc, conn, TAG + "6", 2)
    _ring(oc, t6["id"], bottle)
    oc.post(f"/pos/{t6['id']}/package", data={"package_id": bundled["id"]})
    bill = m.pos_bill(conn, t6["id"])
    s.check("bundled into the menu price, it covers the bottle and charges nothing",
            bill["package"] == 30.0 and bill["package_charge"] == 0.0 and bill["total"] == 0.0,
            detail=f"{bill['package']} / {bill['package_charge']} / {bill['total']}")
    s.check("and stamps no VAT rate for a price that is not there",
            conn.execute("SELECT package_vat_rate FROM pos_orders WHERE id = ?",
                         (t6["id"],)).fetchone()["package_vat_rate"] is None)
    totals = oc.get(f"/pos/{t6['id']}/receipt").get_data(as_text=True).split("Sous-total", 1)[-1]
    s.check("and the note names it once, for what it covered, with no price row",
            totals.count(TAG + "Vin compris") == 1, detail=totals[:400])

    # ------------------------------------------------------------------
    s.section("Settled: the journal entry adds up")
    t5 = _tab(oc, conn, TAG + "5", 2)
    _ring(oc, t5["id"], bottle, 2)
    _ring(oc, t5["id"], magret, 2)
    oc.post(f"/pos/{t5['id']}/package", data={"package_id": pairing["id"]})
    bill = m.pos_bill(conn, t5["id"])
    # 60 of wine + 64 of magret, less the 60 covered, plus 2 x 40.
    s.check("two covers on the pairing owe 144", bill["total"] == 144.0,
            detail=str(bill["total"]))
    oc.post(f"/pos/{t5['id']}/pay", data={"method": "cash", "amount": "144"})
    settled = conn.execute("SELECT * FROM pos_orders WHERE id = ?", (t5["id"],)).fetchone()
    s.check("paying 144 settles it", settled["status"] == "paid"
            and settled["settled_total"] == 144.0,
            detail=f"{settled['status']} {settled['settled_total']}")
    entry = conn.execute(
        "SELECT payload FROM pos_journal WHERE event_type = 'tab_settled' AND order_id = ?",
        (t5["id"],)).fetchone()
    entry = json.loads(entry["payload"]) if entry else {}
    s.check("tab_settled records the package's price and what it covered",
            entry.get("package_charge") == 80.0 and entry.get("package_covered") == 60.0,
            detail=str(entry))
    s.check("and its figures add up to its total",
            bool(entry) and round(entry["gross"] - entry["package_covered"]
                                  + entry["package_charge"] - entry["discount"]
                                  + entry["service"] - entry["deposit"], 2) == entry["total"],
            detail=str(entry))
    s.check("the journal still verifies", m.pos_journal_verify(conn) is None,
            detail=str(m.pos_journal_verify(conn)))

    s.section("A reprint says what was charged, whatever the package costs now")
    conn.execute("UPDATE drink_packages SET price = 50 WHERE id = ?", (pairing["id"],))
    conn.commit()
    again = m.pos_bill(conn, t5["id"])
    s.check("the settled bill still holds 80 for the pairing",
            again["package_charge"] == 80.0 and again["total"] == 144.0,
            detail=f"{again['package_charge']} / {again['total']}")
    note = oc.get(f"/pos/{t5['id']}/receipt").get_data(as_text=True)
    s.check("and the words beside it still say 2 x 40",
            f"2 × {m.money(40)}" in note, detail=note.split("Sous-total", 1)[-1][:300])
    conn.execute("UPDATE drink_packages SET price = 40 WHERE id = ?", (pairing["id"],))
    conn.commit()

    s.section("The emailed copy has it too")
    ctx = m.pos_receipt_email_context(conn, m.pos_bill(conn, t5["id"]))
    s.check("the price as a row", TAG + "Accord mets et vins (2 x €40.00)   €80.00"
            in ctx["totals"], detail=ctx["totals"])
    s.check("and what it covered, so the lines above it add up",
            "-€60.00" in ctx["totals"], detail=ctx["totals"])

    # ------------------------------------------------------------------
    s.section("The cash-up and the day's closure count it")
    # Moved to a day of its own, so the figures below are this tab's alone.
    late = f"{DAY}T20:00:00+00:00"
    conn.execute("UPDATE pos_orders SET opened_at = ?, closed_at = ? WHERE id = ?",
                 (late, late, t5["id"]))
    conn.execute("UPDATE pos_payments SET created_at = ? WHERE order_id = ?", (late, t5["id"]))
    conn.commit()
    report = m.pos_day_report(conn, date.fromisoformat(DAY))
    s.check("the cash-up counts what the packages charged",
            report["package_charged"] == 80.0, detail=str(report.get("package_charged")))
    s.check("and what they covered", report["package_covered"] == 60.0,
            detail=str(report.get("package_covered")))
    s.check("the tabs came to what was taken", report["owed"] == report["taken"] == 144.0,
            detail=f"owed {report['owed']} taken {report['taken']}")
    page = oc.get(f"/pos/day?date={DAY}").get_data(as_text=True)
    s.check("the cash-up page shows both", "Packages charged" in page
            and "€80.00" in page and "Drinks covered" in page and "€60.00" in page)
    s.check("and does not say the tabs and the takings disagree",
            "settled without a payment" not in page)

    closure, created = m.pos_close_period(conn, "day", DAY)
    conn.commit()
    s.check("the day closes", created)
    s.check("the closure keeps what the packages charged and covered",
            closure["package_charged_total"] == 80.0 and closure["package_covered_total"] == 60.0,
            detail=f"{closure['package_charged_total']} / {closure['package_covered_total']}")
    vat = json.loads(closure["vat_json"])
    sealed = round(sum(b["gross"] for b in vat.values()), 2)
    s.check("its sealed VAT includes the pairing's price", sealed == 144.0, detail=str(vat))
    s.check("and its own figures reconcile to it: gross, less covered, plus charged",
            round(closure["gross_total"] - closure["package_covered_total"]
                  + closure["package_charged_total"] - closure["discount_total"]
                  + closure["service_total"], 2) == sealed,
            detail=f"{closure['gross_total']} -> {sealed}")
    page = oc.get(f"/pos/day?date={DAY}").get_data(as_text=True)
    s.check("the closed day shows the frozen figures", "Packages charged" in page
            and "€80.00" in page)

    s.section("And what goes to the accountant")
    lines = m.pos_day_pennylane_lines(conn, closure)
    sent = round(sum(float(l["currency_amount"]) + float(l["currency_tax"]) for l in lines), 2)
    s.check("the day's invoice lines come to 144", sent == 144.0, detail=str(lines))
    s.check("the page offering to send the day states the same figure",
            m.pos_closure_sales(closure) == 144.0, detail=str(m.pos_closure_sales(closure)))
    page = oc.get("/management/revenue-to-send").get_data(as_text=True)
    s.check("Revenue to send renders it", "€144.00" in page)

    s.section("And in the archive an inspector reads")
    data, _counts = m.pos_archive_bundle(conn, int(DAY[:4]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        rows = list(csv.DictReader(io.StringIO(zf.read("clotures.csv").decode("utf-8"))))
        notice = zf.read("notice.txt").decode("utf-8")
    day_row = next((r for r in rows if r["period"] == DAY), {})
    s.check("clotures.csv carries both package figures",
            day_row.get("package_charged_total") == "80.0"
            and day_row.get("package_covered_total") == "60.0", detail=str(day_row))
    s.check("and the notice says what they are", "package_charged_total" in notice)

    s.section("Guards")
    s.check("an employee can see the till page with the row on it",
            ec.get(f"/pos/{t5['id']}").status_code == 200)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
