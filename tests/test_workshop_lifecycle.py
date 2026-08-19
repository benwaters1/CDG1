"""Workshops come and go, and the public page has to keep up.

They are not a fixed catalogue. The owner adds one in the admin, it runs, and
it finishes — and when it has finished it must come off the site rather than
sit there inviting registrations for dates that have passed.

The trap is that only the SESSIONS were filtered by date. A workshop whose
every session was in the past still appeared, with an empty date list reading
"dates to be announced" — which is the opposite of true.

Two states look identical in the data and must not be treated the same:

  - never had a session   -> announced, not yet dated: show it
  - had sessions, all past -> over: hide it

Told apart by whether any session ever existed, which needs no extra column
and no housekeeping job to be run.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWLC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _workshop(name, sort=80):
    conn = db()
    cur = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, '', 2000, 10, 1, ?, ?)""",
        (f"{TAG} {name}", sort, _harness.datetime_now()))
    wid = cur.lastrowid
    conn.commit()
    conn.close()
    return wid


def _session(workshop_id, start, nights=3):
    conn = db()
    cur = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (workshop_id, start.isoformat(), (start + timedelta(days=nights)).isoformat(),
         f"{TAG} sitting", _harness.datetime_now()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def run():
    s = Suite("Workshop lifecycle")
    _cleanup()
    today = m.datetime.now(m.timezone.utc).date()
    pub = m.app.test_client()

    upcoming = _workshop("Running Soon", sort=80)
    _session(upcoming, today + timedelta(days=40))

    finished = _workshop("Long Over", sort=81)
    _session(finished, today - timedelta(days=90))

    undated = _workshop("Coming Later", sort=82)   # deliberately no sessions

    page = pub.get("/workshops").get_data(as_text=True)

    s.section("What a guest sees on the workshops page")
    s.check("one with dates ahead is listed", f"{TAG} Running Soon" in page)
    s.check("one that has finished is gone", f"{TAG} Long Over" not in page,
            detail="a finished workshop is still being advertised")
    s.check("one announced but not yet dated is still listed",
            f"{TAG} Coming Later" in page,
            detail="a workshop with no dates yet was hidden as though it were over")

    s.section("Its own page is not a dead end")
    r = pub.get(f"/workshops/{finished}")
    s.check("a finished workshop's page redirects rather than showing nothing",
            r.status_code in (301, 302), detail=f"HTTP {r.status_code}")
    s.check("and sends them to what is actually on",
            "/workshops" in r.headers.get("Location", ""),
            detail=f"went to {r.headers.get('Location')!r}")
    s.check("while a live one still opens", pub.get(f"/workshops/{upcoming}").status_code == 200)
    s.check("and an undated one opens too, so it can be read about",
            pub.get(f"/workshops/{undated}").status_code == 200)

    s.section("Nobody can register for dates that have passed")
    conn = db()
    past_session = conn.execute(
        "SELECT id FROM workshop_sessions WHERE workshop_id = ?", (finished,)).fetchone()["id"]
    conn.close()
    s.check("the registration page for a past sitting is a 404",
            pub.get(f"/workshops/register/{past_session}").status_code == 404)

    s.section("Adding one in the admin puts it on the site")
    # This is how workshops are meant to arrive — not from code. Proving the
    # admin path works end to end is what makes the seeding in app.py just a
    # first-run bootstrap rather than the way products get added.
    oc, ec, owner, emp = clients()
    oc.post("/admin/workshops/new", data={
        "title": f"{TAG} Added By Hand", "description": "Made in the admin",
        "price_per_person": "2600", "default_capacity": "10", "deposit_percent": "30",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT id FROM workshops WHERE title = ?",
                        (f"{TAG} Added By Hand",)).fetchone()
    conn.close()
    s.check("the workshop is created", made is not None)
    if made:
        page = pub.get("/workshops").get_data(as_text=True)
        s.check("and appears publicly straight away, with no dates yet",
                f"{TAG} Added By Hand" in page)
        # Give it a sitting through the admin, the way the owner would.
        oc.post(f"/admin/workshops/{made['id']}/sessions/new", data={
            "start_date": (today + timedelta(days=60)).isoformat(),
            "end_date": (today + timedelta(days=63)).isoformat(),
            "capacity": "10",
        }, follow_redirects=True)
        conn = db()
        got = conn.execute("SELECT COUNT(*) AS c FROM workshop_sessions WHERE workshop_id = ?",
                           (made["id"],)).fetchone()["c"]
        conn.close()
        s.check("a sitting can be added to it", got == 1, detail=f"got {got}")
        if got:
            page = pub.get("/workshops").get_data(as_text=True)
            s.check("and the date is now offered to guests",
                    (today + timedelta(days=60)).isoformat() in page,
                    detail="the new date is not on the public page")

    s.section("Switching one off hides it without deleting it")
    # The owner's other lever: active = 0. History has to survive it.
    conn = db()
    conn.execute("UPDATE workshops SET active = 0 WHERE id = ?", (upcoming,))
    conn.commit()
    conn.close()
    page = pub.get("/workshops").get_data(as_text=True)
    s.check("an inactive workshop is off the page", f"{TAG} Running Soon" not in page)
    s.check("and its own page 404s rather than selling it",
            pub.get(f"/workshops/{upcoming}").status_code == 404)
    conn = db()
    still = conn.execute("SELECT COUNT(*) AS c FROM workshops WHERE id = ?",
                         (upcoming,)).fetchone()["c"]
    conn.close()
    s.check("but the record is still there", still == 1)

    _cleanup()
    return s
