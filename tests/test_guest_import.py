"""Eleven years of guests, brought in from the system this one replaced.

An import is the easiest way to quietly ruin a table, so the things worth
asserting are the refusals rather than the happy path: that it does not
overwrite what somebody here typed, that running it twice changes nothing the
second time, that the same address twice in one file is one person rather than
two, and that the money on the export stays off.

THE MONEY IS THE ONE THAT MATTERS. The real export carries an outstanding
balance for fifty-three people, a little over two hundred thousand euros, some
of it against people first added in 2021. This app chases an unpaid balance
automatically and that automation ships switched on, so a figure imported from
a system that has been turned off would start writing to people about money
they may have settled years ago. The check below is that not one of those
numbers reaches the database.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZIMP"

FILE = (
    "first_name,last_name,person_id,email,phone,num_programs,overall_balance,"
    "personal_credit,date_added,my_arrival_information,"
    "accommodation_information_in_toulouse,departure_information\n"
    # An ordinary person, with all three travel notes.
    f"Ana,{TAG}Ruiz,101,{TAG.lower()}.ana@example.invalid,+34 600 000 001,2,€0.00,€0.00,"
    "2021-05-04,Landing Toulouse 14:20,Hotel Albert 1er,Taxi at 09:00\n"
    # Somebody who owes a great deal. None of it may be written.
    f"Bo,{TAG}Lang,102,{TAG.lower()}.bo@example.invalid,+34 600 000 002,1,€25200.00,€0.00,"
    "2022-01-09,,,\n"
    # The same address twice: one person, both rows' notes.
    f"Cleo,{TAG}Fern,103,{TAG.lower()}.cleo@example.invalid,+34 600 000 003,1,€0.00,€0.00,"
    "2023-03-03,Arriving Thursday,,\n"
    f"Cleo,{TAG}Fernandez,104,{TAG.lower()}.cleo@example.invalid,+34 600 000 003,1,€3808.00,€0.00,"
    "2023-03-03,,Staying at Le Grand,\n"
    # An accented name, which the real file has and which must survive.
    f"Frédérique,{TAG}Noël,105,{TAG.lower()}.fred@example.invalid,+34 600 000 004,6,"
    "€0.00,€0.00,2019-07-01,,,\n"
    # No email: nameable, but not importable.
    f"Dag,{TAG}Noemail,106,,+34 600 000 005,1,€0.00,€0.00,2024-02-02,,,\n"
)


def _clean(conn):
    conn.execute("DELETE FROM guests WHERE name LIKE ? OR email LIKE ?",
                 ("%" + TAG + "%", TAG.lower() + "%"))
    conn.commit()


def run():
    s = Suite("Importing the old guest list")
    oc, ec, _owner, emp = clients()
    conn = db()
    _clean(conn)

    # ---- reading the file, with no database involved --------------------
    s.section("Reading the export")
    people, problems = m.parse_guest_import(FILE)
    by_email = {p["email"]: p for p in people}
    s.check("four people come out of six rows", len(people) == 4,
            detail="rows: %d, problems: %s" % (len(people), problems))
    s.check("and the row with no email is named, not dropped quietly",
            any(TAG in label for label, _why in problems),
            detail=str(problems))

    s.check("an accented name survives",
            any(p["name"] == "Frédérique " + TAG + "Noël" for p in people),
            detail=str([p["name"] for p in people]))

    s.section("The same address twice is one person")
    cleo = by_email.get(TAG.lower() + ".cleo@example.invalid")
    s.check("both rows fold into one", cleo and cleo["rows"] == 2)
    # Importing them twice would create exactly the duplicate the guests table
    # was refactored to stop being.
    s.check("keeping the fuller name",
            cleo and cleo["name"].endswith("Fernandez"), detail=cleo["name"] if cleo else "")
    s.check("and both rows' notes",
            cleo and any("Arriving Thursday" in n for n in cleo["notes"])
            and any("Le Grand" in n for n in cleo["notes"]),
            detail=str(cleo["notes"] if cleo else None))

    s.section("The travel notes come across, under headings")
    ana = by_email.get(TAG.lower() + ".ana@example.invalid")
    s.check("arrival, Toulouse and departure are all kept",
            ana and len(ana["notes"]) == 3, detail=str(ana["notes"] if ana else None))
    s.check("each said under its own heading",
            ana and any(n.startswith("Arrival:") for n in ana["notes"])
            and any(n.startswith("Toulouse:") for n in ana["notes"])
            and any(n.startswith("Departure:") for n in ana["notes"]))

    # ---- the dry run writes nothing ------------------------------------
    s.section("A dry run writes nothing")
    before = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    result = m.import_guest_rows(conn, people, dry_run=True)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) AS c FROM guests").fetchone()["c"]
    s.check("it says what it would add", len(result["added"]) == 4,
            detail=str(result["added"]))
    s.check("and adds nothing", after == before,
            detail="%d -> %d" % (before, after))

    # ---- and the real one writes -----------------------------------------
    s.section("Importing for real")
    result = m.import_guest_rows(conn, people, dry_run=False)
    conn.commit()
    s.check("four people are added", len(result["added"]) == 4)
    ana_row = conn.execute("SELECT * FROM guests WHERE email = ?",
                           (TAG.lower() + ".ana@example.invalid",)).fetchone()
    s.check("with their name", ana_row and ana_row["name"] == "Ana " + TAG + "Ruiz")
    s.check("their travel notes on the record",
            ana_row and "Hotel Albert 1er" in (ana_row["notes"] or ""),
            detail=repr(ana_row["notes"] if ana_row else None))
    s.check("and where the record came from",
            ana_row and "old workshop booking system" in (ana_row["notes"] or ""),
            detail="a note nobody can place is one nobody trusts")
    s.check("the date they were first added is kept",
            ana_row and (ana_row["created_at"] or "").startswith("2021-05-04"),
            detail=repr(ana_row["created_at"] if ana_row else None))

    # ---- THE MONEY -------------------------------------------------------
    s.section("The balances on the export are not imported")
    bo = conn.execute("SELECT * FROM guests WHERE email = ?",
                      (TAG.lower() + ".bo@example.invalid",)).fetchone()
    s.check("the person who owes 25,200 is imported", bo is not None)
    # Read back out of the whole row: whatever column somebody might be tempted
    # to put it in, the figure must not be in any of them.
    values = " ".join(str(v) for v in (tuple(bo) if bo else ()))
    s.check("and not one euro of it came with them",
            "25200" not in values.replace(",", "").replace(".00", ""),
            detail="the balance reached the guest record: " + values[:160])
    # A PERSON LISTED TWICE CARRIES A BALANCE PER ROW. In the real export the
    # duplicated people have 0.00 against one registration and the real figure
    # against the other, so folding them by keeping the first row's value drops
    # the money — it lost 19,826 euros of the real file. The figure exists to
    # be reconciled against the file, so it has to add up to the file.
    s.check("a folded person keeps the balance from both rows",
            cleo and cleo["balance"] == 3808.0,
            detail="got %r" % (cleo["balance"] if cleo else None))
    owed = [p for p in people if p["balance"]]
    s.check("though the file was read and every figure counted",
            len(owed) == 2 and round(sum(p["balance"] for p in owed), 2) == 29008.0,
            detail="it has to be reportable to be refused out loud")

    # ---- running it twice -------------------------------------------------
    s.section("Running it twice changes nothing the second time")
    again = m.import_guest_rows(conn, people, dry_run=False)
    conn.commit()
    s.check("nobody is added a second time", not again["added"],
            detail=str(again["added"]))
    s.check("and they are reported as already complete",
            len(again["untouched"]) == 4, detail=str(again))
    s.check("there is still one of each",
            conn.execute("SELECT COUNT(*) AS c FROM guests WHERE email = ?",
                         (TAG.lower() + ".ana@example.invalid",)).fetchone()["c"] == 1)

    # ---- what somebody here typed wins -----------------------------------
    s.section("What was typed here is never overwritten")
    conn.execute("UPDATE guests SET phone = ?, notes = ? WHERE email = ?",
                 ("+33 6 11 22 33 44", "Allergic to shellfish — told us in person",
                  TAG.lower() + ".ana@example.invalid"))
    conn.commit()
    m.import_guest_rows(conn, people, dry_run=False)
    conn.commit()
    kept = conn.execute("SELECT * FROM guests WHERE email = ?",
                        (TAG.lower() + ".ana@example.invalid",)).fetchone()
    s.check("a phone number typed here survives the import",
            kept["phone"] == "+33 6 11 22 33 44", detail=repr(kept["phone"]))
    # THE ORDER OF TRUST. Somebody in this house wrote that down about a guest;
    # a file exported from a system nobody uses any more does not get to
    # replace it.
    s.check("and so does a note somebody wrote about them",
            "Allergic to shellfish" in (kept["notes"] or ""),
            detail=repr(kept["notes"]))

    # ---- the door ---------------------------------------------------------
    s.section("Who can do this")
    s.check("the owner can open it",
            oc.get("/management/import-guests").status_code == 200)
    s.check("an employee cannot",
            ec.get("/management/import-guests").status_code == 403,
            detail="it lists three hundred people's addresses and travel plans")
    s.check("and neither can a stranger",
            m.app.test_client().get("/management/import-guests").status_code
            in (302, 401, 403))
    s.check("an employee cannot post one either",
            ec.post("/management/import-guests").status_code == 403,
            detail="the role is checked on the write as well as the read")

    # AND NOT WITH A PRESET EITHER. @owner_required does not mean owner — it
    # means "a preset covering this page's area" — and that reading is what
    # once made a grievance about a manager readable by that manager. This
    # page is held shut by being in no area at all: can_reach refuses an
    # unmapped endpoint outright, so there is no preset that reaches it.
    # Handing somebody management and watching them still be refused is how
    # that is proved, and it is why the page must never be added to
    # NAV_AREAS to 'tidy up' — that would open it to every manager preset.
    conn.execute("""INSERT OR REPLACE INTO access_presets
                    (slug, name, description, areas, is_full_access, built_in,
                     sort_order, created_at)
                    VALUES (?, ?, ?, ?, 0, 0, 99, ?)""",
                 (TAG.lower(), TAG + " manager", "for the test",
                  "management,guests",
                  m.datetime.now(m.timezone.utc).isoformat()))
    before = conn.execute("SELECT access_preset FROM users WHERE id = ?",
                          (emp["id"],)).fetchone()["access_preset"]
    conn.execute("UPDATE users SET access_preset = ? WHERE id = ?",
                 (TAG.lower(), emp["id"]))
    conn.commit()
    try:
        s.check("nor an employee whose preset grants management",
                ec.get("/management/import-guests").status_code == 403,
                detail="the area let them in and only the role stopped them")
        s.check("and they cannot post one either",
                ec.post("/management/import-guests").status_code == 403)
    finally:
        conn.execute("UPDATE users SET access_preset = ? WHERE id = ?",
                     (before, emp["id"]))
        conn.execute("DELETE FROM access_presets WHERE slug = ?", (TAG.lower(),))
        conn.commit()

    _clean(conn)
    conn.close()
    return s
