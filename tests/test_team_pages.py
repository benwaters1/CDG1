"""The Team and Estate pages say who, and say it in words.

WHAT WENT WRONG. The same read-through as test_team_figures found pages that
were quietly incomplete:
- Cover: the people offered for an uncovered day included somebody without
  a certificate their role requires, which the rota-clash page would then
  have flagged on the shift they were offered for.
- Visitors: "Expecting them" printed a dash for every visitor; the host was
  queried and never handed to the page.
- Room faults: a guest arriving TODAY was filed under "soon" (0 days away read
  as 99), and a fault could be handed to somebody who had left.
- A list filtered by a default chip -- incidents open on "Open" -- lost that
  chip when nothing was in it, so the page read as unfiltered and empty.
- The asset register said "add the first item" over forty pieces when a
  search found none.
- Every notice listed the owner, who wrote it, as "not seen".
- Avatars took a name's first two letters: Demo Housekeeper and Demo Server
  were both DE, and the owner's home had two initials typed into it.
- Words: "1 rooms", "1 line name somebody", "1 covers", "— Pottery", "Scans 0
  inboxes", "3 days ago, window was 2", and website analytics stopping at
  forty pages without saying so.
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-tp-"
ROOT = Path(__file__).resolve().parent.parent


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE email LIKE ?", (TAG + "%",)).fetchall()]
    marks = ",".join("?" * len(ids)) or "NULL"
    for table in ("shifts", "certifications", "leave_requests", "absences"):
        conn.execute(f"DELETE FROM {table} WHERE user_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM site_visitors WHERE host_user_id IN ({marks}) OR name LIKE ?",
                 ids + [TAG + "%"])
    conn.execute("DELETE FROM role_requirements WHERE job_role LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG.upper() + "%",))
    conn.execute("DELETE FROM announcement_reads WHERE announcement_id IN "
                 "(SELECT id FROM announcements WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM announcements WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM page_views WHERE path LIKE ?", ("/" + TAG + "%",))
    conn.execute("DELETE FROM incidents WHERE summary LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM assets WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.commit()
    conn.close()


def _person(conn, name, *, job_role="General", status="active"):
    email = f"{TAG}{name.lower().replace(' ', '.')}@example.invalid"
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, 'x', 'employee', ?, ?, ?, ?)""",
        (email, name, job_role, status, _now()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def _booking(conn, ref, room_id, arrive, nights=2):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?)""",
        (room_id, TAG.upper() + ref, (TAG + ref).lower(), TAG + ref, "tp@example.invalid",
         arrive.isoformat(), (arrive + timedelta(days=nights)).isoformat(), _now()))
    conn.commit()


def _free_room(conn):
    return conn.execute(
        """SELECT id FROM rooms WHERE active = 1 AND id NOT IN
             (SELECT room_id FROM room_issues WHERE status = 'open') ORDER BY id LIMIT 1""").fetchone()


def run():
    s = Suite("Team pages")
    _cleanup()
    oc, _ec, owner, _emp = clients()
    today = house_today()
    conn = db()

    s.section("Cover is somebody able to work it")
    role = TAG + "Lifeguard"
    conn.execute("INSERT INTO role_requirements (job_role, requirement, requirement_type, "
                 "created_at) VALUES (?, 'Pool rescue', 'certification', ?)", (role, _now()))
    conn.commit()
    able = _person(conn, "Able Cover")
    unable = _person(conn, "Lapsed Cover", job_role=role)
    day = today + timedelta(days=40)
    lst = []
    free = m.who_could_cover(conn, day, not_qualified=lst)
    s.check("somebody with nothing against them is offered", any(p["user_id"] == able for p in free))
    s.check("somebody whose role needs a certificate they have not got is not",
            not any(p["user_id"] == unable for p in free))
    s.check("and is named as free but unable, with the reason",
            any(p["user_id"] == unable and "Pool rescue not on file" in p["why"] for p in lst),
            detail=str(lst))
    conn.execute("INSERT INTO certifications (user_id, name, expiry_date, created_at) "
                 "VALUES (?, 'Pool rescue', ?, ?)",
                 (unable, (day - timedelta(days=3)).isoformat(), _now()))
    conn.commit()
    lst = []
    free = m.who_could_cover(conn, day, not_qualified=lst)
    s.check("a certificate that has lapsed by that day does not cover it either",
            not any(p["user_id"] == unable for p in free)
            and any(p["user_id"] == unable and "expired" in p["why"] for p in lst),
            detail=str(lst))
    s.check("and the reason says its date in words",
            any(m.format_date_short((day - timedelta(days=3)).isoformat()) in p["why"] for p in lst),
            detail=str(lst))
    skills = oc.get(f"/admin/skills?day={day.isoformat()}").get_data(as_text=True)
    s.check("the skills page says who is free but cannot work it",
            "Free, but cannot work it" in skills and "Lapsed Cover" in skills)

    s.section("Visitors say who is expecting them")
    conn.execute("INSERT INTO site_visitors (name, host_user_id, signed_in_at, created_at) "
                 "VALUES (?, ?, ?, ?)", (TAG + "Plumber", able, _now(), _now()))
    conn.commit()
    here = [v for v in m.visitors_on_site(conn) if v["visitor"]["name"] == TAG + "Plumber"]
    s.check("the host is handed to the page", here and here[0].get("host") == "Able Cover",
            detail=str(here and here[0].get("host")))
    page = oc.get("/management/visitors").get_data(as_text=True)
    row = re.search(re.escape(TAG + "Plumber") + r".*?</tr>", page, re.S)
    s.check("and the row names them", row is not None and "Able Cover" in row.group(0),
            detail=row.group(0)[:300] if row else "no row")

    s.section("Room faults: today is this week, and leavers get nothing")
    room = _free_room(conn)
    if room:
        _booking(conn, "TODAY", room["id"], today)
        conn.execute("INSERT INTO room_issues (room_id, title, status, created_at) "
                     "VALUES (?, ?, 'open', ?)", (room["id"], TAG + "Shutter", _now()))
        conn.commit()
        week = oc.get("/room-issues?arriving=Guest+arriving+this+week").get_data(as_text=True)
        s.check("a fault in a room somebody arrives in today is under 'this week'",
                TAG + "Shutter" in week)
    else:
        s.check("a room without an open fault to test with", False)
    gone = _person(conn, "Gone Leaver", status="inactive")
    page = oc.get("/room-issues").get_data(as_text=True)
    pick = re.search(r'<select name="assigned_to_user_id">.*?</select>', page, re.S)
    s.check("a fault cannot be handed to somebody who has left",
            pick is not None and "Gone Leaver" not in pick.group(0) and "Able Cover" in pick.group(0))

    s.section("The chip a list is filtered by stays drawn when it is empty")
    rows = [{"status": "closed"}, {"status": "closed"}]
    lv = m.list_view(rows, {}, facets=[m.facet(
        "status", "Status", lambda r: r["status"], order=["open", "closed"],
        labels={"open": "Open", "closed": "Closed"}, default="open")])
    opts = {o["value"]: o for f in lv["facets"] for o in f["options"]}
    s.check("the default 'Open' chip is there, selected, with 0",
            "open" in opts and opts["open"]["selected"] and opts["open"]["count"] == 0,
            detail=str(opts))
    lv = m.list_view(rows, {"status": "nonsense"}, facets=[m.facet(
        "status", "Status", lambda r: r["status"], order=["open", "closed"], default="open")])
    s.check("a value the facet does not know draws no chip",
            not any(o["value"] == "nonsense" for f in lv["facets"] for o in f["options"]))

    s.section("The asset register: nothing matches is not nothing yet")
    conn.execute("INSERT INTO assets (name, estimated_value, status, created_at) "
                 "VALUES (?, 100, 'held', ?)", (TAG + "Clock", _now()))
    conn.commit()
    page = oc.get("/admin/assets?q=zzqqxx-nothing-like-this").get_data(as_text=True)
    s.check("a search that finds nothing says so, and offers the rest",
            "Nothing on the register matches that" in page and "add the first item" not in page)

    s.section("A notice is not unseen by the person who wrote it")
    conn.execute("INSERT INTO announcements (title, body, posted_by_user_id, created_at) "
                 "VALUES (?, '', ?, ?)", (TAG + "Fire drill", owner["id"], _now()))
    conn.commit()
    aid = conn.execute("SELECT id FROM announcements WHERE title = ?",
                       (TAG + "Fire drill",)).fetchone()["id"]
    r = m.announcement_readership(conn, [aid])[aid]
    staff = conn.execute("SELECT COUNT(*) AS c FROM users WHERE status = 'active' "
                         "AND role = 'employee'").fetchone()["c"]
    s.check("the owner is not on the not-seen list", owner["name"] not in r["not_seen"],
            detail=str(r["not_seen"]))
    s.check("and the total is the staff", r["total"] == staff, detail=f"{r['total']} vs {staff}")

    s.section("Initials are a person's initials")
    _person(conn, "Zora Quill")
    page = oc.get("/directory").get_data(as_text=True)
    s.check("Zora Quill is ZQ, not ZO", '<div class="profile-avatar">ZQ</div>' in page)
    home = (ROOT / "templates" / "owner_home.html").read_text(encoding="utf-8")
    s.check("the owner's home draws the owner's initials, not two typed letters",
            "'BW'" not in home and "user['name']|initials" in home)

    s.section("Counts are said in words")
    y = today.year + 3
    room = _free_room(conn)
    if room:
        _booking(conn, "ONE", room["id"], date(y, 3, 3), nights=1)
    strip = oc.get(f"/admin/shifts?date={y}-03-03").get_data(as_text=True)
    s.check("one occupied room is '1 room'",
            re.search(r'>\s*1 <span[^>]*>room</span>', strip) is not None)
    s.check("and never '1 rooms'", re.search(r'>\s*1 <span[^>]*>rooms</span>', strip) is None)

    occurred = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    conn.execute("INSERT INTO incidents (kind, occurred_at, summary, severity, created_at) "
                 "VALUES ('workplace', ?, ?, 'minor', ?)", (occurred, TAG + "Ladder", _now()))
    conn.commit()
    page = oc.get("/admin/incidents").get_data(as_text=True)
    row = re.search(re.escape(TAG + "Ladder") + r".*?</span>", page, re.S)
    s.check("an overdue notice says '3 days ago, the window was 2 days'",
            row is not None and "3 days ago, the window was 2 days" in row.group(0),
            detail=row.group(0)[:300] if row else "no row")

    first = date(y, 3, 1)
    for i in range(43):
        conn.execute("INSERT INTO page_views (day, at, visitor, path, status, entry) "
                     "VALUES (?, ?, ?, ?, 200, 1)",
                     (first.isoformat(), f"{first.isoformat()}T10:00:00+00:00", f"v{i}",
                      f"/{TAG}{i:02d}"))
    conn.commit()
    page = oc.get(f"/admin/analytics?period=month&date={y}-03-01").get_data(as_text=True)
    s.check("analytics says how many pages it left off the list",
            "And 3 more pages, read 3 times between them." in page)
    s.check("and how many first pages", "And 18 more first pages, 18 visits between them." in page)

    s.section("Arrival prep notes say their date in words")
    s.check("a new prep note is day first, in words",
            m.prep_note({"guest_name": "Rousseau", "arrival_date": "2026-10-13", "party_size": 2})
            == "Rousseau arriving 13 October 2026, party of 2.")
    conn.execute("INSERT INTO tasks (title, room_note, created_at) VALUES (?, ?, ?)",
                 (TAG + "Prep", "Rousseau arriving 2026-10-13, party of 2. Requests: late", _now()))
    conn.commit()
    first = m.prep_notes_in_words(conn)
    note = conn.execute("SELECT room_note FROM tasks WHERE title = ?", (TAG + "Prep",)).fetchone()
    s.check("and one already stored the database's way is rewritten at startup",
            note["room_note"] == "Rousseau arriving 13 October 2026, party of 2. Requests: late",
            detail=note["room_note"])
    s.check("which finds nothing the second time", first >= 1 and m.prep_notes_in_words(conn) == 0)

    flags = (ROOT / "templates" / "admin_inbox_flags.html").read_text(encoding="utf-8")
    s.check("inbox flags never says 'Scans 0 inboxes'",
            "{% if mailboxes %}Scans" in flags and "No inbox is being watched yet" in flags)

    conn.close()
    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
