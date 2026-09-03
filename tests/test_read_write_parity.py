"""Reading was owner-only. Writing was not.

The areas model names endpoints as owner-only for a reason written down: money
that leaves the house, and anything that grants access. Auditing the inverse —
what is money-shaped or access-shaped and NOT on that list — turned up three
holes, two of them the same shape.

THE BANK DETAILS. reveal_bank_details was owner-only; new_, edit_ and
delete_bank_details were not, and the manager preset grants `financial`. Two
live presets could not READ the account the house is paid into and could
REPLACE it. The asymmetry looks deliberate until it is said out loud: the
dangerous act is not seeing the number, it is changing where money goes.

THE VAULT was the same and was safe only by accident — its writes sat in
`management`, which no preset happens to grant today. Granting `management` to
anybody tomorrow would have opened exactly the bank-details hole with the
reasoning written down nowhere.

REGENERATING AN INVITE is a different animal, and making it owner-only would
have been the wrong fix. It overwrites the target's password and hands the
caller a link that sets a new one, so it is a way to become that person. It is
scoped to role='employee', so an owner cannot be taken over — but any employee
can, INCLUDING ONE WITH A WIDER PRESET. Somebody with `team` alone could reset
the manager's sign-in and claim the account. Locking it to the owner would
also stop whoever runs the rota re-inviting a housekeeper who has lost their
link, which is a real thing they do and nothing to do with this. So it is
refused only UPWARDS.

The parity rule is the durable half: where reading a thing is the owner's,
writing it is the owner's. The groups below are named, and each name is
checked to still be a route — an exception list that outlives the code it
excuses is how the next hole gets in.
"""
from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZPARITY"


# Endpoints that touch one thing, and must all sit at the same level. Reading
# a bank account and replacing it are not different risks with different
# answers; they are the same risk, and the replacing one is worse.
GROUPS = {
    "the house's bank details": [
        "reveal_bank_details", "new_bank_details", "edit_bank_details",
        "delete_bank_details",
    ],
    "the vault": [
        "reveal_vault_entry", "new_vault_entry", "edit_vault_entry",
        "delete_vault_entry",
    ],
    "who may reach what": [
        "admin_access_levels", "save_access_preset", "assign_access_preset",
    ],
}


def _cleanup(conn):
    # The audit rows first: resetting a sign-in writes one, and the users
    # table is referenced by it. A cleanup that cannot run leaves the next
    # run's fixtures colliding with this one's.
    conn.execute(
        "DELETE FROM audit_log WHERE actor_user_id IN "
        "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("read and write sit at the same level")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    s.section("Every group is all owner's or none of it")
    for what, eps in sorted(GROUPS.items()):
        for ep in eps:
            s.check(f"{ep} is still a route", ep in m.app.view_functions,
                    detail="the list names an endpoint that is gone, which is "
                           "how an exception outlives the code it excuses")
        levels = {ep: ep in m.OWNER_ONLY_ENDPOINTS
                  for ep in eps if ep in m.app.view_functions}
        s.check(f"{what}: reading and writing agree",
                len(set(levels.values())) == 1,
                detail="; ".join(f"{e}={'owner' if v else 'area'}"
                                 for e, v in sorted(levels.items()))
                       + " — the dangerous act is not seeing it, it is "
                         "changing it")

    s.section("And no live preset can write what it cannot read")
    # Against the presets that actually exist, not against the model. The
    # model was right about reading all along; what was wrong was what it
    # said about writing.
    presets = conn.execute(
        "SELECT slug, areas, is_full_access FROM access_presets "
        "WHERE is_full_access = 0 ORDER BY slug").fetchall()
    s.check("there are presets to check against", bool(presets),
            detail=f"{len(presets)} non-owner presets")
    for p in presets:
        areas = {a.strip() for a in (p["areas"] or "").split(",") if a.strip()}

        def reachable(ep):
            if ep in m.OWNER_ONLY_ENDPOINTS:
                return False
            return m.ENDPOINT_AREA.get(ep) in areas

        for what, eps in sorted(GROUPS.items()):
            live = [e for e in eps if e in m.app.view_functions]
            can = {e for e in live if reachable(e)}
            s.check(f"{p['slug']} gets all of {what}, or none of it",
                    len(can) in (0, len(live)),
                    detail=f"can reach {sorted(can)} of {sorted(live)}")

    # ------------------------------------------------- resetting a sign-in
    s.section("Resetting a sign-in is refused upwards, not to everybody")
    # Making this owner-only would stop whoever runs the rota re-inviting a
    # housekeeper who has lost their link, which is a real thing they do.
    def add_user(name, preset):
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role,
                       status, access_preset, account_claimed, created_at)
               VALUES (?, 'x', 'employee', ?, 'General', 'active', ?, 1, ?)""",
            (f"{TAG}.{name}@example.invalid".lower(), TAG + " " + name,
             preset, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    wide = add_user("Manager", "manager")
    narrow = add_user("Housekeeper", "karina")
    conn.commit()

    wide_row = conn.execute("SELECT * FROM users WHERE id = ?", (wide,)).fetchone()
    narrow_row = conn.execute("SELECT * FROM users WHERE id = ?", (narrow,)).fetchone()

    s.check("a wide preset covers a narrow one",
            m.access_covers(wide_row, narrow_row))
    s.check("and a narrow one does not cover a wide one",
            not m.access_covers(narrow_row, wide_row),
            detail="'team' alone could reset the manager's sign-in and claim "
                   "the account")
    s.check("a full owner covers everybody",
            m.access_covers({"role": "owner", "access_preset": None}, wide_row))
    s.check("and nobody covers a full owner but another one",
            not m.access_covers(narrow_row,
                                {"role": "owner", "access_preset": None}))

    s.section("Through the page")
    before = conn.execute("SELECT password_hash FROM users WHERE id = ?",
                          (wide,)).fetchone()["password_hash"]
    # The owner may, and it really does reset the sign-in.
    oc.post(f"/directory/{narrow}/regenerate-invite", follow_redirects=True)
    s.check("the owner can reset a narrower account",
            conn.execute("SELECT account_claimed FROM users WHERE id = ?",
                         (narrow,)).fetchone()["account_claimed"] == 0,
            detail="if this fails the refusal below proves nothing, because "
                   "nothing would have worked anyway")

    s.check("and the wider account is untouched by that",
            conn.execute("SELECT password_hash FROM users WHERE id = ?",
                         (wide,)).fetchone()["password_hash"] == before)

    s.section("And the narrow account is refused, through the page")
    # The behaviour that actually changed. access_covers() agreeing is not the
    # same as the route refusing, and it is the route somebody clicks.
    narrow_client = m.app.test_client()
    with narrow_client.session_transaction() as sess:
        sess["user_id"] = narrow
    hash_before = conn.execute("SELECT password_hash FROM users WHERE id = ?",
                               (wide,)).fetchone()["password_hash"]
    r = narrow_client.post(f"/directory/{wide}/regenerate-invite",
                           follow_redirects=True)
    s.check("the wider account's sign-in is untouched",
            conn.execute("SELECT password_hash FROM users WHERE id = ?",
                         (wide,)).fetchone()["password_hash"] == hash_before,
            detail="'team' alone resetting the manager's password and being "
                   "handed the link to set a new one")
    s.check("and it says why rather than simply failing",
            "can reach more of the app than you can" in r.get_data(as_text=True),
            detail="a refusal with no reason sends somebody to ask the owner "
                   "what they did wrong")
    s.check("no onboarding link is handed over",
            "/onboard/" not in r.get_data(as_text=True),
            detail="the link IS the takeover; refusing after printing it "
                   "would refuse nothing")

    s.section("But the same person may still reset somebody narrower")
    # The half that makes this the right fix rather than owner-only: whoever
    # runs the rota can still re-invite a housekeeper who lost their link.
    peer = add_user("Second Housekeeper", "karina")
    conn.commit()
    r = narrow_client.post(f"/directory/{peer}/regenerate-invite",
                           follow_redirects=True)
    s.check("an equal preset is allowed",
            conn.execute("SELECT account_claimed FROM users WHERE id = ?",
                         (peer,)).fetchone()["account_claimed"] == 0,
            detail="refused upwards, not to everybody")

    s.section("An owner cannot be reset at all")
    owner_id = conn.execute(
        "SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()["id"]
    r = oc.post(f"/directory/{owner_id}/regenerate-invite",
                follow_redirects=False)
    s.check("even by an owner", r.status_code == 404,
            detail=f"HTTP {r.status_code} — the route is scoped to employees, "
                   "which is what stops the whole thing being a way to take "
                   "the house")

    s.section("The audit list is what the audit actually found")
    # Named so that removing one is a decision somebody makes on purpose.
    for ep in ("new_bank_details", "edit_bank_details", "delete_bank_details",
               "new_vault_entry", "edit_vault_entry", "delete_vault_entry"):
        s.check(f"{ep} is the owner's", ep in m.OWNER_ONLY_ENDPOINTS,
                detail="it was not, and two live presets could reach it")
    s.check("regenerate_invite is NOT owner-only",
            "regenerate_invite" not in m.OWNER_ONLY_ENDPOINTS,
            detail="locking it to the owner would stop whoever runs the rota "
                   "re-inviting a housekeeper who lost their link, which is "
                   "the wrong fix for the right problem")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
