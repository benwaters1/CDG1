"""A standard week you can stamp onto any week.

There was already "copy last week", and it is not the same thing. Last week
had somebody off sick, a wedding on the Saturday and two people doubled up
for it; copying that forward carries the accident with the pattern, and by
August the rota is a photocopy of a photocopy of one odd week in May.

The whole weight of this file is on what the stamp DOES NOT DO, because that
is the half every bulk action in this app got wrong before somebody wrote it
down:

  - It does not roster somebody on their approved holiday. It leaves them
    off and NAMES THEM. A stamp that quietly puts Marie on the Tuesday she
    is in Spain produces a rota that looks complete and is not, and the hole
    is discovered when nobody arrives. Left off, the day reads as uncovered,
    which is what it is, and Nobody On asks for somebody to fill it.
  - It does not double a shift already on the rota, or overwrite the fixes
    somebody made by hand.
  - It does not put a name on the rota that nobody can ring.

And it says all three through bulk_message, which names items rather than
counting them and is an ERROR the moment anything is skipped — because a
bulk action that half worked is exactly the thing that must not look clean.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZROTATPL"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM rota_template_shifts WHERE template_id IN "
        "(SELECT id FROM rota_templates WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM rota_templates WHERE name LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("standard weeks")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    # Next Monday, so nothing here collides with a real rota.
    today = house_today()
    week = today - timedelta(days=today.weekday()) + timedelta(days=28)

    def add_user(name, status="active"):
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role,
                                  status, created_at)
               VALUES (?, 'x', 'employee', ?, 'General', ?, ?)""",
            (f"{TAG}.{name}@example.invalid".lower(), TAG + " " + name,
             status, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    marie = add_user("Marie")
    olivier = add_user("Olivier")
    left = add_user("Thierry", status="inactive")

    conn.execute(
        "INSERT INTO rota_templates (name, note, active, created_at) "
        "VALUES (?, 'high season', 1, ?)", (TAG + " High Season", now))
    tpl = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def add_line(user_id, weekday, start="08:00", end="16:00"):
        conn.execute(
            """INSERT INTO rota_template_shifts (template_id, user_id, weekday,
                       start_time, end_time, role_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tpl, user_id, weekday, start, end, TAG + " breakfast", now))

    add_line(marie, 0)                  # Monday
    add_line(marie, 1)                  # Tuesday — she is away
    add_line(olivier, 1)                # Tuesday
    add_line(olivier, 5)                # Saturday
    add_line(left, 3)                   # Thursday — he has gone

    # Marie is in Spain on the Tuesday.
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
                   leave_type, status, requested_at)
           VALUES (?, ?, ?, ?, 'holiday', 'approved', ?)""",
        (marie, (week + timedelta(days=1)).isoformat(),
         (week + timedelta(days=2)).isoformat(), TAG + " Spain", now))

    # Olivier's Saturday is already on the rota, put there by hand.
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time,
                   role_note, created_at)
           VALUES (?, ?, '08:00', '16:00', ?, ?)""",
        (olivier, (week + timedelta(days=5)).isoformat(),
         TAG + " already there", now))
    conn.commit()

    placed, skipped = m.apply_rota_template(conn, tpl, week)
    conn.commit()
    by_reason = {}
    for label, reason in skipped:
        by_reason.setdefault(reason, []).append(label)

    s.section("It places what it can")
    s.check("two shifts go on", placed == 2,
            detail=f"{placed} placed of 5 lines")
    got = {(r["user_id"], r["shift_date"]) for r in conn.execute(
        "SELECT user_id, shift_date FROM shifts WHERE role_note = ?",
        (TAG + " breakfast",)).fetchall()}
    s.check("Marie's Monday", (marie, week.isoformat()) in got)
    s.check("and Olivier's Tuesday",
            (olivier, (week + timedelta(days=1)).isoformat()) in got)

    s.section("It does not roster somebody on their approved holiday")
    s.check("Marie's Tuesday is not written",
            (marie, (week + timedelta(days=1)).isoformat()) not in got,
            detail="a rota that puts her on the Tuesday she is in Spain looks "
                   "complete and is not, and the hole shows up when nobody "
                   "arrives")
    s.check("and she is named, not counted",
            any(TAG + " Marie" in lbl for lbl in by_reason.get("on approved leave", [])),
            detail=str(by_reason.get("on approved leave")))
    s.check("with the day said as well as the name",
            any("Tuesday" in lbl for lbl in by_reason.get("on approved leave", [])),
            detail="'Marie was skipped' on a five-day template is not an "
                   "answer somebody can act on")

    s.section("It does not double what is already on the rota")
    saturday = conn.execute(
        "SELECT COUNT(*) FROM shifts WHERE user_id = ? AND shift_date = ?",
        (olivier, (week + timedelta(days=5)).isoformat())).fetchone()[0]
    s.check("Olivier still has one Saturday, not two", saturday == 1,
            detail=f"{saturday} shifts")
    s.check("and the hand-written one is the one that survived",
            conn.execute(
                "SELECT role_note FROM shifts WHERE user_id = ? AND shift_date = ?",
                (olivier, (week + timedelta(days=5)).isoformat())
            ).fetchone()["role_note"] == TAG + " already there",
            detail="stamping a template must not overwrite the fixes")
    s.check("and it says so", "already on the rota" in by_reason,
            detail=str(list(by_reason)))

    s.section("It does not put somebody who has left on the rota")
    s.check("Thierry's Thursday is not written",
            (left, (week + timedelta(days=3)).isoformat()) not in got)
    s.check("and it says why",
            any(TAG + " Thierry" in lbl
                for lbl in by_reason.get("no longer works here", [])),
            detail=str(by_reason.get("no longer works here")))

    s.section("The message is an error, not a cheerful total")
    msg, cat = m.bulk_message("Placed", "shift", placed, skipped)
    s.check("it is flagged as an error", cat == "error",
            detail=f"{cat}: {msg} — a bulk action that half worked is exactly "
                   "the thing that must not look clean")
    s.check("it says how many of how many", "2 of 5 shifts" in msg, detail=msg)
    for word in ("Marie", "Olivier", "Thierry"):
        s.check(f"{word} is named in it", word in msg, detail=msg)
    s.check("and the reasons are in it",
            "approved leave" in msg and "already on the rota" in msg
            and "no longer works here" in msg,
            detail=msg)

    s.section("Stamping the same week twice changes nothing")
    # The second run has to be a no-op, or somebody who clicks twice — or
    # reloads the page after it — doubles the whole week.
    before = conn.execute("SELECT COUNT(*) FROM shifts WHERE role_note = ?",
                          (TAG + " breakfast",)).fetchone()[0]
    again_placed, again_skipped = m.apply_rota_template(conn, tpl, week)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM shifts WHERE role_note = ?",
                         (TAG + " breakfast",)).fetchone()[0]
    s.check("nothing new is placed", again_placed == 0, detail=str(again_placed))
    s.check("and the count is unchanged", after == before,
            detail=f"{before} then {after}")
    s.check("every line is accounted for", len(again_skipped) == 5,
            detail=f"{len(again_skipped)} of 5 lines said something")

    s.section("A template holds a SHAPE, not a week")
    # Storing a date here is how a template turns into another copy of one
    # week. Stamping it somewhere else must land on the same weekdays.
    other = week + timedelta(days=7)
    m.apply_rota_template(conn, tpl, other)
    conn.commit()
    landed = [r["shift_date"] for r in conn.execute(
        "SELECT shift_date FROM shifts WHERE user_id = ? AND role_note = ? "
        "ORDER BY shift_date", (olivier, TAG + " breakfast"))]
    s.check("the next week gets its own Tuesday",
            (other + timedelta(days=1)).isoformat() in landed, detail=str(landed))
    s.check("and every date landed is a Tuesday or a Saturday",
            all(m.parse_date(d).weekday() in (1, 5) for d in landed),
            detail=str([(d, m.parse_date(d).strftime("%a")) for d in landed]))

    # -------------------------------------------------------------- pages
    s.section("The page")
    r = oc.get("/admin/rota-templates")
    body = r.get_data(as_text=True)
    s.check("the owner can open it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("the template is on it", TAG + " High Season" in body)
    s.check("and it says how many name somebody who has left",
            "no longer works here" in body,
            detail="found on the page rather than on the Monday")
    detail = oc.get(f"/admin/rota-templates?template={tpl}").get_data(as_text=True)
    s.check("opening one shows its lines", TAG + " Marie" in detail)
    s.check("with the day named in English", "Saturday" in detail)
    r = ec.get("/admin/rota-templates", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    s.section("Stamping through the page reports the same way")
    third = week + timedelta(days=14)
    r = oc.post("/admin/rota-templates",
                data={"what": "apply", "template_id": str(tpl),
                      "week_start": (third + timedelta(days=3)).isoformat()},
                follow_redirects=True)
    page = r.get_data(as_text=True)
    # The REASON, not the name. It lands on the shifts page, which lists
    # these people's shifts anyway -- so looking for a name found the rota
    # rather than the message, and went on passing with the message replaced
    # by a cheerful total.
    #
    # Marie's leave is in the FIRST week only, so the reason that survives to
    # this one is Thierry having left. Which is the point: the message has to
    # carry whatever it actually skipped, not one rehearsed case.
    s.check("the message says what it left off and why",
            "no longer works here" in page,
            detail="a total with nobody named in it reads as finished")
    s.check("and how many of how many", "of 5 shifts" in page,
            detail="'Placed 4 shifts' is the shape this app decided not to "
                   "use, because it looks like it did everything")
    # Whatever day was picked, the week it belongs to. A Thursday click that
    # stamped Thursday-to-Wednesday would put the Saturday cover on the
    # wrong Saturday.
    monday_landed = conn.execute(
        "SELECT COUNT(*) FROM shifts WHERE role_note = ? AND shift_date = ?",
        (TAG + " breakfast", third.isoformat())).fetchone()[0]
    s.check("a Thursday click still stamps the Monday-to-Sunday week",
            monday_landed == 1, detail=f"{monday_landed} on {third}")

    s.section("Putting one away leaves the shifts alone")
    stamped = conn.execute("SELECT COUNT(*) FROM shifts WHERE role_note = ?",
                           (TAG + " breakfast",)).fetchone()[0]
    oc.post(f"/admin/rota-templates/{tpl}/delete", follow_redirects=True)
    s.check("it is gone from the list",
            TAG + " High Season" not in
            oc.get("/admin/rota-templates").get_data(as_text=True))
    s.check("and every shift already stamped from it stands",
            conn.execute("SELECT COUNT(*) FROM shifts WHERE role_note = ?",
                         (TAG + " breakfast",)).fetchone()[0] == stamped,
            detail="a shift is a real shift once it is on the rota; what goes "
                   "is the offer to stamp it again")

    s.section("It is reachable")
    nav = oc.get("/").get_data(as_text=True)
    nav = nav[:nav.find("</nav>")] if "</nav>" in nav else nav
    s.check("in the nav", "/admin/rota-templates" in nav)
    s.check("and in the palette",
            "rota_templates_page" in {e for _l, e, _k in m.PALETTE_PAGES})

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
