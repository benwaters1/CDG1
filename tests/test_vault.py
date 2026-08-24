"""The vault — the one place in this app that holds real secrets.

Three properties matter, and none of them were tested:

  1. What is stored is genuinely encrypted. A "vault" whose rows contain the
     password in readable form is a filing cabinet with a picture of a lock on
     it, and a stolen database backup is then a stolen password list.
  2. The list page never emits a secret. It is rendered far more often than
     anybody actually needs a password, so the secret is fetched only by an
     explicit reveal.
  3. Every reveal is recorded. The audit trail is the only thing that can
     answer "who looked at the bank password, and when".

The encryption key is not set in this environment, which is itself correct
behaviour to check — every route refuses rather than storing plaintext. The
crypto is then exercised by setting the key for the duration of this suite,
because "it encrypts" is the claim the whole feature rests on.
"""
import json

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZVAULT"
SECRET = "correct-horse-battery-staple-7431"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM vault_entries WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Vault")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("With no key set, nothing is stored at all")
    # The safe failure: refuse, rather than write the password in the clear
    # and encrypt it "later".
    original_key = m.VAULT_ENCRYPTION_KEY
    m.VAULT_ENCRYPTION_KEY = None
    r = oc.post("/management/vault/new", data={
        "title": f"{TAG} nokey", "username": "x", "password": SECRET, "notes": "",
    }, follow_redirects=True)
    conn = db()
    leaked = conn.execute(
        "SELECT COUNT(*) AS c FROM vault_entries WHERE title LIKE ?", (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("no entry is created without a key", leaked == 0, r,
            detail=f"{leaked} row(s) written with encryption unavailable")

    # A real Fernet key for the rest of the suite.
    from cryptography.fernet import Fernet
    m.VAULT_ENCRYPTION_KEY = Fernet.generate_key().decode()

    s.section("What lands in the database is ciphertext")
    r2 = oc.post("/management/vault/new", data={
        "title": f"{TAG} Bank", "username": "chateau", "password": SECRET,
        "notes": "second line of the note",
    }, follow_redirects=True)
    conn = db()
    row = conn.execute(
        "SELECT * FROM vault_entries WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    s.check("the entry is created", row is not None, r2)
    if row is None:
        m.VAULT_ENCRYPTION_KEY = original_key
        return s

    blob = row["secret_encrypted"] or ""
    s.check("the secret is NOT sitting in the row in readable form",
            SECRET not in blob, detail="the password is stored in the clear")
    s.check("nor is the note", "second line of the note" not in blob)
    s.check("and it looks like a Fernet token", blob.startswith("gAAAA"),
            detail=f"starts with {blob[:8]!r}")

    s.section("But it round-trips")
    back = m.vault_decrypt(blob)
    s.check("the password comes back intact", back.get("password") == SECRET)
    s.check("and the note with it", back.get("notes") == "second line of the note")

    s.section("A wrong key cannot read it")
    good_key = m.VAULT_ENCRYPTION_KEY
    m.VAULT_ENCRYPTION_KEY = Fernet.generate_key().decode()
    wrong = m.vault_decrypt(blob)
    m.VAULT_ENCRYPTION_KEY = good_key
    s.check("decrypting with a different key fails safely rather than raising",
            wrong.get("error") is True and wrong.get("password") is None,
            detail=f"got {wrong}")

    s.section("The list page does not carry the secret")
    page = oc.get("/management/vault")
    html = page.get_data(as_text=True)
    s.check("the page loads", page.status_code == 200, page)
    s.check("the password is not in the HTML", SECRET not in html,
            detail="the vault list renders the secret to anyone who views source")
    s.check("nor is the ciphertext", blob not in html)
    s.check("but the entry is listed by title", f"{TAG} Bank" in html)

    s.section("Revealing is explicit, and recorded")
    conn = db()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'vault_entry_revealed'").fetchone()["c"]
    conn.close()
    rev = oc.post(f"/management/vault/{row['id']}/reveal")
    body = rev.get_json() if rev.status_code == 200 else {}
    s.check("the reveal returns the password", body.get("password") == SECRET,
            detail=f"HTTP {rev.status_code}, body {body}")
    conn = db()
    after = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'vault_entry_revealed'").fetchone()["c"]
    conn.close()
    s.check("and it is written to the audit log — this is the only record of who looked",
            after == before + 1, detail=f"{before} -> {after}")

    s.section("Editing keeps it encrypted")
    oc.post(f"/management/vault/{row['id']}/edit", data={
        "title": f"{TAG} Bank", "username": "chateau", "password": "a-different-secret-9182",
        "notes": "",
    }, follow_redirects=True)
    conn = db()
    edited = conn.execute(
        "SELECT secret_encrypted FROM vault_entries WHERE id = ?", (row["id"],)).fetchone()
    conn.close()
    s.check("the new secret is not readable in the row",
            "a-different-secret-9182" not in (edited["secret_encrypted"] or ""))
    s.check("and it decrypts to the new value",
            m.vault_decrypt(edited["secret_encrypted"]).get("password") == "a-different-secret-9182")

    s.section("Guards")
    s.check("an employee cannot see the vault",
            ec.get("/management/vault").status_code in (302, 403))
    s.check("an employee cannot reveal an entry",
            ec.post(f"/management/vault/{row['id']}/reveal").status_code in (302, 403))
    s.check("revealing something that does not exist is a 404",
            oc.post("/management/vault/999999/reveal").status_code == 404)

    m.VAULT_ENCRYPTION_KEY = original_key
    _cleanup()
    return s
