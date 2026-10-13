"""Signing in to the Outlook add-in with Microsoft 365, instead of a shared key
pasted into every Outlook.

The add-in sends a token Microsoft issued; the app checks it and answers with
a key of the add-in's own for that person. Everything here turns on that check
being right, so it is tested the way it would be attacked: with a real RSA
key made for the test, standing in for Microsoft's published one, and a token
that is wrong in each way a token can be wrong -- signed by somebody else,
for another organisation, for another app, asked for by another app, out of
date, without the permission. Each must be refused, and a genuine one must
not be.

Then the rest of the door: a person the dashboard does not know is turned
away and named for the owner to link; a key that is signed out, run out, or
belongs to somebody who has left stops working; the shared key is staff and
can be switched off; and the limits are per person, because the whole house
is usually one internet address.
"""
import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSI"
TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SIGNING = rsa.generate_private_key(public_exponent=65537, key_size=2048)
IMPOSTOR = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(key, kid):
    n = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "use": "sig",
            "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
            "e": _b64(n.e.to_bytes((n.e.bit_length() + 7) // 8, "big"))}


def token(claims, key=SIGNING, kid="k1", alg="RS256"):
    head = _b64({"alg": alg, "typ": "JWT", "kid": kid})
    body = _b64(claims)
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


def claims(**over):
    now = time.time()
    base = {"aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "tid": TENANT, "azp": CLIENT, "scp": "access_as_user", "exp": now + 3600,
            "nbf": now - 60, "preferred_username": "nobody@example.invalid", "name": "Nobody"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


class _SignInOn:
    """Sign-in switched on, against the test's own key, for the length of a
    `with`. Records each time Microsoft would have been asked for its keys."""

    def __enter__(self):
        self.asked = []
        self.saved = {n: getattr(m, n) for n in ("ADDIN_CLIENT_ID", "ADDIN_TENANT_ID",
                                                 "fetch_microsoft_keys")}
        m.ADDIN_CLIENT_ID, m.ADDIN_TENANT_ID = CLIENT, TENANT
        m.fetch_microsoft_keys = lambda tenant: (self.asked.append(tenant)
                                                 or {"k1": _jwk(SIGNING, "k1")})
        m._MS_KEYS.update(keys={}, fetched_at=0.0, tried_at=0.0)
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(m, n, v)
        m._MS_KEYS.update(keys={}, fetched_at=0.0, tried_at=0.0)
        return False


def _cleanup(conn):
    conn.execute("DELETE FROM addin_sessions WHERE label LIKE ? OR user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("UPDATE users SET m365_upn = NULL WHERE LOWER(COALESCE(m365_upn, '')) LIKE ?",
                 (TAG.lower() + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)",
                 (m.ADDIN_UNMATCHED_SETTING, m.ADDIN_SHARED_KEY_SETTING))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'guest_lookup%' "
                 "OR action LIKE 'addin_%'")
    conn.commit()


def run():
    s = Suite("signing in to the Outlook add-in")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    anon = m.app.test_client()
    owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()

    s.section("Switched off, there is nothing there")
    s.check("no client id, no sign-in", anon.post(
        "/api/addin/session", headers={"Authorization": "Bearer x"}).status_code == 404)

    with _SignInOn() as on:
        s.section("A genuine token is accepted")
        good = claims(preferred_username=owner_row["email"])
        try:
            got = m.verify_microsoft_token(token(good))
        except m.AddinAuthError as e:
            got = str(e)
        s.check("Microsoft's signature, this house, this app, in date, with the permission",
                isinstance(got, dict) and got["tid"] == TENANT, detail=str(got)[:160])
        v1 = claims(iss=f"https://sts.windows.net/{TENANT}/", aud=f"api://{CLIENT}", azp=None,
                    appid=CLIENT, preferred_username=None, upn=owner_row["email"])
        try:
            got = m.verify_microsoft_token(token(v1))
        except m.AddinAuthError as e:
            got = str(e)
        s.check("and the older shape of token Microsoft also issues",
                isinstance(got, dict), detail=str(got)[:160])
        s.check("Microsoft's keys were fetched once and kept", len(on.asked) == 1,
                detail=f"{len(on.asked)} fetches")

        s.section("A token wrong in any way is refused")
        now = time.time()
        for label, tok, why in (
                ("signed by somebody else", token(good, key=IMPOSTOR), "signature"),
                ("for another organisation", token(claims(tid="99999999-0000-0000-0000-000000000000")),
                 "another organisation"),
                ("from another issuer", token(claims(iss="https://evil.example/v2.0")), "issued"),
                ("for another app", token(claims(aud="bbbbbbbb-0000-0000-0000-000000000000")),
                 "another app"),
                ("asked for by another app", token(claims(azp="cccccccc-0000-0000-0000-000000000000")),
                 "another app"),
                ("out of date", token(claims(exp=now - 3600)), "expired"),
                ("not valid yet", token(claims(nbf=now + 3600)), "not valid yet"),
                ("without the permission", token(claims(scp="User.Read")), "permission"),
                ("unsigned", token(good, alg="none"), "signed"),
                ("with a key Microsoft does not publish", token(good, kid="k-unknown"), "publish"),
                ("not a token at all", "this.is.nonsense", "not a token")):
            try:
                m.verify_microsoft_token(tok)
                said = None
            except m.AddinAuthError as e:
                said = str(e)
            s.check(f"refused: {label}", said is not None and why in said, detail=str(said))
        s.check("an unknown key id just after a fetch does not fetch again",
                len(on.asked) == 1, detail=f"{len(on.asked)} fetches — Microsoft publishes a "
                "new key weeks before signing with it, so a stranger is a forgery")
        m._MS_KEYS["tried_at"] = time.time() - 301
        for kid in ("k-other", "k-another", "k-yet-another"):
            try:
                m.verify_microsoft_token(token(good, kid=kid))
            except m.AddinAuthError:
                pass
        s.check("after five minutes it asks once more, and a stream of made-up ids "
                "does not have it asking every time", len(on.asked) == 2,
                detail=f"{len(on.asked)} fetches")

        s.section("The token is exchanged for the add-in's own key")
        r = anon.get("/api/addin/session")
        s.check("a GET is a 404", r.status_code == 404)
        r = anon.post("/api/addin/session", headers={"Authorization": f"Bearer {token(good)}"},
                      data={"platform": "PC"})
        got = r.get_json() or {}
        s.check("the owner signs in", r.status_code == 200 and got.get("session"),
                detail=f"{r.status_code} {got}")
        s.check("as the owner", got.get("level") == "owner")
        key = got.get("session") or ""
        row = conn.execute("SELECT * FROM addin_sessions WHERE user_id = ? ORDER BY id DESC",
                           (owner["id"],)).fetchone()
        s.check("the key itself is never stored, only its hash",
                row and row["token_hash"] != key and key not in str(dict(row)))
        s.check("and it lasts a month", row and row["expires_at"][:10] >=
                (m.datetime.now(m.timezone.utc) + m.timedelta(days=29)).isoformat()[:10])
        r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid"},
                      headers={"Authorization": f"Bearer {key}"})
        s.check("the key opens the panel", r.status_code == 200, detail=str(r.status_code))
        r = anon.post("/api/addin/session", headers={"Authorization": "Bearer junk"})
        s.check("a bad token is refused with a reason",
                r.status_code == 401 and "not accepted" in (r.get_json() or {}).get("error", ""))

        s.section("Somebody the dashboard does not know")
        stranger = claims(preferred_username=f"{TAG.lower()}.newcomer@example.invalid",
                          name="A Newcomer")
        r = anon.post("/api/addin/session", headers={"Authorization": f"Bearer {token(stranger)}"})
        s.check("is turned away", r.status_code == 403, detail=str(r.status_code))
        s.check("told why", "not linked" in (r.get_json() or {}).get("error", ""))
        page = oc.get("/admin/outlook-addin").get_data(as_text=True)
        s.check("and named on the add-in page for the owner to link",
                f"{TAG.lower()}.newcomer@example.invalid" in page)
        r = oc.post("/admin/outlook-addin/people",
                    data={f"m365_{emp['id']}": f"{TAG.lower()}.newcomer@example.invalid"},
                    follow_redirects=True)
        s.check("the owner links them", "Saved for" in r.get_data(as_text=True), response=r)
        r = anon.post("/api/addin/session", headers={"Authorization": f"Bearer {token(stranger)}"})
        got = r.get_json() or {}
        s.check("and they are in, as staff", r.status_code == 200 and got.get("level") == "staff",
                detail=f"{r.status_code} {got}")
        staff_key = got.get("session") or ""
        page = oc.get("/admin/outlook-addin").get_data(as_text=True)
        s.check("and no longer listed as a stranger",
                "Tried to sign in and were not recognised" not in page)
        r = oc.post("/admin/outlook-addin/people",
                    data={f"m365_{owner['id']}": f"{TAG.lower()}.newcomer@example.invalid"},
                    follow_redirects=True)
        s.check("one address cannot be linked to two people",
                "already linked to somebody else" in r.get_data(as_text=True), response=r)
        r = oc.post("/admin/outlook-addin/people", data={f"m365_{owner['id']}": "not an address"},
                    follow_redirects=True)
        s.check("and nonsense is refused", "is not an address" in r.get_data(as_text=True))
        r = oc.post("/admin/outlook-addin/people", data={}, follow_redirects=True)
        s.check("an empty save changes nothing", "Nothing had changed" in r.get_data(as_text=True))

        s.section("A linked address wins over the dashboard one")
        conn.execute("UPDATE users SET m365_upn = ? WHERE id = ?",
                     (f"{TAG.lower()}.owner@example.invalid", owner["id"]))
        conn.commit()
        r = anon.post("/api/addin/session", headers={"Authorization": f"Bearer {token(good)}"})
        s.check("the old address no longer signs the owner in", r.status_code == 403)
        r = anon.post("/api/addin/session", headers={"Authorization": "Bearer " + token(
            claims(preferred_username=f"{TAG.lower()}.owner@example.invalid"))})
        s.check("the linked one does", r.status_code == 200)
        conn.execute("UPDATE users SET m365_upn = NULL WHERE id = ?", (owner["id"],))
        conn.commit()

    s.section("A key stops working when it should")
    sessions = conn.execute(
        "SELECT * FROM addin_sessions WHERE user_id = ? AND revoked_at IS NULL ORDER BY id DESC",
        (emp["id"],)).fetchall()
    page = oc.get("/admin/outlook-addin").get_data(as_text=True)
    s.check("the add-in page lists who is signed in", emp["name"] in page and "Sign out" in page)
    r = oc.post(f"/admin/outlook-addin/sessions/{sessions[0]['id']}/revoke", follow_redirects=True)
    s.check("the owner signs one Outlook out", "Signed out" in r.get_data(as_text=True), response=r)
    r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid"},
                  headers={"Authorization": f"Bearer {staff_key}"})
    s.check("and its key opens nothing", r.status_code == 404)
    r = oc.post(f"/admin/outlook-addin/sessions/{sessions[0]['id']}/revoke", follow_redirects=True)
    s.check("signing it out twice says so", "already signed out" in r.get_data(as_text=True))
    s.check("a key that does not exist is a 404",
            oc.post("/admin/outlook-addin/sessions/99999999/revoke").status_code == 404)
    old_key, _ = m.addin_issue_session(conn, owner["id"], "test", label=f"{TAG} expired")
    conn.execute("UPDATE addin_sessions SET expires_at = '2020-01-01T00:00:00+00:00' "
                 "WHERE label = ?", (f"{TAG} expired",))
    conn.commit()
    s.check("a key that has run out opens nothing", anon.post(
        "/api/guest-lookup", data={"email": "nobody@example.invalid"},
        headers={"Authorization": f"Bearer {old_key}"}).status_code == 404)
    leaver = conn.execute(
        """INSERT INTO users (email, password_hash, role, name, status, created_at)
           VALUES (?, 'x', 'employee', ?, 'inactive', ?)""",
        (f"{TAG.lower()}.leaver@example.invalid", f"{TAG} Leaver",
         m.datetime.now(m.timezone.utc).isoformat())).lastrowid
    leaver_key, _ = m.addin_issue_session(conn, leaver, "test", label=f"{TAG} leaver")
    conn.commit()
    s.check("nor does one belonging to somebody who has left", anon.post(
        "/api/guest-lookup", data={"email": "nobody@example.invalid"},
        headers={"Authorization": f"Bearer {leaver_key}"}).status_code == 404)
    fresh_key, _ = m.addin_issue_session(conn, owner["id"], "test", label=f"{TAG} stamp")
    conn.commit()
    anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid"},
              headers={"Authorization": f"Bearer {fresh_key}"})
    first = conn.execute("SELECT last_used_at FROM addin_sessions WHERE label = ?",
                         (f"{TAG} stamp",)).fetchone()["last_used_at"]
    anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid"},
              headers={"Authorization": f"Bearer {fresh_key}"})
    second = conn.execute("SELECT last_used_at FROM addin_sessions WHERE label = ?",
                          (f"{TAG} stamp",)).fetchone()["last_used_at"]
    s.check("use is stamped, but not on every request", first == second)

    s.section("The shared key is a fallback, as staff, and can be switched off")
    was = m.GUEST_LOOKUP_TOKEN
    m.GUEST_LOOKUP_TOKEN = "zz-shared-0123456789"
    try:
        r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid",
                                                 "token": "zz-shared-0123456789"})
        got = r.get_json() or {}
        s.check("it opens the panel", r.status_code == 200)
        s.check("as staff, never with money", (got.get("who") or {}).get("level") == "staff")
        r = oc.post("/admin/outlook-addin/settings", data={"house_notes": "Dogs welcome."},
                    follow_redirects=True)
        s.check("the owner switches it off", "only people signed in to Microsoft 365"
                in r.get_data(as_text=True), response=r)
        r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid",
                                                 "token": "zz-shared-0123456789"})
        s.check("and it opens nothing", r.status_code == 404)
        s.check("while a person's key still works", anon.post(
            "/api/guest-lookup", data={"email": "nobody@example.invalid"},
            headers={"Authorization": f"Bearer {fresh_key}"}).status_code == 200)
        r = oc.post("/admin/outlook-addin/settings", data={"shared_key_allowed": "1",
                                                           "house_notes": "Dogs welcome."},
                    follow_redirects=True)
        s.check("and back on", "still works, as staff" in r.get_data(as_text=True))
        s.check("the house notes are kept for the drafter",
                conn.execute("SELECT value FROM app_settings WHERE key = ?",
                             (m.ADDIN_HOUSE_NOTES_SETTING,)).fetchone()["value"] == "Dogs welcome.")

        s.section("Limits are per person")
        now_iso = m.datetime.now(m.timezone.utc).isoformat()
        conn.executemany("INSERT INTO submission_log (ip_address, action, created_at) VALUES (?, ?, ?)",
                         [("127.0.0.1", f"guest_lookup:u{owner['id']}", now_iso)] * 600)
        conn.commit()
        r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid"},
                      headers={"Authorization": f"Bearer {fresh_key}"})
        s.check("somebody who has used up theirs is told to wait", r.status_code == 429)
        r = anon.post("/api/guest-lookup", data={"email": "nobody@example.invalid",
                                                 "token": "zz-shared-0123456789"})
        s.check("while everybody else in the house carries on", r.status_code == 200)
    finally:
        m.GUEST_LOOKUP_TOKEN = was

    s.section("Each inbox's signature")
    conn.execute("INSERT INTO mailbox_routing (mailbox, label, active, created_at) VALUES (?, ?, 1, ?)",
                 (f"{TAG.lower()}.experience@example.invalid", "Experience",
                  m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    mb = conn.execute("SELECT id FROM mailbox_routing WHERE mailbox = ?",
                      (f"{TAG.lower()}.experience@example.invalid",)).fetchone()["id"]
    s.check("an inbox with no signature set has a sensible one",
            m.mailbox_signature(conn, f"{TAG.lower()}.experience@example.invalid") == "The Château Team")
    s.check("experience@ signs as Jasmine, as the owner said",
            m.mailbox_signature(conn, "experience@chateaugudanes.com") == "Jasmine")
    s.check("and accounts@ is the owner's alone by default", m.mailbox_is_owner_only(
        {"mailbox": "accounts@chateaugudanes.com", "owner_only": None}))
    r = oc.post("/admin/outlook-addin/mailboxes",
                data={f"signature_{mb}": "Jasmine Dupont", f"owner_only_{mb}": "1"},
                follow_redirects=True)
    s.check("the owner sets one", "Saved" in r.get_data(as_text=True), response=r)
    s.check("and it is used",
            m.mailbox_signature(conn, f"{TAG.lower()}.experience@example.invalid") == "Jasmine Dupont")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
