"""Where each kind of income lands, in the owner's own words.

The mapping page and the senders used two different sets of keys. The page wrote
revenue_account_<stream> into app_settings; the senders read
pennylane_account_<something-else>. Both halves worked. Neither met. So the owner
could map every stream, watch the page report six of six mapped, and every
invoice still went to the accountant with no ledger account on it — the same
shape as a column read and never written, one level up.

Fixed by there being ONE list, in a table, in their language: nightly, F&B,
transport, extras, workshops, events, city tax, taxes. The key behind each row
is what the app maps a stream to and never changes; the label is theirs to
rename, because they said they would rename things on Pennylane's side. That
separation is what most of these checks are about — renaming F&B must not unhook
the till from its account.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRVC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM revenue_categories WHERE builtin = 0 AND label LIKE ?",
                 (TAG + "%",))
    conn.commit()
    conn.close()


def _cats():
    conn = db()
    try:
        return {c["key"]: c for c in m.revenue_categories(conn, include_inactive=True)}
    finally:
        conn.close()


def _account(key):
    conn = db()
    try:
        return m.revenue_account_for(conn, key)
    finally:
        conn.close()


def _catalogue_extra(name, category, *, price=90.0, revenue=None):
    conn = db()
    conn.execute(
        "INSERT INTO extras (name, price, category, revenue_category, active) "
        "VALUES (?, ?, ?, ?, 1)", (f"{TAG} {name}", price, category, revenue))
    conn.commit()
    row = conn.execute("SELECT * FROM extras WHERE name = ?",
                       (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _stay_with(extras, *, city_tax=4.80):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = house_today() + timedelta(days=12)
    departure = arrival + timedelta(days=2)
    priced = m.compute_room_total(conn, room, arrival, departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?, 0, ?, ?)""",
        (room["id"], f"{TAG}-1", f"tok{TAG}1".lower(), f"{TAG} Guest",
         f"{TAG.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), priced, city_tax,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-1",)).fetchone()
    for extra in extras:
        m.add_booking_extra(conn, "room", booking["id"], extra, 1)
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-1",)).fetchone()
    conn.close()
    return booking


def _lines(booking):
    conn = db()
    try:
        return m.booking_pennylane_lines(conn, m.guest_statement(conn, booking))
    finally:
        conn.close()


def run():
    s = Suite("Revenue categories")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("The list is the owner's, in their words")
    cats = _cats()
    for key, label in (("nightly", "Nightly"), ("fnb", "F&B"),
                       ("transport", "Transport"), ("extras", "Extras"),
                       ("workshops", "Workshops"), ("events", "Events"),
                       ("city_tax", "City tax"), ("taxes", "Taxes")):
        s.check(f"{label} is there", key in cats, detail=f"{sorted(cats)}")
        if key in cats:
            s.check(f"named {label}", cats[key]["label"] == label,
                    detail=f"{cats[key]['label']!r}")

    s.section("The page writes what the senders read")
    # The whole bug in one section. Map an account on the page, then ask a
    # sender what account it will use.
    form = {f"revenue_{k}": "" for k in cats}
    form.update({"revenue_nightly": "706100", "revenue_transport": "706400",
                 "revenue_extras": "706300", "revenue_fnb": "706200",
                 "revenue_city_tax": "447100"})
    r = oc.post("/admin/pennylane", data=form, follow_redirects=True)
    s.check("the page saves", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and a sender sees the same account", _account("nightly") == "706100",
            detail=f"{_account('nightly')!r} — the page wrote one set of keys and "
                   "the senders read another, so every invoice went out uncoded")
    s.check("for every one of them",
            all(_account(k) == v for k, v in
                (("transport", "706400"), ("extras", "706300"),
                 ("fnb", "706200"), ("city_tax", "447100"))),
            detail=f"{[(k, _account(k)) for k in ('transport','extras','fnb','city_tax')]}")

    s.section("A transfer is transport and a hamper is not")
    transfer = _catalogue_extra("station transfer", "transfer", price=90)
    hamper = _catalogue_extra("hamper", "food", price=60)
    booking = _stay_with([transfer, hamper])
    lines = _lines(booking)
    by_label = {l["label"].split(" at ")[0]: l for l in lines}
    s.check("the transfer has its own line", "Transport" in by_label,
            detail=f"{sorted(by_label)}")
    s.check("and goes to the transport account",
            by_label.get("Transport", {}).get("ledger_account_number") == "706400",
            detail=f"{by_label.get('Transport', {}).get('ledger_account_number')}")
    s.check("the hamper stays in extras",
            by_label.get("Extras", {}).get("ledger_account_number") == "706300",
            detail=f"{by_label.get('Extras', {}).get('ledger_account_number')}")
    s.check("the room is nightly",
            by_label.get("Nightly", {}).get("ledger_account_number") == "706100",
            detail=f"{sorted(by_label)}")
    total = round(sum(float(l["currency_amount"]) + float(l["currency_tax"])
                      for l in lines), 2)
    conn = db()
    statement = m.guest_statement(conn, booking)
    conn.close()
    s.check("and it all still adds up to the statement",
            abs(total - statement["total"]) < 0.005,
            detail=f"{total} vs {statement['total']}")

    s.section("Renaming one does not unhook it")
    # They said they would rename things on Pennylane's side. The key is what
    # the app holds; the label is theirs.
    form.update({"label_transport": f"{TAG} Chauffeur"})
    oc.post("/admin/pennylane", data=form, follow_redirects=True)
    s.check("the name changes", _cats()["transport"]["label"] == f"{TAG} Chauffeur",
            detail=f"{_cats()['transport']['label']!r}")
    s.check("the account is untouched", _account("transport") == "706400",
            detail=f"{_account('transport')!r}")
    lines = _lines(booking)
    renamed = [l for l in lines if l["label"].startswith(f"{TAG} Chauffeur")]
    s.check("and the transfer still posts there",
            renamed and renamed[0].get("ledger_account_number") == "706400",
            detail=f"{[l['label'] for l in lines]} — renaming a category unhooked "
                   "the stream that feeds it")
    conn = db()
    conn.execute("UPDATE revenue_categories SET label = 'Transport' WHERE key = 'transport'")
    conn.commit()
    conn.close()

    s.section("An account nobody set is left blank, not guessed")
    s.check("taxes has no account", not _account("taxes"),
            detail=f"{_account('taxes')!r}")
    conn = db()
    conn.execute("UPDATE revenue_categories SET ledger_account = NULL WHERE key = 'city_tax'")
    conn.commit()
    conn.close()
    city = [l for l in _lines(booking) if "tax" in l["label"].lower()]
    s.check("and its line goes without one rather than with a made-up one",
            city and "ledger_account_number" not in city[0],
            detail=f"{city[0] if city else None} — a wrong account is harder to "
                   "unpick than a missing one, because nobody notices it")

    s.section("A category of their own")
    r = oc.post("/admin/pennylane/categories/new",
                data={"label": f"{TAG} Spa", "ledger_account": "706900"},
                follow_redirects=True)
    made = [c for c in _cats().values() if c["label"] == f"{TAG} Spa"]
    s.check("it is added", len(made) == 1, detail=f"{flashes(r)[:1]}")
    if made:
        s.check("with an account", (made[0]["ledger_account"] or "") == "706900")
        s.check("and marked as theirs, not the app's", not made[0]["builtin"],
                detail="a category the app does not fill should not claim it does")
        spa_key = made[0]["key"]
        spa = _catalogue_extra("massage", "wellness", price=120, revenue=spa_key)
        conn = db()
        m.add_booking_extra(conn, "room", booking["id"], spa, 1)
        conn.commit()
        conn.close()
        lines = _lines(booking)
        spa_lines = [l for l in lines if l["label"].startswith(f"{TAG} Spa")]
        s.check("an extra pointed at it posts there",
                spa_lines and spa_lines[0].get("ledger_account_number") == "706900",
                detail=f"{[l['label'] for l in lines]} — a category nothing can "
                       "feed is a row on a settings page and nothing else")

    s.section("A built-in one cannot be hidden")
    nightly = _cats()["nightly"]
    r = oc.post(f"/admin/pennylane/categories/{nightly['id']}/toggle",
                follow_redirects=True)
    s.check("it stays on the list", _cats()["nightly"]["active"] == 1,
            detail="the app posts to it automatically; hidden, those lines go out "
                   "with no account at all")
    s.check("and says to repoint it instead",
            any("account" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("Theirs can be hidden, and nothing is deleted")
    if made:
        oc.post(f"/admin/pennylane/categories/{made[0]['id']}/toggle",
                follow_redirects=True)
        after = [c for c in _cats().values() if c["label"] == f"{TAG} Spa"]
        s.check("it is hidden rather than removed", len(after) == 1,
                detail="an account number that has been on invoices is part of the "
                       "record, and deleting it takes the explanation with it")
        s.check("and marked inactive", after[0]["active"] == 0,
                detail=f"{after[0]['active']}")

    s.section("The page shows them all")
    body = oc.get("/admin/pennylane").get_data(as_text=True)
    s.check("in the owner's words", "Nightly" in body and "F&amp;B" in body,
            detail="the page still speaks in the app's old names")
    s.check("with the account against each", "706100" in body)
    s.check("and a way to add one", "categories/new" in body)

    s.section("Guards")
    s.check("an employee cannot remap the accounts",
            ec.post("/admin/pennylane", data={"revenue_nightly": "999999"},
                    follow_redirects=False).status_code in (302, 403))
    s.check("and nothing moved", _account("nightly") == "706100",
            detail=f"{_account('nightly')!r}")
    s.check("nor add a category",
            ec.post("/admin/pennylane/categories/new", data={"label": f"{TAG} Nope"},
                    follow_redirects=False).status_code in (302, 403))

    _cleanup()
    return s
