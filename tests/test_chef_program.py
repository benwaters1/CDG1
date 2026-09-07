"""The kitchen's own page, and the half of it that leaves the house.

Everything a chef needs already existed and was in four places, none of them
the kitchen: who is eating and what they cannot eat on the pass, the clocks
and the bills on the floor, the shortfall on the stock page, the list at
/shopping. So the kitchen asked somebody, or printed a sheet that was wrong
by eight o'clock.

  COMPOSED, NOT REWRITTEN. pass_service and pos_open_tables are already the
  definition every other screen reads. The kitchen and the floor disagreeing
  about how long table four has been standing is worse than neither of them
  knowing — so pos_home's loop was LIFTED OUT rather than copied, and both
  now read the one function.

  THE LIST TRAVELS AND THE GUESTS DO NOT. /chef/shopping is the only page in
  this app the service worker keeps, because it is read in a supermarket an
  hour and three quarters away with no signal. A cached page lives on a device
  that goes in a van and is left in a car park — and the privacy notice says a
  guest's dietary and medical notes are held for the stay and deleted after
  it. A copy in a car park is not that. So the travelling page carries nothing
  personal at all, and this suite checks it rather than trusting it.

  AND A TICK IN AN AISLE IS NOT LOST. Twenty things ticked with no bars and
  lost at the till is the exact case the offline queue was built for.
"""
from datetime import timedelta

import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM shopping_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("The kitchen's own page")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    now = m.datetime.now(m.timezone.utc).isoformat()
    tonight = m.service_day()

    conn = db()
    # Somebody eating here tonight with something they cannot eat. The allergy
    # is the one item on this page that can put a person in hospital.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token,
                   guest_name, guest_email, party_size, dinner_date,
                   dietary_notes, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, ?, 'confirmed', ?)""",
        (TAG + "R1", TAG + "T1", TAG + " Allergic Guest",
         "zzch@example.invalid", tonight.isoformat(),
         TAG + " severe nut allergy", now))
    conn.execute(
        """INSERT INTO shopping_items (name, category, bought, created_at)
           VALUES (?, 'Vegetables', 0, ?)""", (TAG + " leeks", now))
    conn.execute(
        """INSERT INTO shopping_items (name, category, bought, created_at)
           VALUES (?, 'Vegetables', 1, ?)""", (TAG + " carrots", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM shopping_items WHERE name = ?",
                       (TAG + " leeks",)).fetchone()["id"]
    conn.close()

    try:
        s.section("One page, built out of what already knew the answers")
        conn = db()
        try:
            with m.app.test_request_context("/"):
                data = m.chef_tonight(conn)
        finally:
            conn.close()
        s.check("tonight's guests are on it",
                any(TAG in (g["name"] or "") for g in data["guests"]),
                detail=str([g["name"] for g in data["guests"]][:5]))
        s.check("with what they cannot eat",
                any(TAG in (g["dietary_notes"] or "") for g in data["guests"]),
                detail="the one thing on this page that can hurt somebody")
        s.check("and it counts them",
                data["allergy_count"] >= 1, detail=str(data["allergy_count"]))
        for key in ("covers", "floor", "waiting", "money", "owed_now", "stock"):
            s.check("it carries %s" % key, key in data)

        s.section("And it is the same answer the floor gives")
        # THE RULE. pos_home's loop was lifted out rather than copied, so the
        # kitchen and the floor cannot drift apart about how long a table has
        # been standing. Checked at the source, because the drift is invisible
        # until somebody has waited forty minutes.
        source = open("app.py", encoding="utf-8").read().replace("\r\n", "\n")
        s.check("there is one definition of an open table",
                source.count("def pos_open_tables(") == 1,
                detail=str(source.count("def pos_open_tables(")))
        s.check("and the floor reads it rather than its own copy",
                "tables = pos_open_tables(conn, orders)" in source,
                detail="a second copy of that loop is how the two screens "
                       "start disagreeing")
        s.check("and so does the kitchen",
                "floor = pos_open_tables(conn)" in source)
        s.check("the store is asked through stock_levels, not summed again",
                "levels = stock_levels(conn," in source
                and "SUM(stock_movements" not in source.split(
                    "def chef_shopping")[1].split("\ndef ")[0],
                detail="the store is a ledger, not a counter; a page that "
                       "adds the movements its own way will one day disagree "
                       "with the stock page about whether there is butter")

        s.section("The page a chef opens in the kitchen")
        page = ec.get("/chef").get_data(as_text=True)
        s.check("an employee can open it",
                ec.get("/chef").status_code == 200,
                detail="a screen the kitchen cannot open is one that gets "
                       "replaced by a printed sheet nobody updates")
        s.check("the guest is on it", TAG + " Allergic Guest" in page)
        s.check("and so is the allergy", "severe nut allergy" in page)
        s.check("it says how many covers", "covers" in page)
        s.check("and links to the list that travels",
                "/chef/shopping" in page)

        s.section("The list that leaves the house")
        shop = ec.get("/chef/shopping").get_data(as_text=True)
        s.check("it opens", ec.get("/chef/shopping").status_code == 200)
        s.check("the unbought thing is on it", TAG + " leeks" in shop)
        s.check("and so is the one already got",
                TAG + " carrots" in shop,
                detail="a list that hides what was ticked is a list you "
                       "cannot untick in an aisle")
        conn = db()
        try:
            with m.app.test_request_context("/"):
                stamped = m.chef_shopping(conn)["as_of"]
        finally:
            conn.close()
        s.check("it says when it was true",
                ('data-asof="' + stamped[:13]) in shop,
                detail="the point of a page you are allowed to keep is that "
                       "you might be reading yesterday's — and the attribute "
                       "name alone appears in the script that reads it, so "
                       "this has to look at the value")
        shown = re.search(r'data-asof="[^"]*">([^<]*)</span>', shop)
        s.check("and shows a real time before any script runs",
                shown and re.search(r"\d{1,2}[:.]\d{2}", shown.group(1)),
                detail="the script rewrites this into the device's own local "
                       "time; what the server renders is what stands if it "
                       "does not run, and 'as of now' over a day-old cached "
                       "list is worse than no stamp at all")

        s.section("And it carries nothing personal, because it is cached")
        # THE PRIVACY RULE, checked rather than asserted. This page is kept on
        # a device that goes to a supermarket and is left in a van. The notice
        # says dietary and medical notes are held for the stay and deleted
        # after it; a copy in a car park is not that.
        s.check("no guest's name is on it",
                TAG + " Allergic Guest" not in shop,
                detail="this page is cached on a handset that leaves the "
                       "house; a guest's name has no business on it")
        s.check("and no allergy either",
                "nut allergy" not in shop.lower(),
                detail="a medical note in a browser cache in a car park is "
                       "not what the privacy notice says the house does")
        s.check("nor anybody's email address",
                "@example.invalid" not in shop)

        s.section("The service worker keeps that page and only that page")
        sw = open("static/sw.js", encoding="utf-8").read()
        s.check("it is named",
                "'/chef/shopping'" in sw,
                detail="what may be kept is decided per page, not per section")
        s.check("and it is still network first",
                sw.split("/chef/shopping")[1].lstrip().startswith(")")
                or "fetch(request).then" in sw.split("/chef/shopping")[1][:400],
                detail="the copy is a fallback for no signal, never a "
                       "shortcut past a list somebody has added to")
        # The narrowness is the whole safety argument. A second page on that
        # list is a decision somebody should make on purpose.
        special = sorted(set(re.findall(
            r"pathname\s*===\s*'([^']+)'", sw)))
        s.check("and it is the only page kept",
                special == ["/chef/shopping"],
                detail="the narrowness IS the safety argument — a second page "
                       "kept on a handset that leaves the house is a decision "
                       "somebody should make on purpose: " + str(special))

        s.section("A tick in an aisle with no signal is not lost")
        s.check("the shopping tick is one the app will hold",
                "toggle_shopping_item" in m.OFFLINE_ACTIONS,
                detail=str(sorted(m.OFFLINE_ACTIONS)))
        s.check("and holding it is safe, because it is repeatable",
                "@repeatable" in source.split(
                    "def toggle_shopping_item")[0].split("@app.route")[-1],
                detail="a queue retries by definition, and this one toggles: "
                       "a replay without a key would untick what was ticked")
        # Proved rather than asserted: the same key twice must not undo it.
        key = TAG + "-aisle-key"
        first = ec.post(f"/shopping/{sid}/toggle", headers={"X-Action-Key": key})
        conn = db()
        try:
            after_one = conn.execute(
                "SELECT bought FROM shopping_items WHERE id = ?",
                (sid,)).fetchone()["bought"]
        finally:
            conn.close()
        ec.post(f"/shopping/{sid}/toggle", headers={"X-Action-Key": key})
        conn = db()
        try:
            after_two = conn.execute(
                "SELECT bought FROM shopping_items WHERE id = ?",
                (sid,)).fetchone()["bought"]
        finally:
            conn.close()
        s.check("the first tick marks it bought",
                first.status_code == 200 and after_one == 1,
                detail=str(after_one))
        s.check("and sending it again does not untick it",
                after_two == after_one,
                detail="the phone cannot tell 'never heard' from 'reply "
                       "lost', so it retries — and this toggles")
    finally:
        _cleanup()
    return s
