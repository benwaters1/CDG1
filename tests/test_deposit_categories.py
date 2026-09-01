"""A deposit rule can be written for everything the table accepts.

The categories lived in three places -- the table's CHECK, the route's
validation tuple, and the form's <option> list -- and adding 'event' to the
CHECK left the other two behind. The result was a table that accepted an event
deposit rule and no way in the app to write one: the deposit percentage for a
wedding could not be varied by date or party size, and nothing looked wrong.

That is the same shape as a column read and never written, one level up, and the
guard has to be the relationship rather than any one of the four places. So this
suite compares them against each other: the constant, the live table's CHECK,
the form's options, and what the route will accept. Adding a category to one and
not the others fails here.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZDEPCAT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM deposit_rules WHERE label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Deposit rule categories")
    _cleanup()
    oc, ec, owner, emp = clients()

    keys = m.DEPOSIT_RULE_KEYS
    s.section("There is one list, and it is not empty")
    s.check("the constant names some categories", len(keys) >= 3,
            detail=f"{keys}")
    s.check("every entry has a label to show",
            all(k and lbl for k, lbl in m.DEPOSIT_RULE_CATEGORIES),
            detail=f"{m.DEPOSIT_RULE_CATEGORIES}")
    s.check("and events are among them", "event" in keys,
            detail=f"{keys} — an event deposit is the largest sum the house asks "
                   "for up front and could not have a rule at all")

    s.section("The live table accepts exactly those")
    conn = db()
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'deposit_rules'").fetchone()["sql"] or ""
    conn.close()
    for key in keys:
        s.check(f"the CHECK allows {key}", f"'{key}'" in sql,
                detail="the constant offers a category the database refuses, so "
                       "saving the rule 500s or is silently dropped")

    s.section("The form offers exactly those")
    body = oc.get("/admin/deposit-rules").get_data(as_text=True)
    s.check("the page opens", "<select name=\"category\"" in body)
    for key, label in m.DEPOSIT_RULE_CATEGORIES:
        s.check(f"the form offers {key}", f'value="{key}"' in body,
                detail="the database accepts this and the page gives no way to "
                       "choose it")
        s.check(f"and names it in words for {key}", label in body,
                detail=f"{label!r} missing — an option reading 'event' rather "
                       "than 'Event' is a field name leaking onto a page")

    s.section("The route accepts exactly those, and nothing else")
    # Driven through the form, so this is what the owner's click actually does.
    for i, key in enumerate(keys):
        r = oc.post("/admin/deposit-rules/new", data={
            "category": key, "deposit_percent": "40",
            "min_party_size": "8", "label": f"{TAG} {key}",
        }, follow_redirects=True)
        conn = db()
        made = conn.execute("SELECT * FROM deposit_rules WHERE label = ?",
                            (f"{TAG} {key}",)).fetchone()
        conn.close()
        s.check(f"a {key} rule is written", made is not None,
                detail=f"{flashes(r)[:1]} — the form offers it and the route "
                       "refuses it")
        if made:
            s.check(f"with the percentage as given for {key}",
                    abs((made["deposit_percent"] or 0) - 40) < 0.01,
                    detail=f"{made['deposit_percent']}")

    r = oc.post("/admin/deposit-rules/new", data={
        "category": "spaceship", "deposit_percent": "40",
        "min_party_size": "8", "label": f"{TAG} junk",
    }, follow_redirects=True)
    conn = db()
    junk = conn.execute("SELECT COUNT(*) c FROM deposit_rules WHERE label = ?",
                        (f"{TAG} junk",)).fetchone()["c"]
    conn.close()
    s.check("a category nobody offers is refused", junk == 0,
            detail="the CHECK is the only thing standing between a typo and a "
                   "deposit rule that matches nothing")
    s.check("and the owner is told why",
            any("valid category" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("An event rule actually changes what an event asks for")
    # The point of the whole thing. A rule that saves and never applies is the
    # same defect wearing a different hat.
    conn = db()
    house = m.event_payment_setting(conn, "event_deposit_percent")
    with_rule = m.resolve_deposit_percent(
        conn, "event", (house_today() + timedelta(days=90)).isoformat(), 20, house)
    conn.close()
    s.check("a party over the threshold gets the rule's percentage",
            abs(with_rule - 40) < 0.01,
            detail=f"{with_rule} rather than 40 — the rule is in the table and "
                   "resolve_deposit_percent does not see it")
    conn = db()
    small = m.resolve_deposit_percent(
        conn, "event", (house_today() + timedelta(days=90)).isoformat(), 2, house)
    conn.close()
    s.check("and a small party still gets the house percentage",
            abs(small - house) < 0.01,
            detail=f"{small} vs house {house}")

    s.section("Guards")
    s.check("an employee cannot write a deposit rule",
            ec.post("/admin/deposit-rules/new",
                    data={"category": "event", "deposit_percent": "99",
                          "min_party_size": "2", "label": f"{TAG} nope"},
                    follow_redirects=False).status_code in (302, 403))
    conn = db()
    left = conn.execute("SELECT COUNT(*) c FROM deposit_rules WHERE label = ?",
                        (f"{TAG} nope",)).fetchone()["c"]
    conn.close()
    s.check("and nothing was written", left == 0)

    _cleanup()
    return s
