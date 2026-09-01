"""Incidents, role compliance, the access register and the payroll pack."""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHR"


def run():
    s = Suite("HR compliance")
    today = m.house_today()
    oc, ec, owner, emp = clients()

    s.section("Incidents: log, close, and keep staff out")
    r = oc.post("/admin/incidents/new", data={
        "occurred_at": today.isoformat(), "kind": "workplace", "severity": "minor",
        "summary": f"{TAG} slipped on wet floor",
        "affected_user_id": str(emp["id"]) if emp else "",
        "location": "Kitchen", "action_taken": "First aid given",
    }, follow_redirects=True)
    conn = db()
    inc = conn.execute("SELECT * FROM incidents WHERE summary LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can log an incident", inc is not None, r)

    if inc:
        oc.post(f"/admin/incidents/{inc['id']}/update",
                data={"status": "closed", "action_taken": "Resolved"}, follow_redirects=True)
        conn = db()
        st = conn.execute("SELECT status FROM incidents WHERE id=?", (inc["id"],)).fetchone()["status"]
        conn.close()
        s.check("incident can be closed", st == "closed", detail=f"got {st}")

    denied = ec.post("/admin/incidents/new",
                     data={"occurred_at": today.isoformat(), "summary": f"{TAG} sneaky"})
    conn = db()
    leak = conn.execute("SELECT COUNT(*) c FROM incidents WHERE summary LIKE ?",
                        (TAG + " sneaky%",)).fetchone()["c"]
    conn.close()
    s.check("employee cannot log an incident",
            denied.status_code in (302, 403) and leak == 0,
            detail=f"status={denied.status_code} rows={leak}")

    s.section("Role compliance: add and remove a requirement")
    r = oc.post("/admin/compliance/new", data={
        "job_role": f"{TAG} Chef", "requirement": "Food hygiene certificate",
        "requirement_type": "certification",
    }, follow_redirects=True)
    conn = db()
    cr = conn.execute("SELECT * FROM role_requirements WHERE job_role LIKE ?",
                      (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can add a role requirement", cr is not None, r)
    if cr:
        oc.post(f"/admin/compliance/{cr['id']}/delete", follow_redirects=True)
        conn = db()
        n = conn.execute("SELECT COUNT(*) c FROM role_requirements WHERE id=?",
                         (cr["id"],)).fetchone()["c"]
        conn.close()
        s.check("requirement can be deleted", n == 0, detail=f"{n} remain")

    s.section("Access register: issue a key and get it back")
    r = oc.post("/admin/access/items/new",
                data={"label": f"{TAG} front door key", "kind": "key"}, follow_redirects=True)
    conn = db()
    it = conn.execute("SELECT * FROM access_items WHERE label LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can add an access item", it is not None, r)

    if it and emp:
        r2 = oc.post("/admin/access/issue",
                     data={"access_item_id": str(it["id"]), "user_id": str(emp["id"])},
                     follow_redirects=True)
        conn = db()
        h = conn.execute(
            "SELECT * FROM access_holdings WHERE access_item_id=? AND returned_at IS NULL",
            (it["id"],)).fetchone()
        conn.close()
        s.check("item can be issued to staff", h is not None, r2)
        if h:
            oc.post(f"/admin/access/{h['id']}/return", follow_redirects=True)
            conn = db()
            ret = conn.execute("SELECT returned_at FROM access_holdings WHERE id=?",
                               (h["id"],)).fetchone()["returned_at"]
            conn.close()
            s.check("item can be returned", ret is not None)

    s.section("Payroll")
    period = today.strftime("%Y-%m")
    r = oc.get(f"/admin/payroll?period={period}")
    s.check("payroll page renders", r.status_code == 200, detail=f"HTTP {r.status_code}")

    # Export is DELIBERATELY refused when a row has blockers — an impossible
    # shift or a missing pay rate. A payroll file that silently prices someone
    # at zero is worse than no file, so a refusal here is a pass; a 500 is not.
    r = oc.get(f"/admin/payroll/export.csv?period={period}", follow_redirects=True)
    refused = "Fix these before exporting" in r.get_data(as_text=True)
    s.check("payroll CSV exports, or refuses with a reason",
            r.status_code == 200 and (refused or b"," in r.data),
            detail=f"HTTP {r.status_code}")
    if refused:
        print("       (correctly refused — unresolved payroll blockers)")

    s.section("Permissions")
    for path in ["/admin/incidents", "/admin/compliance", "/admin/access", "/admin/payroll"]:
        rr = ec.get(path)
        s.check(f"employee blocked from {path}", rr.status_code in (302, 403),
                detail=f"HTTP {rr.status_code}")

    conn = db()
    for table, col in [("incidents", "summary"), ("role_requirements", "job_role"),
                       ("access_items", "label")]:
        try:
            conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (TAG + "%",))
        except Exception:
            pass
    try:
        conn.execute("DELETE FROM access_holdings "
                     "WHERE access_item_id NOT IN (SELECT id FROM access_items)")
    except Exception:
        pass
    conn.commit()
    conn.close()
    return s
