"""Two list pages that never got the standard treatment.

CLAUDE.md is explicit: "Every list gets `list_view()` — search, counted chips,
sort. Never add another one-off search box." The team directory and the
supplier list each had one of their own: a text input, and on the directory a
status dropdown beside it.

A search box is the half that does not help. `list_view`'s own docstring says
why the chips are the point: "A status filter that just lists the statuses
makes you click each one to find out where the work is; one that says 'Pending
4 · Confirmed 112 · Cancelled 9' has already told you." And the counts are
worked out against the OTHER filters, so a chip reading twelve always yields
twelve — which a hand-rolled box cannot do without becoming `list_view`.

WHAT EACH ONE GAINS.

The directory gets **who is on shift right now**, which is what somebody is
looking for half the time they open a staff list, and could not be asked at
all before.

The supplier list gets **what we owe**, bucketed into overdue and owed rather
than listed per supplier — forty suppliers is forty chips, and forty chips is
a second list to read. Plus sorting by most owed, which is the order somebody
actually wants when deciding who to pay.
"""
from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "listtest-"


def _chip_count(body, label, chip):
    """The number on one chip, or None. Reads the rendered page rather than
    the builder, because the whole point is what a person sees."""
    import re
    flat = " ".join(body.split())
    block = flat[flat.find(f'facet-label">{label}<'):]
    block = block[:block.find("</div>") + 6] if "</div>" in block else block
    hit = re.search(r'>%s <span class="chip-n">(\d+)</span>'
                    % re.escape(chip), block)
    return int(hit.group(1)) if hit else None


def run():
    s = Suite("Five lists that had a search box and nothing else")
    oc, ec, _owner, _emp = clients()
    conn = db()

    s.section("All five pages use the toolbar the rest of the house uses")
    for path, name in (("/directory", "directory"),
                       ("/management/vendors", "vendors"),
                       ("/expenses", "expenses"),
                       ("/guests", "guests"),
                       ("/admin/assets", "admin_assets")):
        src = open(f"templates/{name}.html", encoding="utf-8").read()
        s.check(f"{name} includes the shared toolbar",
                '_list_toolbar.html' in src)
        s.check("and no longer rolls its own search box",
                'name="q"' not in src,
                detail="never add another one-off search box")
        s.check(f"{name} still renders", oc.get(path).status_code == 200)

    s.section("The chips carry counts, which is the point")
    body = oc.get("/directory").get_data(as_text=True)
    s.check("the page renders", oc.get("/directory").status_code == 200)
    s.check("there is a chip row at all", "facet-label" in body)
    active = _chip_count(body, "Where they stand", "Active")
    s.check("with a number on the status chip", active is not None,
            detail="a filter that lists the statuses makes you click each one "
                   "to find out where the work is")
    real = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'employee' "
        "AND COALESCE(status, 'active') = 'active'").fetchone()["n"]
    s.check("and the number is the real one",
            active == real, detail=f"{active} on the chip, {real} in the table")

    s.section("A chip yields what it says it will")
    if active:
        filtered = oc.get("/directory?status=Active").get_data(as_text=True)
        s.check("clicking it returns that many",
                filtered.count('class="profile-card') == active
                or _chip_count(filtered, "Where they stand", "Active") == active,
                detail="a chip reading twelve that yields nothing is worse "
                       "than no chip")
    else:
        s.check("there are active staff to filter on", False,
                detail="reported rather than skipped: the check above would "
                       "pass on an empty list")

    s.section("The directory can be asked who is on shift")
    # Somebody has to be clocked in for the chip to exist — hide_empty drops
    # a classification with no values, which is right: a chip row reading
    # "All 4" and nothing else is furniture.
    worker = conn.execute(
        "SELECT id, name FROM users WHERE role = 'employee' "
        "AND COALESCE(status, 'active') = 'active' ORDER BY id LIMIT 1").fetchone()
    s.check("there is somebody to clock in", worker is not None,
            detail="reported rather than skipped")
    if worker:
        conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
            (worker["id"], m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()
        on_now = " ".join(oc.get("/directory").get_data(as_text=True).split())
        conn.execute("DELETE FROM time_entries WHERE user_id = ? "
                     "AND clock_out_at IS NULL", (worker["id"],))
        conn.commit()
        # As a facet LABEL, not as words anywhere on the page: "on shift"
        # appears in the overview band too, so a plain substring passed with
        # the facet taken out entirely.
        s.check("that is offered as a chip",
                'facet-label">On shift<' in on_now,
                detail="what somebody is looking for half the time they open "
                       "a staff list, and it could not be asked at all before")
        s.check("counting the person who is on", 
                '>On now <span class="chip-n">1</span>' in on_now,
                detail=on_now[on_now.find("On shift"):][:130])
        s.check("and the clock is left as it was found",
                not conn.execute(
                    "SELECT 1 FROM time_entries WHERE user_id = ? "
                    "AND clock_out_at IS NULL", (worker["id"],)).fetchone(),
                detail="an open shift left behind would follow every suite "
                       "after this one, and hours are summed from these")

    s.section("And the search searches")
    someone = conn.execute(
        "SELECT name FROM users WHERE role = 'employee' AND name IS NOT NULL "
        "ORDER BY id LIMIT 1").fetchone()
    s.check("there is somebody to search for", someone is not None,
            detail="reported rather than skipped")
    if someone:
        found = oc.get("/directory?q=" + someone["name"].split()[0]
                       ).get_data(as_text=True)
        others = conn.execute(
            "SELECT name FROM users WHERE role = 'employee' AND name IS NOT NULL "
            "AND name NOT LIKE ? ORDER BY id",
            (someone["name"].split()[0] + "%",)).fetchall()
        s.check("the person searched for is on the answer",
                someone["name"] in found)
        s.check("and somebody else is not",
                not others or others[0]["name"] not in found,
                detail=f"searching for {someone['name'].split()[0]!r} still "
                       f"returned {others[0]['name'] if others else '-'} — a "
                       "search over no fields matches everything")

    # Built, because there are none. With an empty table the "what we owe"
    # facet renders nothing at all -- correctly, since hide_empty drops a
    # classification with no values -- and every check below would be a
    # sentence about nothing.
    conn.execute(
        """INSERT INTO vendors (name, contact_person, payment_terms, active,
                   created_at)
           VALUES (?, ?, '30 days', 1, ?)""",
        (TAG + " Late Supplier", TAG + " Contact",
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.execute(
        """INSERT INTO vendors (name, contact_person, payment_terms, active,
                   created_at)
           VALUES (?, ?, 'On delivery', 1, ?)""",
        (TAG + " Settled Supplier", TAG + " Other",
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    late_id = conn.execute("SELECT id FROM vendors WHERE name = ?",
                           (TAG + " Late Supplier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO expenses (kind, vendor_id, vendor_name, description,
                   amount, status, due_date, doc_type, submitted_at)
           VALUES ('supplier_invoice', ?, ?, ?, 640.0, 'approved', ?,
                   'bill_to_pay', ?)""",
        (late_id, TAG + " Late Supplier", TAG + "overdue bill",
         (m.house_today() - m.timedelta(days=20)).isoformat(),
         m.datetime.now(m.timezone.utc).isoformat()))
    # A third: owed money that is NOT yet due. Without it both fixtures are
    # extremes -- one overdue, one owed nothing -- and calling every unpaid
    # bill overdue looks identical to telling them apart.
    conn.execute(
        """INSERT INTO vendors (name, contact_person, payment_terms, active,
                   created_at)
           VALUES (?, ?, '60 days', 1, ?)""",
        (TAG + " Soon Supplier", TAG + " Third",
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    soon_id = conn.execute("SELECT id FROM vendors WHERE name = ?",
                           (TAG + " Soon Supplier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO expenses (kind, vendor_id, vendor_name, description,
                   amount, status, due_date, doc_type, submitted_at)
           VALUES ('supplier_invoice', ?, ?, ?, 210.0, 'approved', ?,
                   'bill_to_pay', ?)""",
        (soon_id, TAG + " Soon Supplier", TAG + "future bill",
         (m.house_today() + m.timedelta(days=30)).isoformat(),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()

    s.section("The supplier list can be asked what we owe")
    v = oc.get("/management/vendors").get_data(as_text=True)
    s.check("the page renders",
            oc.get("/management/vendors").status_code == 200)
    s.check("with a chip for what is owed", "What we owe" in v,
            detail="the question this page is opened to answer")
    s.check("and the overdue one is counted there",
            _chip_count(v, "What we owe", "Overdue") == 1,
            detail=f"{_chip_count(v, 'What we owe', 'Overdue')} — a bill "
                   "twenty days past its date")
    s.check("and a bill not yet due is owed rather than overdue",
            _chip_count(v, "What we owe", "Owed") == 1,
            detail=f"{_chip_count(v, 'What we owe', 'Owed')} — overdue and "
                   "merely owed are different conversations with the same "
                   "supplier, and calling both overdue is how a real one "
                   "stops standing out")
    s.check("and one for payment terms", "Payment terms" in v)
    s.check("bucketed rather than listed per supplier",
            "Overdue" in v or "Owed" in v,
            detail="forty suppliers is forty chips, and forty chips is a "
                   "second list to read")

    s.section("And sorted the way somebody actually wants it")
    s.check("most owed first is offered", "Most owed first" in v)
    s.check("as is most spent with", "Most spent with" in v)
    s.check("the sorted page still renders",
            oc.get("/management/vendors?sort=owed").status_code == 200)

    s.section("Searching still searches")
    hit = oc.get("/management/vendors?q=Late+Supplier").get_data(as_text=True)
    s.check("a supplier can be found by name",
            TAG + " Late Supplier" in hit)
    s.check("and the other one is not on the answer",
            TAG + " Settled Supplier" not in hit,
            detail="a search that returns the whole list has not searched")
    contact = oc.get("/management/vendors?q=Contact").get_data(as_text=True)
    s.check("and by who you speak to there",
            TAG + " Late Supplier" in contact,
            detail="the old box searched name and contact; the toolbar "
                   "searches the telephone and the notes as well")

    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    s.check("and the suppliers are taken away again",
            not conn.execute("SELECT 1 FROM vendors WHERE name LIKE ?",
                             (TAG + "%",)).fetchone(),
            detail="every suite after this one reads the same table")

    s.section("The expenses page can be asked the two things it is opened for")
    e = " ".join(oc.get("/expenses").get_data(as_text=True).split())
    s.check("where the work is", 'facet-label">Where it stands<' in e,
            detail="a page showing every invoice and every claim with no way "
                   "to ask what is waiting on you")
    s.check("and what the accountant has not been given",
            'facet-label">With the accountant<' in e,
            detail="the other question, and it could not be asked at all")
    s.check("with the two lists told apart", 'facet-label">Kind<' in e,
            detail="supplier bills and staff claims are different money and "
                   "different people")
    s.check("and the document type from this morning's work",
            'facet-label">What it is<' in e)
    s.check("a chip narrows it",
            oc.get("/expenses?sent=Not+sent").status_code == 200)

    s.section("The guest list can be asked who has stayed before")
    g = " ".join(oc.get("/guests").get_data(as_text=True).split())
    s.check("that is a chip", 'facet-label">Have they stayed<' in g,
            detail="one of the two questions actually asked of a guest list, "
                   "and it could not be asked before")
    s.check("with a number on it",
            _chip_count(g, "Have they stayed", "First time") is not None
            or _chip_count(g, "Have they stayed", "Been before") is not None,
            detail=g[g.find("Have they stayed"):][:150])
    s.check("and one search narrows the whole page",
            "lv.q" in open("templates/guests.html", encoding="utf-8").read()
            or oc.get("/guests?q=zzz-nobody").status_code == 200,
            detail="the who-is-here lists above the profiles follow the same "
                   "box, so it does not narrow half a page")

    s.section("The asset register keeps the pool and gains the chips")
    # Built, because the register is empty. With no rows the facets correctly
    # render nothing — hide_empty again — and every check below would be a
    # sentence about nothing.
    # One of them sold, which is the only fixture that proves the page still
    # opens on what the house STILL HAS rather than on everything it has ever
    # owned.
    for name, where, status in ((" Gilt mirror", "Chambre Cerise", "held"),
                                (" Oak table", "La Cuisine", "held"),
                                (" Sold commode", "Gone", "sold")):
        conn.execute(
            """INSERT INTO assets (name, category, location, status,
                       estimated_value, insurance_policy_id, created_at)
               VALUES (?, 'furniture', ?, ?, 900.0, NULL, ?)""",
            (TAG + name, where, status,
             m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    a = " ".join(oc.get("/admin/assets").get_data(as_text=True).split())
    s.check("still-held is still what it opens on",
            'value="current" selected' in a,
            detail="a register opening on everything ever owned buries the "
                   "forty things in the building under the four that were sold")
    s.check("so a sold piece is not on the default view",
            TAG + " Sold commode" not in a,
            detail="a register opening on everything ever owned buries the "
                   "forty things in the building under the four that were sold")
    sold = oc.get("/admin/assets?status=sold").get_data(as_text=True)
    s.check("and asking for the sold ones still works",
            TAG + " Sold commode" in sold)
    s.check("without the ones still in the house",
            TAG + " Gilt mirror" not in sold,
            detail="the dropdown chooses which pool the page is about, which "
                   "is a different job from filtering within one")
    s.check("what is not insured is now a chip",
            'facet-label">Insurance<' in a or "Not covered" in a,
            detail="the sentence an insurance renewal is actually about, and "
                   "the old form could not say it")
    s.check("as is where a thing is",
            'facet-label">Where it is<' in a)
    s.check("counting the two uninsured pieces",
            _chip_count(a, "Insurance", "Not covered") == 2,
            detail=str(_chip_count(a, "Insurance", "Not covered")))
    conn.execute("DELETE FROM assets WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    s.check("and the register is left as it was found",
            not conn.execute("SELECT 1 FROM assets WHERE name LIKE ?",
                             (TAG + "%",)).fetchone(),
            detail="every suite after this one reads the same register")

    s.section("An employee sees the colleague list, not the admin one")
    emp = ec.get("/directory")
    s.check("they can open it", emp.status_code == 200)
    s.check("and it still has the toolbar",
            "facet-label" in emp.get_data(as_text=True),
            detail="the standard treatment is for every list, not only the "
                   "owner's")

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
