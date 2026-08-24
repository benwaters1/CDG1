"""What a staff profile shows, and to whom.

One page serves three different readers. The owner sees everything on it. The
person themselves sees their own page, because that is where they check their
hours and update a phone number. A manager with `team` access can open anybody's
profile, because that is what running a rota needs.

Three things on that page are none of the last two's business:

  - check-in notes, which are the owner's 1:1 record ABOUT the person
  - pay rate history, which is what everybody else is paid on the same list
  - the offboarding checklist, which is what happens when they leave

All three are held back by `user["role"] == "owner"` rather than by the access
map, so they are not covered by the area checks elsewhere and nothing else
tested them. The gap between "can open the page" and "can see everything on
it" is exactly where a leak of this kind lives, and it leaks silently: nobody
gets an error, the wrong person just reads something.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPROF"
NOTE = "ZZPROF-NOTE keeps turning up late, spoke to them about it"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM check_in_notes WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?) OR body LIKE ?""",
                 (TAG + "%", TAG + "%"))
    conn.execute("""DELETE FROM offboarding_items WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM pay_rate_history WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name, preset=None):
    conn = db()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(users)").fetchall()}
    fields = {
        "name": f"{TAG} {name}", "email": f"{TAG.lower()}.{name.lower()}@example.invalid",
        "role": "employee", "status": "active", "job_role": "Housekeeping",
        "password_hash": "x", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if preset is not None and "access_preset" in cols:
        fields["access_preset"] = preset
    keys = [k for k in fields if k in cols]
    conn.execute(f"INSERT INTO users ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _as(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _notes(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM check_in_notes WHERE user_id = ? ORDER BY id DESC", (user_id,)
        ).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Profile privacy")
    _cleanup()
    oc, ec, owner, emp = clients()

    person = _employee("Léa")
    theirs = _as(person["id"])

    s.section("The owner records a 1:1 note")
    oc.post(f"/directory/{person['id']}/notes/new", data={"body": NOTE},
            follow_redirects=True)
    made = _notes(person["id"])
    s.check("it is saved", len(made) == 1, detail=f"{len(made)} note(s)")
    page = oc.get(f"/directory/{person['id']}")
    s.check("and the owner can read it back", NOTE in page.get_data(as_text=True), page)

    # Something in each of the other two private blocks, so their absence
    # later means "withheld" rather than "there was nothing to show".
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO offboarding_items (user_id, label, done, sort_order, created_at)
                    VALUES (?, ?, 0, 0, ?)""",
                 (person["id"], f"{TAG}-LEAVING hand back the tractor key", now))
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(pay_rate_history)").fetchall()}
    if {"user_id", "new_rate"} <= cols:
        conn.execute(
            f"INSERT INTO pay_rate_history (user_id, new_rate{', changed_at' if 'changed_at' in cols else ''}) "
            f"VALUES (?, ?{', ?' if 'changed_at' in cols else ''})",
            (person["id"], 13.37, now) if "changed_at" in cols else (person["id"], 13.37))
    conn.commit()
    conn.close()
    owner_sees = oc.get(f"/directory/{person['id']}").get_data(as_text=True)
    s.check("the owner sees the offboarding line too", "LEAVING" in owner_sees)

    s.section("The person themselves opens their own profile")
    own = theirs.get(f"/directory/{person['id']}")
    body = own.get_data(as_text=True)
    s.check("their own page opens", own.status_code == 200, own)
    s.check("their hours are on it", "Update phone" in body or "hours" in body.lower(),
            detail="the page is not actually rendering the employee view")
    s.check("the 1:1 note about them is NOT", NOTE not in body,
            detail="the owner's private record about somebody was shown to them")
    s.check("nor is the pay rate history", "13.37" not in body,
            detail="one person's page revealed a pay rate")
    s.check("nor what happens when they leave", "LEAVING" not in body,
            detail="an employee can see their own offboarding checklist")

    s.section("A colleague's profile is not theirs to open")
    colleague = _employee("Bastien")
    s.check("it is refused outright",
            theirs.get(f"/directory/{colleague['id']}").status_code in (302, 403),
            detail="one employee can read another's profile")

    s.section("Not even a manager reads somebody else's profile")
    # profile() is gated by ROLE, not by the access map: the owner, or the
    # person themselves, and nobody else. Team access runs the directory and
    # the rota; it does not open a colleague's file.
    manager = _employee("Karine", preset="karina")      # team, rota, estate
    mc = _as(manager["id"])
    s.check("somebody with team access can still run the team pages",
            mc.get("/directory").status_code == 200,
            detail="the preset under test grants no team access, so the next "
                   "check would pass for the wrong reason")
    mpage = mc.get(f"/directory/{person['id']}")
    s.check("but a colleague's profile is refused", mpage.status_code in (302, 403),
            detail=f"HTTP {mpage.status_code} — a manager opened somebody's file")
    s.check("their own profile still opens",
            mc.get(f"/directory/{manager['id']}").status_code == 200,
            detail="the guard is too broad — nobody can see their own page")

    s.section("Only the owner writes and removes those notes")
    before = len(_notes(person["id"]))
    theirs.post(f"/directory/{person['id']}/notes/new",
                data={"body": f"{TAG} written by the employee"}, follow_redirects=True)
    mc.post(f"/directory/{person['id']}/notes/new",
            data={"body": f"{TAG} written by a manager"}, follow_redirects=True)
    s.check("neither the employee nor a manager can add one",
            len(_notes(person["id"])) == before,
            detail=f"{before} -> {len(_notes(person['id']))} notes; writing was gated "
                   "by area while reading is gated by role, so team access could "
                   "write into a record it cannot read")

    s.section("Nor can they delete one they cannot read")
    # The worse half of the same gap: a manager could destroy the owner's
    # written record of a conversation, including one about themselves.
    kept = len(_notes(person["id"]))
    mc.post(f"/directory/{person['id']}/notes/{made[0]['id']}/delete", follow_redirects=True)
    s.check("the note survives a manager trying to remove it",
            len(_notes(person["id"])) == kept,
            detail="a manager deleted the owner's private note")

    s.section("An empty note is not stored")
    oc.post(f"/directory/{person['id']}/notes/new", data={"body": "   "},
            follow_redirects=True)
    s.check("whitespace does not become a note", len(_notes(person["id"])) == before)

    s.section("Deleting one is scoped to the person it is about")
    note_id = made[0]["id"]
    wrong = oc.post(f"/directory/{colleague['id']}/notes/{note_id}/delete",
                    follow_redirects=True)
    s.check("deleting it under somebody else's id does not remove it",
            len(_notes(person["id"])) == before, wrong)
    oc.post(f"/directory/{person['id']}/notes/{note_id}/delete",
            follow_redirects=True)
    s.check("but the owner can delete it properly", not _notes(person["id"]))

    _cleanup()
    return s
