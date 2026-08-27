"""What the house has lent and not had back.

Both registers listed what was issued, ordered by name, and neither said how
long. The access page flagged a departed holder on that page only — so "who
has keys to a Class I monument" needed somebody to open the right screen and
read carefully.

The find underneath is the better one: offboarding_items is free text with no
link to either register, so ticking "return keys" on the way out proves only
that somebody ticked it. The checklist and the register have never been
compared, and they can disagree indefinitely.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-out-"


def _cleanup(conn):
    conn.execute("DELETE FROM access_holdings WHERE access_item_id IN "
                 "(SELECT id FROM access_items WHERE label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM access_items WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM equipment_items WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM offboarding_items WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name, status="active"):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', ?, 'General', ?, ?)""",
        (f"{TAG}{name}@example.invalid", name, status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _key(conn, label, user_id, days_ago, returned=False):
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO access_items (label, kind, active, created_at) VALUES (?, 'fob', 1, ?)",
        (TAG + label, now.isoformat()))
    item = conn.execute("SELECT id FROM access_items WHERE label = ?",
                        (TAG + label,)).fetchone()["id"]
    conn.execute(
        """INSERT INTO access_holdings (access_item_id, user_id, issued_at, returned_at)
           VALUES (?, ?, ?, ?)""",
        (item, user_id, (now - timedelta(days=days_ago)).isoformat(),
         now.isoformat() if returned else None))
    conn.commit()


def _find(data, what):
    return next((r for r in data["lent"] if r["what"] == TAG + what), None)


def run():
    s = Suite("still out")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc)

    s.section("What is out, and for how long")
    here = _person(conn, "Current")
    _key(conn, "Cellar", here, 120)
    data = m.things_still_out(conn)
    r = _find(data, "Cellar")
    s.check("an issued key is listed", r is not None)
    s.check("with how long it has been gone", r and 118 <= r["days"] <= 122,
            detail=str(r["days"]) if r else "")
    s.check("and flagged as out a while", r and r["stale"] is True)

    s.section("A returned key drops off")
    _key(conn, "Returned", here, 200, returned=True)
    s.check("signing something back in removes it",
            _find(m.things_still_out(conn), "Returned") is None)

    s.section("Held by somebody who has left")
    gone = _person(conn, "Departed", status="inactive")
    _key(conn, "Frontdoor", gone, 30)
    data = m.things_still_out(conn)
    leaver = _find(data, "Frontdoor")
    s.check("it is marked as held by a leaver", leaver and leaver["gone"] is True)
    s.check("and appears on its own list",
            any(x["what"] == TAG + "Frontdoor" for x in data["with_leavers"]))
    # Sorted above age deliberately: a person who can open a door outranks an
    # old laptop however long each has been gone.
    s.check("a leaver's recent key outranks a current person's old one",
            data["lent"][0]["what"] == TAG + "Frontdoor",
            detail=str([x["what"] for x in data["lent"][:2]]))
    s.check("and a current holder is not counted as a leaver",
            _find(data, "Cellar")["gone"] is False)

    s.section("Kit counts too, not just keys")
    conn.execute(
        "INSERT INTO equipment_items (user_id, label, issued_at) VALUES (?, ?, ?)",
        (here, TAG + "Laptop", (now - timedelta(days=10)).isoformat()))
    conn.commit()
    s.check("a laptop still out is listed",
            _find(m.things_still_out(conn), "Laptop") is not None)
    s.check("but ten days is not 'a while'",
            _find(m.things_still_out(conn), "Laptop")["stale"] is False)

    s.section("The checklist says returned; the register says not")
    # The whole reason this page exists. Ticking a free-text line proves only
    # that somebody ticked it.
    conn.execute(
        """INSERT INTO offboarding_items (user_id, label, done, created_at)
           VALUES (?, ?, 1, ?)""",
        (gone, TAG + "Hand back keys and fob", now.isoformat()))
    conn.commit()
    contra = m.offboarding_contradictions(conn)
    mine = [c for c in contra if c["user_id"] == gone]
    s.check("the contradiction is found", len(mine) == 1, detail=str(len(mine)))
    s.check("it names what is actually still open",
            mine and any(x["what"] == TAG + "Frontdoor" for x in mine[0]["still_out"]))

    # Somebody who ticked it and genuinely has nothing out must not appear,
    # or every leaver ever becomes a false alarm.
    clean = _person(conn, "Cleanleaver", status="inactive")
    conn.execute(
        """INSERT INTO offboarding_items (user_id, label, done, created_at)
           VALUES (?, ?, 1, ?)""",
        (clean, TAG + "Hand back keys", now.isoformat()))
    conn.commit()
    s.check("somebody who returned everything is not accused",
            not any(c["user_id"] == clean for c in m.offboarding_contradictions(conn)))

    # An unticked line is not a contradiction — it is simply not done yet.
    conn.execute("UPDATE offboarding_items SET done = 0 WHERE user_id = ? AND label = ?",
                 (gone, TAG + "Hand back keys and fob"))
    conn.commit()
    s.check("an unticked checklist line is not a contradiction",
            not any(c["user_id"] == gone for c in m.offboarding_contradictions(conn)))

    s.section("The page and the front page")
    page = oc.get("/admin/still-out").get_data(as_text=True)
    s.check("it renders", "Still out" in page)
    s.check("the departed holder is called out", "Held by somebody who has left" in page)
    s.check("it admits there is no due-back date", "no due-back date" in page)

    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, datetime.now(m.LOCAL_TZ).date())
    s.check("a leaver holding a key reaches the owner home",
            any(w["title"] == "Somebody who has left still holds a key" for w in warnings),
            detail=str([w["title"] for w in warnings]))

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
