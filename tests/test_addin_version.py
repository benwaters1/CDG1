"""A new Outlook add-in reaches the owner as a task (22 Oct 2026).

Microsoft 365 keeps handing every Outlook the manifest it fetched last, until
somebody opens the add-in in Integrated apps and chooses Update. The add-in
page said "when this page shows a new version number... choose Update", and
nothing told anybody the number had changed: a fix could ship, the app could
serve it, and every Outlook in the house would carry on without it, with
nothing anywhere saying so.

So the owner says, with a button on the add-in page, which version Microsoft
has fetched. While the app serves another, the owner home says so and a watch
task is raised; pressing the button closes both.

WHAT IS CHECKED

  With nothing recorded and no Outlook ever having reached the add-in, nothing
  is said: an add-in nobody installed has nothing to update.
  Once one has, an unrecorded version is on the owner home, linked to the
  button, and raised as a task that names the page.
  The page offers the button for the version it shows, and says it is not
  recorded.
  Pressing it records the version, who and when; the home line goes, and the
  task closes there and then, and the next pass does not raise it again.
  A new version reopens it, naming the one Microsoft was last updated to.
  A press from a page opened before the change is refused, because Microsoft
  may not have the new one; so is a press that names no version.
  Pressing twice records once. Staff cannot press it.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ztest-addin-version-"
TITLE_START = "Update the Outlook add-in to version "
NEXT = "9.9.0.0"


def _warnings(conn):
    with m.app.test_request_context():
        return [w for w in m.owner_home_warnings(conn, m.house_today())
                if w["title"].startswith(TITLE_START)]


def _found(conn):
    found, _dropped = m.watch_task_findings(conn, m.house_today())
    return [f for f in found if f[0] == "addin_version"]


def _tasks(conn, status_open=True):
    return conn.execute(
        f"""SELECT * FROM tasks WHERE origin = ? AND title LIKE ?
             AND status {'!=' if status_open else '='} 'done' ORDER BY id""",
        (m.WATCH_TASK_ORIGIN, TITLE_START + "%")).fetchall()


def _confirmed(conn):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (m.ADDIN_CONFIRMED_VERSION_SETTING,)).fetchone()
    return row["value"] if row else None


def _press(client, version):
    """(the answer to the press, what the page it went back to said)."""
    r = client.post("/admin/outlook-addin/updated", data={"version": version})
    return r, flashes(client.get("/admin/outlook-addin"))


def run():
    s = Suite("A new Outlook add-in reaches the owner as a task")
    _oc, ec, owner, _emp = clients()
    oc = m.app.test_client()
    with oc.session_transaction() as sess:
        sess["user_id"] = owner["id"]
    conn = db()
    put_back = [
        _harness.keep_rows(conn, "app_settings", "key = ?",
                           (m.ADDIN_CONFIRMED_VERSION_SETTING,)),
        _harness.keep_rows(conn, "tasks", "origin = ? AND title LIKE ?",
                           (m.WATCH_TASK_ORIGIN, TITLE_START + "%")),
        _harness.keep_rows(conn, "audit_log", "action = 'addin_manifest_updated'"),
        # Emptied for the first section only, and every row put back: they
        # belong to the suites that signed those Outlooks in.
        _harness.keep_rows(conn, "addin_sessions", "1 = 1"),
        _harness.keep_rows(conn, "addin_check_keys", "1 = 1"),
        _harness.keep_rows(conn, "addin_send_checks", "1 = 1"),
    ]
    running = m.OUTLOOK_ADDIN_VERSION
    try:
        _run(s, oc, ec, owner, conn, running)
    finally:
        m.OUTLOOK_ADDIN_VERSION = running
        for undo in reversed(put_back):
            undo()
        conn.close()
    return s


def _run(s, oc, ec, owner, conn, running):
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.ADDIN_CONFIRMED_VERSION_SETTING,))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title LIKE ?",
                 (m.WATCH_TASK_ORIGIN, TITLE_START + "%"))
    for table in ("addin_sessions", "addin_check_keys", "addin_send_checks"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()

    s.section("An add-in nobody has installed has nothing to update")
    s.check("nothing on the owner home", _warnings(conn) == [], detail=str(_warnings(conn)))
    s.check("and no task", _found(conn) == [], detail=str(_found(conn)))
    # Once a version is recorded, the house has said it has the add-in, so a
    # change is news even with no Outlook seen yet.
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                 (m.ADDIN_CONFIRMED_VERSION_SETTING, "1.0.0.0"))
    conn.commit()
    s.check("but a recorded older version is still said",
            len(_warnings(conn)) == 1, detail=str(_warnings(conn)))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.ADDIN_CONFIRMED_VERSION_SETTING,))

    s.section("Once an Outlook has used it, an unrecorded version is said")
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO addin_check_keys (identity, token_hash, label, created_at, expires_at)
           VALUES (?, ?, 'Outlook on the web', ?, ?)""",
        (TAG + "a@example.invalid", TAG + "hash", now.isoformat(),
         (now + timedelta(days=30)).isoformat()))
    conn.commit()
    title = TITLE_START + running + " in Microsoft 365"
    found = _warnings(conn)
    s.check("the owner home says the add-in needs updating",
            [w["title"] for w in found] == [title], detail=str(found))
    s.check("it says nobody has said which version Microsoft has",
            found and "nobody has yet said" in found[0]["detail"]
            and "Integrated apps" in found[0]["detail"],
            detail=found[0]["detail"] if found else "")
    s.check("and links to the button on the add-in page",
            found and found[0]["href"].endswith("/admin/outlook-addin#update"),
            detail=found[0]["href"] if found else "")
    s.check("the kind has somebody it can be routed to",
            "addin_version" in m.WATCH_TASK_KINDS)
    m.generate_watch_tasks(conn)
    raised = _tasks(conn)
    s.check("a task is raised for it", [t["title"] for t in raised] == [title],
            detail=str([dict(t) for t in raised]))
    s.check("which names the page to put it right on",
            raised and "/admin/outlook-addin" in (raised[0]["notes"] or ""),
            detail=raised[0]["notes"] if raised else "")
    home = oc.get("/admin")
    s.check("and the owner's home page prints it",
            home.status_code == 200 and title in home.get_data(as_text=True), home)

    s.section("The page offers the button for the version it shows")
    page = oc.get("/admin/outlook-addin")
    html = page.get_data(as_text=True)
    form = next((f for f in _harness.forms_on(html)
                 if f["action"].endswith("/admin/outlook-addin/updated")), None)
    s.check("the add-in page has the button", page.status_code == 200 and form is not None, page)
    s.check("it carries the version on the page",
            form is not None and {f["name"]: f["value"] for f in form["fields"]}.get("version") == running,
            detail=str(form and form["fields"]))
    s.check("and the page says the version is not recorded",
            "not recorded" in _harness.visible_text(html))

    s.section("Pressing it records the version and closes both")
    r, told = _press(oc, running)
    s.check("the press is taken", r.status_code == 302
            and r.headers["Location"].endswith("/admin/outlook-addin#update")
            and any(f.startswith("Recorded") for f in told), r, detail=str(told))
    s.check("the version is stored", _confirmed(conn) == running, detail=str(_confirmed(conn)))
    said = conn.execute(
        """SELECT * FROM audit_log WHERE action = 'addin_manifest_updated' AND target = ?""",
        (running,)).fetchall()
    s.check("who pressed it is on the record",
            len(said) == 1 and said[0]["actor_user_id"] == owner["id"],
            detail=str([dict(a) for a in said]))
    s.check("the owner home line has gone", _warnings(conn) == [], detail=str(_warnings(conn)))
    s.check("the task closed there and then", _tasks(conn) == []
            and [t["title"] for t in _tasks(conn, status_open=False)] == [title],
            detail=str([dict(t) for t in _tasks(conn)]))
    m.generate_watch_tasks(conn)
    s.check("and the next pass does not raise it again", _tasks(conn) == [],
            detail=str([dict(t) for t in _tasks(conn)]))
    text = _harness.visible_text(oc.get("/admin/outlook-addin").get_data(as_text=True))
    s.check("the page says Microsoft has it, and who said so",
            "up to date" in text and owner["name"] in text, detail=text[:400])
    page_forms = _harness.forms_on(oc.get("/admin/outlook-addin").get_data(as_text=True))
    s.check("and no longer offers the button",
            not any(f["action"].endswith("/admin/outlook-addin/updated") for f in page_forms))

    s.section("A new version reopens it")
    m.OUTLOOK_ADDIN_VERSION = NEXT
    new_title = TITLE_START + NEXT + " in Microsoft 365"
    found = _warnings(conn)
    s.check("the owner home says so again", [w["title"] for w in found] == [new_title],
            detail=str(found))
    s.check("naming the version Microsoft was last updated to",
            found and f"last updated to {running}" in found[0]["detail"],
            detail=found[0]["detail"] if found else "")
    m.generate_watch_tasks(conn)
    s.check("and raises a task for the new version",
            [t["title"] for t in _tasks(conn)] == [new_title],
            detail=str([t["title"] for t in _tasks(conn)]))
    text = _harness.visible_text(oc.get("/admin/outlook-addin").get_data(as_text=True))
    s.check("the page says Microsoft's copy is out of date", "out of date" in text)

    s.section("A press for a version the app no longer serves is refused")
    r, told = _press(oc, running)
    s.check("a page opened before the change cannot record the new one",
            r.status_code == 302 and _confirmed(conn) == running
            and any("changed while the page was open" in f for f in told),
            r, detail=str(told))
    r, told = _press(oc, "")
    s.check("nor can a press that names no version",
            r.status_code == 302 and _confirmed(conn) == running
            and any(f.startswith("Not recorded") for f in told), r, detail=str(told))
    s.check("so the line is still there", len(_warnings(conn)) == 1)
    r = ec.post("/admin/outlook-addin/updated", data={"version": NEXT})
    s.check("staff cannot press it", r.status_code in (302, 403)
            and "/admin/outlook-addin" not in r.headers.get("Location", ""), r,
            detail=f"{r.status_code} {r.headers.get('Location')}")
    s.check("and nothing was recorded for them", _confirmed(conn) == running)

    s.section("Pressing for the version it shows closes it; pressing again records once")
    r, told = _press(oc, NEXT)
    s.check("the new version is recorded", _confirmed(conn) == NEXT, r, detail=str(told))
    s.check("its task closed", _tasks(conn) == [], detail=str([t["title"] for t in _tasks(conn)]))
    r, told = _press(oc, NEXT)
    s.check("a second press says it was already recorded",
            any(f.startswith("Already recorded") for f in told), r, detail=str(told))
    n = conn.execute("""SELECT COUNT(*) FROM audit_log
                         WHERE action = 'addin_manifest_updated' AND target = ?""",
                     (NEXT,)).fetchone()[0]
    s.check("and the record has it once", n == 1, detail=str(n))


if __name__ == "__main__":
    print(run().report())
