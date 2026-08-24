"""Work the house needs on a rhythm, rather than when something breaks.

Everything the app had for upkeep was reactive — a room issue or a vehicle
fault is something a person noticed. That is fine for a dripping tap. It is
useless for a chimney, because an unswept chimney does not report itself, and
the first anyone hears of it can be an insurer asking for the certificate.

Three behaviours are worth testing hardest, and all three are about time:
the next date must follow the day the work was DONE rather than the day it was
due; a schedule already carrying an open task must not raise another one every
morning; and "overdue" must be told apart from "overdue and somebody outside
the house is expecting it", because only the second is an exposure.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "maint-"


def _cleanup(conn):
    conn.execute("DELETE FROM maintenance_visits WHERE schedule_id IN "
                 "(SELECT id FROM maintenance_schedules WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_schedules WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'maintenance' AND title LIKE ?",
                 ("%" + TAG + "%",))
    conn.commit()


def _schedule(conn, name, **kw):
    fields = {"name": TAG + name, "category": "building", "location": None,
              "every_months": 12, "last_done_on": None, "next_due_on": None,
              "lead_days": 21, "assigned_to_user_id": None, "vendor_id": None,
              "required_by": "none", "insurance_policy_id": None, "notes": None,
              "active": 1, "generated_through": None,
              "created_at": datetime.now(timezone.utc).isoformat()}
    fields.update(kw)
    conn.execute(f"INSERT INTO maintenance_schedules ({', '.join(fields)}) "
                 f"VALUES ({', '.join('?' * len(fields))})", list(fields.values()))
    conn.commit()
    return conn.execute("SELECT * FROM maintenance_schedules WHERE name = ?",
                        (TAG + name,)).fetchone()


def _tasks(conn, name):
    return conn.execute(
        """SELECT * FROM tasks WHERE origin = 'maintenance' AND title LIKE ?
           ORDER BY id""", ("%" + TAG + name + "%",)).fetchall()


def run():
    s = Suite("The estate's upkeep")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = datetime.now(m.LOCAL_TZ).date()

    def d(n):
        return (today + timedelta(days=n)).isoformat()

    s.section("Knowing when it is next owed")
    far = _schedule(conn, "Roof", next_due_on=d(200))
    soon = _schedule(conn, "Gutters", next_due_on=d(10), lead_days=21)
    late = _schedule(conn, "Sweep", next_due_on=d(-40), required_by="insurance")
    never = _schedule(conn, "Trees")
    s.check("something months away is just scheduled",
            m.maintenance_status(far, today)["state"] == "scheduled",
            detail=m.maintenance_status(far, today)["state"])
    s.check("something inside its notice period is due soon",
            m.maintenance_status(soon, today)["state"] == "soon",
            detail=m.maintenance_status(soon, today)["state"])
    s.check("something past its date is overdue",
            m.maintenance_status(late, today)["state"] == "overdue")
    s.check("and it says by how long",
            "40 days overdue" in m.maintenance_status(late, today)["label"],
            detail=m.maintenance_status(late, today)["label"])
    s.check("something never recorded says so rather than guessing",
            m.maintenance_status(never, today)["state"] == "unknown",
            detail=m.maintenance_status(never, today)["state"])

    s.section("A date can be worked out from when it was last done")
    derived = _schedule(conn, "Boiler", last_done_on=d(-300), every_months=12)
    due = m.maintenance_next_due(derived)
    s.check("twelve months after the last service", due and due.year == (
        m.parse_date(d(-300)).year + 1), detail=str(due))

    s.section("Tasks are raised for what is due, and nothing else")
    made = m.generate_maintenance_tasks(conn, today=today)
    s.check("something raised", made >= 2, detail=f"{made} raised")
    s.check("the overdue one has a task", len(_tasks(conn, "Sweep")) == 1)
    s.check("so does the one due soon", len(_tasks(conn, "Gutters")) == 1)
    s.check("the one months away does not", len(_tasks(conn, "Roof")) == 0,
            detail="a task raised months early is a task ignored months early")
    s.check("nor does one nobody has ever recorded a date for",
            len(_tasks(conn, "Trees")) == 0)

    s.section("Overdue and expected is urgent; overdue alone is not")
    # The distinction the page is built on. A chimney the insurer asks about
    # is not the same as gutters nobody but the house cares about.
    sweep_task = _tasks(conn, "Sweep")[0]
    s.check("the insurer's one is high priority", sweep_task["priority"] == "high",
            detail=sweep_task["priority"])
    s.check("and its note says who is asking",
            "insurer" in (sweep_task["notes"] or "").lower(),
            detail=str(sweep_task["notes"])[:70])
    s.check("and to keep the certificate",
            "certificate" in (sweep_task["notes"] or "").lower())
    ours = _schedule(conn, "Windows", next_due_on=d(-40), required_by="none")
    m.generate_maintenance_tasks(conn, today=today)
    win = _tasks(conn, "Windows")
    s.check("one only we care about is normal priority",
            win and win[0]["priority"] == "normal",
            detail=win[0]["priority"] if win else "?")

    s.section("Running again does not fill the list with the same line")
    before = len(_tasks(conn, "Sweep"))
    m.generate_maintenance_tasks(conn, today=today)
    m.generate_maintenance_tasks(conn, today=today + timedelta(days=1))
    s.check("still one task for it", len(_tasks(conn, "Sweep")) == before,
            detail=f"{len(_tasks(conn, 'Sweep'))} tasks")

    s.section("Recording it done moves the next date on")
    # From the day it was DONE, not the day it was due. Six weeks late must
    # not make the following one six weeks early.
    m.record_maintenance_visit(conn, late["id"], done_on=today.isoformat(),
                              done_by="Le ramoneur", cost=180.0)
    after = conn.execute("SELECT * FROM maintenance_schedules WHERE id = ?",
                         (late["id"],)).fetchone()
    s.check("the last-done date is recorded", after["last_done_on"] == today.isoformat(),
            detail=str(after["last_done_on"]))
    s.check("the next one is a year from when it was done, not from when it was due",
            after["next_due_on"] == m._add_months(today, 12).isoformat(),
            detail=f"{after['next_due_on']} — due date was {d(-40)}")
    s.check("it is no longer overdue",
            m.maintenance_status(after, today)["state"] == "scheduled",
            detail=m.maintenance_status(after, today)["state"])
    s.check("and the task is ticked off",
            all(t["status"] == "done" for t in _tasks(conn, "Sweep")),
            detail=str([t["status"] for t in _tasks(conn, "Sweep")]))
    visit = conn.execute(
        "SELECT * FROM maintenance_visits WHERE schedule_id = ?", (late["id"],)).fetchone()
    s.check("the visit is kept, with who and what it cost",
            visit and visit["done_by"] == "Le ramoneur" and visit["cost"] == 180.0,
            detail=str(dict(visit)) if visit else "no visit")

    s.section("And the next one comes round in its own time")
    m.generate_maintenance_tasks(conn, today=today)
    s.check("nothing new is raised the day after it was done",
            len([t for t in _tasks(conn, "Sweep") if t["status"] != "done"]) == 0)
    m.generate_maintenance_tasks(conn, today=m._add_months(today, 12) - timedelta(days=5))
    s.check("but it is raised again when it next falls due",
            len([t for t in _tasks(conn, "Sweep") if t["status"] != "done"]) == 1,
            detail="a schedule that never comes round again is not a schedule")

    s.section("Retiring keeps the history")
    # The proof it was ever done outlives the schedule. An insurer asking about
    # last year does not care that it has since been retired.
    oc.post(f"/management/maintenance/{late['id']}/delete", follow_redirects=True)
    row = conn.execute("SELECT active FROM maintenance_schedules WHERE id = ?",
                       (late["id"],)).fetchone()
    s.check("it comes off the schedule", row and row["active"] == 0)
    s.check("but the visit is still there", conn.execute(
        "SELECT COUNT(*) AS c FROM maintenance_visits WHERE schedule_id = ?",
        (late["id"],)).fetchone()["c"] >= 1)
    # The open task goes with it. Retiring says this work is no longer owed,
    # so leaving somebody chasing it asks for the one thing just stopped.
    s.check("and the task it was carrying is closed",
            len([t for t in _tasks(conn, "Sweep") if t["status"] != "done"]) == 0,
            detail="somebody is still being asked to do retired work")
    m.generate_maintenance_tasks(conn, today=m._add_months(today, 13))
    s.check("and nothing new is ever raised for it again",
            len([t for t in _tasks(conn, "Sweep") if t["status"] != "done"]) == 0)

    s.section("On the page")
    r = oc.get("/management/maintenance")
    s.check("it opens", r.status_code == 200, detail=str(r.status_code))
    body = r.get_data(as_text=True)
    s.check("with the overdue one called out at the top",
            "Overdue, and expected by somebody outside the house" in body
            or (TAG + "Windows") in body)
    s.check("and the suggestions are offered without being asserted",
            "prompt to check, not a rule" in body,
            detail="the suggested intervals read as requirements")

    s.section("Adding one through the form")
    r = oc.post("/management/maintenance/new",
                data={"name": TAG + "Extinguishers", "category": "safety",
                      "every_months": "12", "last_done_on": d(-350),
                      "required_by": "law", "lead_days": "21", "active": "on"},
                follow_redirects=True)
    added = conn.execute("SELECT * FROM maintenance_schedules WHERE name = ?",
                         (TAG + "Extinguishers",)).fetchone()
    s.check("it is saved", bool(added), r)
    s.check("and the next date was worked out from the last one",
            added and added["next_due_on"] == m._add_months(m.parse_date(d(-350)), 12).isoformat(),
            detail=str(added["next_due_on"]) if added else "?")
    s.check("and it raised its task straight away, being nearly due",
            len(_tasks(conn, "Extinguishers")) == 1,
            detail=f"{len(_tasks(conn, 'Extinguishers'))} tasks")

    s.section("Guards")
    s.check("a schedule with no name is refused",
            b"Give it a name" in oc.post("/management/maintenance/new",
                                         data={"name": "  "}, follow_redirects=True).data)
    s.check("recording without a date is refused",
            b"When was it done" in oc.post(
                f"/management/maintenance/{far['id']}/done",
                data={"done_on": ""}, follow_redirects=True).data)
    s.check("an employee cannot see the page",
            ec.get("/management/maintenance").status_code in (302, 403))
    s.check("nor record work as done",
            ec.post(f"/management/maintenance/{far['id']}/done",
                    data={"done_on": today.isoformat()}).status_code in (302, 403))
    s.check("editing one that does not exist is a 404",
            oc.post("/management/maintenance/999999/edit",
                    data={"name": "x"}).status_code == 404)
    s.check("a certificate nobody uploaded is a 404, not a crash",
            oc.get("/management/maintenance/certificate/999999").status_code == 404)

    _cleanup(conn)
    conn.close()
    return s
