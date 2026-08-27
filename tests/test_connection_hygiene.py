"""A failed write must not take the write lock down with it.

This is the bug that took the app down through a stock movement against a
deleted item. It was fixed in that one route by checking the item exists first,
and it was still live in five others, because the real cause was never the
check — it was that nothing closed the connection when an execute() raised.

The shape: a form posts an id that no longer resolves. The insert reaches
SQLite, the foreign key fails, and the request 500s. The traceback keeps the
stack frame alive, the frame keeps the connection alive, and the connection
keeps the write transaction it had already begun. SQLite allows one writer, so
until the garbage collector happens to run — measured at over ten seconds —
nothing anywhere in the app can write. Production is one gunicorn worker with
eight threads sharing that process, so it is not one page that stops.

`close_open_connections` now owns every connection get_db() hands out inside a
request, so the frame no longer decides when the lock is released.

The check that matters is the one WITHOUT a gc.collect(). Collecting first
would release the connection by hand and pass against the broken code too,
which is how a test like this quietly becomes worthless.
"""
import gc
import sqlite3

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZCONN"

# Each of these posts an id that no longer resolves, against a column that
# really does carry a foreign key. Confirmed by PRAGMA foreign_key_list.
#
# Every payload has to be complete enough to reach the INSERT. A form missing a
# required field stops at an earlier validation and never gets near the foreign
# key, so the check passes without exercising anything — which is exactly what
# the first draft of this file did, on the certification case, with no `name`.
DANGLING = [
    ("a certification for nobody", "/admin/hr/certifications/new",
     {"name": TAG + " first aid", "kind": "training", "user_id": "999999",
      "expiry_date": "2036-01-01"}),
    ("an absence for nobody", "/admin/hr/absences/new",
     {"user_id": "999999", "start_date": "2035-01-01",
      "end_date": "2035-01-02", "kind": "sick"}),
    ("stock from a deleted vendor", "/admin/stock/new",
     {"name": TAG + " wine", "category": "drinks", "unit": "bottle",
      "reorder_level": "1", "vendor_id": "999999"}),
    ("a dish from a deleted stock item", "/admin/restaurant/menu/new",
     {"name": TAG + " dish", "price": "10", "stock_item_id": "999999"}),
    ("an atelier led by nobody", "/admin/workshops/new",
     {"title": TAG + " atelier", "instructor_user_id": "999999"}),
]


def _cleanup():
    conn = db()
    for table, col in (("stock_items", "name"), ("menu_items", "name"),
                       ("workshops", "title"), ("certifications", "name")):
        try:
            conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (TAG + "%",))
        except sqlite3.OperationalError:
            pass
    conn.execute("DROP TABLE IF EXISTS zz_conn_probe")
    conn.commit()
    conn.close()


def _make_probe_table():
    conn = sqlite3.connect(m.DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS zz_conn_probe (id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.close()


def _can_write(timeout=0.5):
    """Can an unrelated connection get the write lock right now?

    A separate connection on purpose: the point is whether the REST of the app
    can still write, not whether this one can.
    """
    conn = sqlite3.connect(m.DB_PATH, timeout=timeout)
    try:
        conn.execute("INSERT INTO zz_conn_probe (v) VALUES ('x')")
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run():
    s = Suite("Connection hygiene")
    _cleanup()
    _make_probe_table()
    oc, ec, owner, emp = clients()

    s.section("A write that fails leaves the lock behind it")
    for label, url, data in DANGLING:
        gc.collect()
        if not _can_write():
            s.check(f"{label}: the probe was already wedged", False,
                    detail="an earlier case leaked a lock — this run cannot "
                           "tell you anything about this one")
            continue
        code = oc.post(url, data=data).status_code
        # Deliberately no gc.collect() here. Collecting would release the
        # connection by hand and the check would pass against broken code.
        s.check(f"{label}: the rest of the app can still write", _can_write(),
                detail=f"HTTP {code} left the write lock held — every till "
                       "order, clock-in and booking in the house now waits "
                       "on the garbage collector")

    s.section("And says so in words, rather than a stack trace")
    # Not cosmetic. The realistic way to hit this is to delete a supplier in
    # one tab and submit a form that was already open in another — an ordinary
    # afternoon, not an attack — and a 500 tells the owner nothing about what
    # they did or what to do instead.
    for label, url, data in DANGLING:
        gc.collect()
        r = oc.post(url, data=data, follow_redirects=True)
        said = " ".join(flashes(r)).lower()
        s.check(f"{label}: not a 500", r.status_code < 500,
                detail=f"HTTP {r.status_code}")
        s.check(f"{label}: says the list has changed",
                "no longer" in said,
                detail=f"{flashes(r)[:1]} — nothing told them why it failed")

    s.section("And nothing is written when it is refused")
    conn = db()
    leftovers = 0
    for table, col in (("stock_items", "name"), ("menu_items", "name"),
                       ("workshops", "title"), ("certifications", "name")):
        leftovers += conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE {col} LIKE ?",
            (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("no half-made rows survived the refusals", leftovers == 0,
            detail=f"{leftovers} row(s) written by a request that was rejected")

    s.section("And the ordinary path still works")
    # A teardown that closes too eagerly would break every page instead, which
    # is the failure this fix could plausibly have introduced.
    for label, url in (("the dashboard", "/"), ("bookings", "/admin/bookings"),
                       ("the stock page", "/admin/stock")):
        r = oc.get(url)
        s.check(f"{label} still renders", r.status_code == 200,
                detail=f"HTTP {r.status_code}")

    s.section("A successful write still commits")
    # Teardown rolls back before closing. If it ever rolled back a committed
    # transaction, saving anything would silently stop working.
    oc.post("/breakfast/items/new",
            data={"name": TAG + " croissant", "category": "bakery"},
            follow_redirects=True)
    conn = db()
    saved = conn.execute("SELECT 1 FROM breakfast_items WHERE name = ?",
                         (TAG + " croissant",)).fetchone()
    conn.execute("DELETE FROM breakfast_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    s.check("the row is there afterwards", saved is not None,
            detail="the teardown rolled back a commit — nothing saves any more")

    s.section("Outside a request, nothing is tracked")
    # The automation thread has no request context and closes its own
    # connections. Tracking there would put them on a context that never pops.
    conn = m.get_db()
    tracked = m.has_request_context()
    conn.close()
    s.check("get_db() outside a request still works", not tracked,
            detail="the background job path went through the request context")

    _cleanup()
    return s
