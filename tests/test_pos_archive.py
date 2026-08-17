"""The archive: a year of the till, readable without this app.

The point of an open-format archive is that somebody can verify it in 2032 on a
machine that has never heard of this software. So the test that matters is not
"a zip came back" — it is that the chain in the exported CSV recomputes using
nothing but the CSV itself and the rule written in the notice.
"""
import csv
import hashlib
import io
import zipfile
from datetime import datetime, timezone

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "arch-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Till archive")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    year = str(datetime.now(timezone.utc).year)
    _cleanup(conn)

    # A tab with a sale and a payment, so the archive has something in it.
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, always_available, sort_order, created_at)
           VALUES (?, 'main', 'main', 24.0, 1, 1, 1, 1, 0, ?)""", (TAG + "Plat", now))
    conn.commit()
    dish = conn.execute("SELECT id FROM menu_items WHERE name = ?", (TAG + "Plat",)).fetchone()["id"]
    oc.post("/pos/open", data={"table_label": TAG + "1", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "1",)).fetchone()
    oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": dish}, follow_redirects=True)
    oc.post(f"/pos/{order['id']}/pay", data={"method": "cash"}, follow_redirects=True)

    s.section("The bundle")
    r = oc.get(f"/admin/pos/archive?year={year}")
    s.check("a zip comes back", r.status_code == 200
            and r.headers.get("Content-Type", "").startswith("application/zip"),
            detail=f"HTTP {r.status_code} {r.headers.get('Content-Type')}")
    s.check("named for the year", year in r.headers.get("Content-Disposition", ""),
            detail=r.headers.get("Content-Disposition"))

    bundle = zipfile.ZipFile(io.BytesIO(r.data))
    names = set(bundle.namelist())
    for wanted in ("notice.txt", "journal.csv", "clotures.csv", "tickets.csv", "lignes.csv"):
        s.check(f"{wanted} is in it", wanted in names, detail=str(sorted(names)))

    s.section("The notice explains how to check it")
    notice = bundle.read("notice.txt").decode("utf-8")
    # In French, because that is who reads it.
    s.check("it is written in French", "CONSERVATION" in notice and "TVA" in notice)
    s.check("it states the retention period", "L. 102 B" in notice)
    s.check("it cites the obligation", "286" in notice)
    s.check("it gives the hash rule, not just a claim of integrity",
            "sha256" in notice, detail=notice[:200])
    s.check("and reports the chain state at export time",
            "CHAINE VERIFIEE" in notice or "ANOMALIE" in notice)

    s.section("The exported chain recomputes from the CSV alone")
    # This is the whole point. Nothing below touches the database or the app —
    # it is the rule from the notice, applied to the file.
    rows = list(csv.DictReader(io.StringIO(bundle.read("journal.csv").decode("utf-8"))))
    s.check("the journal has entries", len(rows) >= 2, detail=str(len(rows)))

    broken_at = None
    for row in rows:
        digest = hashlib.sha256(
            f"{row['prev_hash']}|{row['sequence']}|{row['event_type']}|{row['payload']}"
            .encode("utf-8")).hexdigest()
        if digest != row["hash"]:
            broken_at = row["sequence"]
            break
    s.check("every row's hash recomputes from its own contents", broken_at is None,
            detail=f"first mismatch at sequence {broken_at}")

    links_ok = all(rows[i]["prev_hash"] == rows[i - 1]["hash"] for i in range(1, len(rows)))
    s.check("and each row links to the one before it", links_ok)

    # And it must FAIL when tampered with, or the check above proves nothing.
    if rows:
        tampered = dict(rows[0])
        tampered["payload"] = '{"amount":999999}'
        redigest = hashlib.sha256(
            f"{tampered['prev_hash']}|{tampered['sequence']}|{tampered['event_type']}"
            f"|{tampered['payload']}".encode("utf-8")).hexdigest()
        s.check("altering an exported row breaks its hash — so the check is real",
                redigest != rows[0]["hash"])

    s.section("The tickets and their lines are there")
    tickets = list(csv.DictReader(io.StringIO(bundle.read("tickets.csv").decode("utf-8"))))
    s.check("the settled tab is in tickets.csv",
            any(t["table_label"] == TAG + "1" for t in tickets),
            detail=str([t["table_label"] for t in tickets][:5]))
    s.check("carrying its receipt number",
            all(t["receipt_number"] for t in tickets))
    lines = list(csv.DictReader(io.StringIO(bundle.read("lignes.csv").decode("utf-8"))))
    ours = [l for l in lines if l["name"] == TAG + "Plat"]
    s.check("the line detail is there", bool(ours))
    # A line without its rate is not an archive an inspector can use.
    s.check("with the VAT rate on each line",
            all(l["vat_rate"] for l in ours), detail=str(ours[:1]))

    s.section("Taking an archive is itself recorded")
    logged = conn.execute(
        "SELECT * FROM pos_journal WHERE event_type = 'archive_exported' "
        "ORDER BY sequence DESC LIMIT 1").fetchone()
    s.check("the export appears in the journal", bool(logged))
    s.check("naming the year", bool(logged) and year in (logged["payload"] or ""),
            detail=str(logged["payload"] if logged else None))
    s.check("and in the audit log",
            bool(conn.execute(
                "SELECT 1 FROM audit_log WHERE action = 'pos_archive_exported'").fetchone()))
    s.check("the journal still verifies afterwards", m.pos_journal_verify(conn) is None,
            detail=str(m.pos_journal_verify(conn)))

    s.section("Guards")
    s.check("the index page renders", oc.get("/admin/pos/archive").status_code == 200)
    s.check("it offers the year that has data",
            year in oc.get("/admin/pos/archive").get_data(as_text=True))
    s.check("a nonsense year is refused",
            oc.get("/admin/pos/archive?year=not-a-year").status_code == 400)
    s.check("an employee cannot download the archive",
            ec.get(f"/admin/pos/archive?year={year}").status_code in (302, 403))
    # A year with nothing in it should give an empty archive, not an error —
    # "there were no takings" is a legitimate answer to an inspector.
    empty = oc.get("/admin/pos/archive?year=1999")
    s.check("a year with no trade still produces a valid archive",
            empty.status_code == 200
            and "notice.txt" in zipfile.ZipFile(io.BytesIO(empty.data)).namelist())

    _cleanup(conn)
    conn.close()
    return s
