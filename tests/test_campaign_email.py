"""Campaign email — the only module that can do something irreversible to
real guests, so these checks are about the guards rather than the happy path.
"""
import secrets
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZCAMP"
MAIL = f"{TAG.lower()}.guest@example.invalid"


def run():
    s = Suite("Campaign email")
    oc, ec, owner, emp = clients()

    s.section("Templates")
    r = oc.post("/admin/emails/new", data={
        "name": f"{TAG} Autumn offer", "subject": "A quiet October at Gudanes",
        "area": "general", "body": "Dear {{name}}, the valley is beautiful in October.",
    }, follow_redirects=True)
    conn = db()
    tpl = conn.execute("SELECT * FROM campaign_templates WHERE name LIKE ?",
                       (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can create a template", tpl is not None, r)

    r = oc.post("/admin/emails/new", data={"name": f"{TAG} nameless"}, follow_redirects=True)
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM campaign_templates WHERE name LIKE ?",
                     (TAG + " nameless%",)).fetchone()["c"]
    conn.close()
    s.check("template without a subject is refused", n == 0, r, detail=f"{n} created")

    s.section("Audience and the send guard")
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Test Guest", MAIL, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    audience = m.campaign_audience(conn, ["profiles"])
    conn.close()
    s.check("guest appears in the audience", MAIL in audience,
            detail=f"{len(audience)} recipients")

    if tpl:
        # The typed-count confirmation is what stands between a mis-click and
        # mailing every guest the château has ever had.
        conn = db()
        before = conn.execute("SELECT COUNT(*) c FROM campaign_sends").fetchone()["c"]
        conn.close()
        bogus = str(len(audience) + 7)          # can never be the real count
        r = oc.post(f"/admin/emails/{tpl['id']}/send",
                    data={"segments": ["profiles"], "confirm_count": bogus},
                    follow_redirects=True)
        conn = db()
        after = conn.execute("SELECT COUNT(*) c FROM campaign_sends").fetchone()["c"]
        conn.close()
        s.check("wrong confirm-count sends nothing", after == before, r,
                detail=f"typed {bogus} for an audience of {len(audience)}; {before}->{after}")

        r = oc.post(f"/admin/emails/{tpl['id']}/send",
                    data={"segments": [], "confirm_count": str(len(audience))},
                    follow_redirects=True)
        s.check("send with no audience selected is refused",
                any("at least one audience" in f for f in flashes(r)), r)

    s.section("Unsubscribe")
    conn = db()
    token = f"{TAG}-{secrets.token_urlsafe(8)}"
    try:
        conn.execute(
            """INSERT INTO campaign_sends (template_id, recipient_email, recipient_name,
               unsubscribe_token, status, created_at) VALUES (?,?,?,?,?,?)""",
            (tpl["id"] if tpl else None, MAIL, f"{TAG} Test Guest", token, "sent",
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        seeded = True
    except Exception as e:
        seeded = False
        print(f"       (could not seed a send row: {e})")
    conn.close()

    if seeded:
        anon = m.app.test_client()
        # Mail clients and link scanners fetch every URL in a message. If GET
        # unsubscribed, guests would be opted out without ever clicking.
        r = anon.get(f"/unsubscribe/{token}")
        conn = db()
        out = conn.execute("SELECT COUNT(*) c FROM email_optouts WHERE email=?",
                           (MAIL,)).fetchone()["c"]
        conn.close()
        s.check("GET on the unsubscribe link does NOT opt out",
                r.status_code == 200 and out == 0,
                detail=f"HTTP {r.status_code}, optouts={out}")

        r = anon.post(f"/unsubscribe/{token}", follow_redirects=True)
        conn = db()
        out = conn.execute("SELECT COUNT(*) c FROM email_optouts WHERE email=?",
                           (MAIL,)).fetchone()["c"]
        conn.close()
        s.check("POST does opt out", out == 1, r, detail=f"optouts={out}")

        conn = db()
        again = m.campaign_audience(conn, ["profiles"])
        every = m.campaign_audience(conn, ["profiles"], include_optouts=True)
        conn.close()
        s.check("opted-out guest is dropped from the next audience", MAIL not in again,
                detail="still in the audience" if MAIL in again else "")
        s.check("owner can still see them via include_optouts", MAIL in every)

        r = anon.get(f"/unsubscribe/{TAG}-does-not-exist")
        s.check("unknown token is a clean 404, not a crash", r.status_code == 404,
                detail=f"HTTP {r.status_code}")

    s.section("Readiness and permissions")
    r = oc.get("/admin/readiness")
    s.check("readiness page renders", r.status_code == 200, detail=f"HTTP {r.status_code}")
    for path in ["/admin/emails", "/admin/readiness"]:
        rr = ec.get(path)
        s.check(f"employee blocked from {path}", rr.status_code in (302, 403),
                detail=f"HTTP {rr.status_code}")

    conn = db()
    for sql, args in [
        ("DELETE FROM campaign_sends WHERE recipient_email=?", (MAIL,)),
        ("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",)),
        ("DELETE FROM email_optouts WHERE email=?", (MAIL,)),
        ("DELETE FROM guests WHERE email=?", (MAIL,)),
    ]:
        try:
            conn.execute(sql, args)
        except Exception:
            pass
    conn.commit()
    conn.close()
    return s
