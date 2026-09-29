"""Incidents, role compliance, the access register and the payroll pack."""
import contextlib
import io
import re
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHR"


def _card(page, summary):
    """One incident's card on the register, found by its summary."""
    return next((c for c in page.split('class="expense-card"')
                 if f"<strong>{summary}</strong>" in c), "")


def _when_on_card(page, summary):
    """The time the card prints beside the summary, read from its own cell."""
    shown = re.search(r'<span class="upload-hint">([^<]*)</span>', _card(page, summary))
    return shown.group(1).strip() if shown else None


def _banner_day(page, summary):
    """The day the insurer banner files an incident under."""
    row = next((" ".join(b.split())
                for b in re.findall(r'class="pass-short-row">(.*?)</span>', page, re.S)
                if summary in b), "")
    return row.split(" — ")[0] if row else None


def _this_year(page):
    """The "This year" figure in the register's band, read from its cell."""
    cell = next((c for c in page.split('class="overview-cell')
                 if 'overview-label">This year<' in c), "")
    shown = re.search(r'overview-value">\s*([0-9]+)', cell)
    return int(shown.group(1)) if shown else None


def run():
    s = Suite("HR compliance")
    today = m.house_today()
    oc, ec, owner, emp = clients()

    s.section("Incidents: log, close, and keep staff out")
    r = oc.post("/admin/incidents/new", data={
        # What the form's datetime-local sends: a date AND a time.
        "occurred_at": f"{today.isoformat()}T10:00", "kind": "workplace", "severity": "minor",
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

    # A valid time, so the only thing that can refuse it is the permission.
    denied = ec.post("/admin/incidents/new",
                     data={"occurred_at": f"{today.isoformat()}T10:00",
                           "summary": f"{TAG} sneaky"})
    conn = db()
    leak = conn.execute("SELECT COUNT(*) c FROM incidents WHERE summary LIKE ?",
                        (TAG + " sneaky%",)).fetchone()["c"]
    conn.close()
    s.check("employee cannot log an incident",
            denied.status_code in (302, 403) and leak == 0,
            detail=f"status={denied.status_code} rows={leak}")

    s.section("Incidents: when it happened, on the house's clock")
    # 23:30 on 4 September in the Ariège is 21:30 UTC. It is the hour where
    # the day changes if the time is misread: taken for UTC, it is 01:30 on
    # the 5th. The register used to store it as typed and read it as UTC.
    late = f"{TAG} fell on the terrace steps"
    r = oc.post("/admin/incidents/new", data={
        "occurred_at": "2026-09-04T23:30", "kind": "workplace", "severity": "minor",
        "summary": late}, follow_redirects=True)
    winter = f"{TAG} slipped on the frozen drive"
    oc.post("/admin/incidents/new", data={
        "occurred_at": "2026-01-15T23:30", "kind": "guest", "severity": "minor",
        "summary": winter})
    conn = db()
    stored = {row["summary"]: row["occurred_at"] for row in conn.execute(
        "SELECT summary, occurred_at FROM incidents WHERE summary IN (?, ?)",
        (late, winter))}
    conn.close()
    s.check("23:30 on 4 September is stored as 21:30 UTC, zone and all",
            stored.get(late) == "2026-09-04T21:30:00+00:00", r,
            detail=f"stored {stored.get(late)!r}")
    s.check("23:30 on 15 January is stored as 22:30 UTC: the zone's offset, not a fixed two hours",
            stored.get(winter) == "2026-01-15T22:30:00+00:00",
            detail=f"stored {stored.get(winter)!r}")

    page = oc.get("/admin/incidents?status=open").get_data(as_text=True)
    s.check("the register shows it at 23:30 on 4 September",
            _when_on_card(page, late) == "September 4, 2026 23:30",
            detail=f"shows {_when_on_card(page, late)!r}")
    s.check("and the winter one at 23:30 on 15 January",
            _when_on_card(page, winter) == "January 15, 2026 23:30",
            detail=f"shows {_when_on_card(page, winter)!r}")
    s.check("the insurer banner files it under 4 September, not the 5th",
            _banner_day(page, late) == "4 September 2026",
            detail=f"filed under {_banner_day(page, late)!r}")

    bad = oc.post("/admin/incidents/new", data={
        "occurred_at": "2026-09-04", "summary": f"{TAG} no time given"},
        follow_redirects=True)
    conn = db()
    guessed = conn.execute("SELECT COUNT(*) c FROM incidents WHERE summary = ?",
                           (f"{TAG} no time given",)).fetchone()["c"]
    conn.close()
    s.check("a date with no time is refused, not stored as a guess",
            guessed == 0 and any("date and time" in f for f in flashes(bad)), bad,
            detail=f"rows={guessed}")

    s.section("Incidents: this year starts at midnight here")
    # The band counts from 1 January. Half past midnight on New Year's Day is
    # 23:30 UTC on the 31st: counted from midnight UTC it falls in last year.
    # Half an hour before midnight on the 31st is last year's, either way.
    before = _this_year(oc.get("/admin/incidents?status=open").get_data(as_text=True))
    oc.post("/admin/incidents/new", data={
        "occurred_at": f"{today.year}-01-01T00:30", "kind": "guest",
        "severity": "near_miss", "summary": f"{TAG} new year's night"})
    oc.post("/admin/incidents/new", data={
        "occurred_at": f"{today.year - 1}-12-31T23:30", "kind": "guest",
        "severity": "near_miss", "summary": f"{TAG} new year's eve"})
    after = _this_year(oc.get("/admin/incidents?status=open").get_data(as_text=True))
    s.check("00:30 on 1 January counts in this year, 23:30 on the 31st does not",
            before is not None and after == before + 1,
            detail=f"This year went {before} -> {after}")

    s.section("Incidents already in the register, stored as typed")
    # What production holds: the form's value exactly as it arrived. The
    # startup step reads each on the house's clock and writes back UTC.
    now = datetime.now(timezone.utc).isoformat()
    legacy = {
        "summer evening": ("2026-09-04T23:30", "2026-09-04T21:30:00+00:00"),
        "winter evening": ("2026-01-15T23:30", "2026-01-15T22:30:00+00:00"),
        "a date with no time": ("2026-09-04", "2026-09-03T22:00:00+00:00"),
        # 02:30 happens twice that night; the first is the earlier moment.
        "the night the clocks go back": ("2026-10-25T02:30", "2026-10-25T00:30:00+00:00"),
        "already in UTC": ("2026-09-04T21:30:00+00:00", "2026-09-04T21:30:00+00:00"),
        "unreadable": ("2026-09-04T25:99", "2026-09-04T25:99"),
    }
    conn = db()
    ids = {}
    for label, (typed, _want) in legacy.items():
        ids[label] = conn.execute(
            """INSERT INTO incidents (kind, occurred_at, summary, severity, status, created_at)
               VALUES ('guest', ?, ?, 'minor', 'open', ?)""",
            (typed, f"{TAG} legacy {label}", now)).lastrowid
    conn.commit()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        m.incident_times_to_utc(conn)
    held = {label: conn.execute("SELECT occurred_at FROM incidents WHERE id = ?",
                                (ids[label],)).fetchone()["occurred_at"]
            for label in legacy}
    for label, (typed, want) in legacy.items():
        s.check(f"{label}: {typed} becomes {want}", held[label] == want,
                detail=f"holds {held[label]!r}")
    s.check("the unreadable one is named at startup, not guessed at",
            f"#{ids['unreadable']}" in said.getvalue(),
            detail=f"printed {said.getvalue().strip()!r}")

    second = m.incident_times_to_utc(conn)
    again = {label: conn.execute("SELECT occurred_at FROM incidents WHERE id = ?",
                                 (ids[label],)).fetchone()["occurred_at"]
             for label in legacy}
    s.check("run again, it finds nothing and changes nothing",
            second == 0 and again == held, detail=f"changed {second}")

    def audit_lines(label):
        return [r["details"] for r in conn.execute(
            "SELECT details FROM audit_log WHERE action = 'incident_time_to_utc' "
            "AND target = ?", (f"incident #{ids[label]}",))]
    lines = audit_lines("summer evening")
    s.check("each rewrite leaves one audit line saying what the row held",
            len(lines) == 1 and "2026-09-04T23:30" in lines[0]
            and "2026-09-04T21:30:00+00:00" in lines[0], detail=str(lines))
    s.check("a row that already had its zone leaves none",
            audit_lines("already in UTC") == [],
            detail=str(audit_lines("already in UTC")))
    conn.close()

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
