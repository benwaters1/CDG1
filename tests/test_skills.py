"""The skills field, asked properly.

Every profile can hold skills. They were typed in, shown as chips, and read
by nothing — so "who could cover Tuesday" was unanswerable, which is the one
question the rota pages leave the owner to solve in their head.

It is free text. The checks that matter are the ones that keep it honest: a
near-match is NOT merged, the spelling shown does not depend on the order
people happen to be listed in, and "free" means only that the app has nothing
else against somebody.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-skill-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM absences WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name, skills, status="active"):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           skills, created_at) VALUES (?, 'x', 'employee', ?, 'General', ?, ?, ?)""",
        (f"{TAG}{name}@example.invalid", name, status, skills,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _skill(matrix, display):
    return next((s for s in matrix["skills"] if s["key"] == display.casefold()), None)


def run():
    s = Suite("skills")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("Reading the field")
    s.check("commas separate, spaces do not",
            [d for _k, d in m.parse_skills("pool chemicals, first aid")]
            == ["pool chemicals", "first aid"])
    # chr(10) rather than an escape, so what is being tested cannot be
    # confused by how this file itself is written.
    s.check("newlines and semicolons separate too",
            len(m.parse_skills("a" + chr(10) + "b; c")) == 3,
            detail=str(m.parse_skills("a" + chr(10) + "b; c")))
    s.check("blank entries are dropped", m.parse_skills(" , ,") == [])
    s.check("the same skill twice is once",
            len(m.parse_skills("First aid, first aid")) == 1)
    s.check("empty is empty, not a skill called nothing", m.parse_skills(None) == [])

    s.section("Who has what")
    ana = _person(conn, "Ana", "First aid, pool chemicals, chainsaw")
    ben = _person(conn, "Ben", "first aid , Pool Chemicals")
    _person(conn, "Cal", "")
    _person(conn, "Old", "chainsaw", status="inactive")

    mx = m.skill_matrix(conn)
    fa = _skill(mx, "first aid")
    s.check("case and spacing are matched through", fa and fa["count"] == 2,
            detail=str(fa["count"]) if fa else "")
    s.check("both spellings are reported, not hidden",
            fa and len(fa["spellings"]) == 2, detail=str(fa["spellings"]) if fa else "")

    # The whole point of the page.
    saw = _skill(mx, "chainsaw")
    s.check("a skill only one active person has is flagged",
            any(x["key"] == "chainsaw" for x in mx["held_by_one"]))
    s.check("and somebody who has left does not count towards it",
            saw and saw["count"] == 1, detail=str(saw["count"]) if saw else "")

    s.check("somebody with nothing recorded is named rather than silently absent",
            "Cal" in mx["without_any"], detail=str(mx["without_any"]))

    # A near-match must NOT merge: on a rota, somebody who appears able to do
    # something they cannot is worse than a duplicate row.
    _person(conn, "Dee", "firstaid")
    mx = m.skill_matrix(conn)
    # Guarded rather than indexed straight in: a fuzzy-merge bug makes the
    # second lookup None, and a crash stops the rest of the suite instead of
    # naming what broke.
    near, spaced = _skill(mx, "firstaid"), _skill(mx, "first aid")
    s.check("a near-match is left as its own skill",
            near is not None and spaced is not None and spaced["count"] == 2,
            detail=str([x["display"] for x in mx["skills"]]))

    s.section("The spelling shown does not depend on who is listed first")
    # Two people write it one way, one writes it another. The majority wins,
    # rather than whoever happens to sort first.
    _person(conn, "Eve", "Pool Chemicals")
    mx = m.skill_matrix(conn)
    pc = _skill(mx, "pool chemicals")
    s.check("the most common spelling is the one displayed",
            pc and pc["display"] == "Pool Chemicals", detail=str(pc["display"]) if pc else "")

    s.section("Who could cover a day")
    day = _iso(4)
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (ana, day, TAG + "shift", datetime.now(timezone.utc).isoformat()))
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at)
           VALUES (?, ?, ?, ?, 'annual', 'approved', ?)""",
        (ben, day, day, TAG + "off", datetime.now(timezone.utc).isoformat()))
    conn.commit()

    free = m.who_could_cover(conn, day)
    names = {p["name"] for p in free}
    s.check("somebody already rostered is not free", "Ana" not in names)
    s.check("somebody on approved leave is not free", "Ben" not in names)
    s.check("somebody with nothing against them is", "Cal" in names, detail=str(names))
    s.check("and somebody who has left is never offered", "Old" not in names)

    s.check("asking for a skill narrows it",
            {p["name"] for p in m.who_could_cover(conn, day, skill="firstaid")} == {"Dee"},
            detail=str([p["name"] for p in m.who_could_cover(conn, day, skill="firstaid")]))
    s.check("asking case-insensitively works too",
            {p["name"] for p in m.who_could_cover(conn, day, skill="FIRSTAID")} == {"Dee"})
    s.check("a skill nobody free has returns nobody, not everybody",
            m.who_could_cover(conn, day, skill="chainsaw") == [],
            detail="Ana has it and is already rostered")

    s.section("The pages")
    page = oc.get("/admin/skills").get_data(as_text=True)
    s.check("it renders", "Who can do what" in page)
    s.check("the single-holder skill is called out", "Only one person can" in page)
    s.check("and the two spellings are shown rather than quietly merged",
            "Spelt more than one way" in page)
    s.check("it says what 'free' does not know", "whose day off is an arrangement" in page)

    cover = oc.get("/admin/cover?days=30").get_data(as_text=True)
    s.check("the cover page offers this page", "/admin/skills" in cover)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
