"""Changing the vault key without losing what it locks.

One key locks the Vault's passwords and the bank details, and until this the
app held that one key and nothing else. Change it and every secret failed to
open: no error, and the readiness line still said "Set." Worse, the edit form
read a blank password box as "keep what is there" -- and what was there,
opened with the wrong key, was nothing. So the first time anybody corrected
an entry's title after a change of key, its password was saved as blank, for
good, and nothing said so.

Checked here: a key being retired still opens everything; one button moves
it all onto the new key without changing a word of it; what no key opens is
named, never drawn as blank; and a blank box is never allowed to mean "save
nothing".
"""
import html

from cryptography.fernet import Fernet, InvalidToken

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZKEYCHANGE"
PASSWORD = "plum-orchard-4471"
NOTES = "PIN is on the back of the card"
ACCOUNT, IBAN, SWIFT = "00012345678", "FR7630006000011234567890189", "BNPAFRPPXXX"
A, B, C = (Fernet.generate_key().decode() for _ in range(3))


def flashes(response):
    """The page's messages as read, not as escaped in the HTML."""
    return [html.unescape(x) for x in _harness.flashes(response)]


def _keys(current, previous=""):
    m.VAULT_ENCRYPTION_KEY = current
    m.VAULT_ENCRYPTION_KEY_PREVIOUS = previous


def _ours(items):
    return [i for i in items if i["name"].startswith(TAG)]


def _readiness(conn):
    return next(r for r in m.readiness_checks(conn, include_slow=False)
                if r["label"] == "Vault encryption key")


def _opens_with(key, token):
    try:
        Fernet(key.encode()).decrypt(token.encode())
        return True
    except InvalidToken:
        return False


def run():
    s = Suite("vault key change")
    oc, ec, _owner, _emp = clients()
    conn = db()
    was = (m.VAULT_ENCRYPTION_KEY, m.VAULT_ENCRYPTION_KEY_PREVIOUS)
    # Whatever this copy of the database already holds was locked with a key
    # this suite does not have, so it would read as unreadable all the way
    # through and every count and message would be about it. Set it aside.
    # Nothing references either table, so nothing else goes with it.
    put_back = [_harness.keep_rows(conn, "vault_entries", "1 = 1"),
                _harness.keep_rows(conn, "bank_details", "1 = 1")]
    conn.execute("DELETE FROM vault_entries")
    conn.execute("DELETE FROM bank_details")
    conn.commit()
    try:
        _checks(s, oc, ec, conn)
    finally:
        m.VAULT_ENCRYPTION_KEY, m.VAULT_ENCRYPTION_KEY_PREVIOUS = was
        for restore in put_back:
            restore()
    return s


def _checks(s, oc, ec, conn):
    s.section("Two secrets, locked with the first key")
    _keys(A)
    oc.post("/management/vault/new", data={
        "title": TAG + " Supplier portal", "username": "chateau",
        "password": PASSWORD, "notes": NOTES})
    oc.post("/management/bank-details/new", data={
        "label": TAG + " Operating account", "bank_name": "BNP",
        "account_number": ACCOUNT, "iban": IBAN, "swift_bic": SWIFT})
    vault = conn.execute("SELECT * FROM vault_entries WHERE title LIKE ?",
                         (TAG + "%",)).fetchone()
    bank = conn.execute("SELECT * FROM bank_details WHERE label LIKE ?",
                        (TAG + "%",)).fetchone()
    s.check("both are stored", vault is not None and bank is not None)
    if vault is None or bank is None:
        return
    v_url, b_url = f"/management/vault/{vault['id']}", f"/management/bank-details/{bank['id']}"
    s.check("and the readiness line has nothing to say", _readiness(conn)["ok"] is True,
            detail=_readiness(conn)["detail"])

    s.section("A new key, with the old one kept, still opens everything")
    _keys(B, A)
    got = oc.post(v_url + "/reveal").get_json()
    s.check("the password reveals", got.get("password") == PASSWORD, detail=str(got))
    got = oc.post(b_url + "/reveal").get_json()
    s.check("and the IBAN", got.get("iban") == IBAN, detail=str(got))
    census = m.vault_key_census(conn)
    s.check("both are counted as on the previous key",
            len(_ours(census["previous"])) == 2 and not _ours(census["current"]),
            detail=str({k: len(_ours(census[k])) for k in ("current", "previous", "unreadable")}))
    page = oc.get("/management/vault").get_data(as_text=True)
    s.check("the Vault says two are still on the previous key",
            "2 still locked with the previous key" in page)
    s.check("and names them, bank details included",
            TAG + " Supplier portal (Vault)" in page
            and TAG + " Operating account (Bank details)" in page)
    s.check("with the button that moves them", "Lock them with the new key" in page)
    s.check("and the page carries no secret or token while it says so",
            PASSWORD not in page and IBAN not in page and vault["secret_encrypted"] not in page)
    bank_page = oc.get("/management/bank-details").get_data(as_text=True)
    s.check("Bank details says the same, since one key locks both",
            "2 still locked with the previous key" in bank_page)
    line = _readiness(conn)
    s.check("readiness says the change is half done",
            line["ok"] is False and "still locked with the previous key" in line["detail"],
            detail=line["detail"])

    s.section("One button moves them, and changes nothing else")
    r = oc.post("/management/vault/relock", follow_redirects=True)
    msgs = flashes(r)
    s.check("it says what it moved", any("Re-locked 2 secrets." in x for x in msgs),
            r, detail=str(msgs))
    v2 = conn.execute("SELECT * FROM vault_entries WHERE id = ?", (vault["id"],)).fetchone()
    b2 = conn.execute("SELECT * FROM bank_details WHERE id = ?", (bank["id"],)).fetchone()
    s.check("the new key alone now opens them",
            _opens_with(B, v2["secret_encrypted"]) and _opens_with(B, b2["sensitive_encrypted"]))
    s.check("and the old one opens neither",
            not _opens_with(A, v2["secret_encrypted"])
            and not _opens_with(A, b2["sensitive_encrypted"]))
    _keys(B)
    s.check("not a word of the password or notes changed",
            m.vault_decrypt(v2["secret_encrypted"]) == {"password": PASSWORD, "notes": NOTES})
    s.check("nor of the bank details",
            m.fernet_decrypt_json(b2["sensitive_encrypted"])
            == {"account_number": ACCOUNT, "iban": IBAN, "swift_bic": SWIFT})
    s.check("and 'last changed' still says when somebody last changed them",
            v2["updated_at"] == vault["updated_at"] and b2["updated_at"] == bank["updated_at"])
    audit = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'vault_relocked' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    s.check("the audit log names what moved",
            audit is not None and TAG + " Supplier portal" in (audit["details"] or "")
            and TAG + " Operating account" in (audit["details"] or ""),
            detail=None if audit is None else audit["details"])
    _keys(B, A)
    page = oc.get("/management/vault").get_data(as_text=True)
    s.check("the page now says the old key can go",
            "Everything is locked with the current key" in page
            and "Lock them with the new key" not in page)
    s.check("and so does readiness",
            "Delete VAULT_ENCRYPTION_KEY_PREVIOUS" in _readiness(conn)["detail"])
    msgs = flashes(oc.post("/management/vault/relock", follow_redirects=True))
    s.check("pressing it again says there was nothing to move",
            any("already locked with the current key" in x for x in msgs), detail=str(msgs))

    s.section("With the old key deleted, nothing is lost")
    _keys(B)
    s.check("the password still reveals",
            oc.post(v_url + "/reveal").get_json().get("password") == PASSWORD)
    s.check("the panel has gone",
            "Changing the vault key" not in oc.get("/management/vault").get_data(as_text=True))
    s.check("and readiness is back to plain set", _readiness(conn)["ok"] is True,
            detail=_readiness(conn)["detail"])

    s.section("A key changed without keeping the old one: named, never blanked")
    _keys(C)
    census = m.vault_key_census(conn)
    s.check("both count as unreadable", len(_ours(census["unreadable"])) == 2)
    page = oc.get("/management/vault").get_data(as_text=True)
    s.check("the page names them",
            "No key the app holds opens" in page and TAG + " Supplier portal (Vault)" in page)
    got = oc.post(v_url + "/reveal").get_json()
    s.check("revealing says it can't be opened, not '(no password set)'",
            "error" in got and "password" not in got, detail=str(got))
    got = oc.post(b_url + "/reveal").get_json()
    s.check("and the bank details likewise", "error" in got and "iban" not in got,
            detail=str(got))
    # The bug this exists for: a title corrected, both secret boxes left blank.
    before = conn.execute("SELECT secret_encrypted FROM vault_entries WHERE id = ?",
                          (vault["id"],)).fetchone()[0]
    r = oc.post(v_url + "/edit", data={"title": TAG + " Supplier portal (renamed)",
                                       "username": "chateau", "url": "",
                                       "password": "", "notes": ""},
                follow_redirects=True)
    after = conn.execute("SELECT title, secret_encrypted FROM vault_entries WHERE id = ?",
                         (vault["id"],)).fetchone()
    s.check("saving with blank boxes is refused", any("can't be opened" in x for x in flashes(r)),
            r, detail=str(flashes(r)))
    s.check("and the stored password is exactly as it was, not blank",
            after["secret_encrypted"] == before and after["title"] == TAG + " Supplier portal")
    before = conn.execute("SELECT sensitive_encrypted FROM bank_details WHERE id = ?",
                          (bank["id"],)).fetchone()[0]
    r = oc.post(b_url + "/edit", data={"label": TAG + " Operating account",
                                       "iban": "FR0000000000000000000000000"},
                follow_redirects=True)
    s.check("a bank detail half filled in is refused too",
            any("can't be opened" in x for x in flashes(r)), r, detail=str(flashes(r)))
    s.check("and its account number and SWIFT are not blanked",
            conn.execute("SELECT sensitive_encrypted FROM bank_details WHERE id = ?",
                         (bank["id"],)).fetchone()[0] == before)
    msgs = flashes(oc.post("/management/vault/relock", follow_redirects=True))
    s.check("the button names what it could not move, and why",
            any("Nothing was re-locked" in x and "no key the app holds opens it" in x
                and TAG + " Supplier portal (Vault)" in x for x in msgs), detail=str(msgs))
    line = _readiness(conn)
    s.check("readiness names them", line["ok"] is False
            and "can't be opened with any key" in line["detail"]
            and TAG + " Operating account (Bank details)" in line["detail"],
            detail=line["detail"])
    _keys(C, B)
    s.check("and giving the app the key they were locked with brings them back",
            oc.post(v_url + "/reveal").get_json().get("password") == PASSWORD)

    s.section("Each blank box keeps its own value")
    _keys(B)
    oc.post(v_url + "/edit", data={"title": TAG + " Supplier portal", "username": "chateau",
                                   "url": "", "password": "a-new-password-5520", "notes": ""})
    kept = m.vault_decrypt(conn.execute("SELECT secret_encrypted FROM vault_entries WHERE id = ?",
                                        (vault["id"],)).fetchone()[0])
    s.check("a new password keeps the notes",
            kept == {"password": "a-new-password-5520", "notes": NOTES}, detail=str(kept))
    oc.post(b_url + "/edit", data={"label": TAG + " Operating account",
                                   "iban": "FR7612345000019876543210987"})
    kept = m.fernet_decrypt_json(conn.execute(
        "SELECT sensitive_encrypted FROM bank_details WHERE id = ?", (bank["id"],)).fetchone()[0])
    s.check("a new IBAN keeps the account number and SWIFT",
            kept == {"account_number": ACCOUNT, "iban": "FR7612345000019876543210987",
                     "swift_bic": SWIFT}, detail=str(kept))

    s.section("A mistyped key is named, not a crash")
    _keys(B, "not-a-vault-key")
    r = oc.get("/management/vault")
    s.check("the Vault still opens", r.status_code == 200, r)
    s.check("and says the previous-key setting holds something that is not a key",
            "1 value(s) that are not vault keys" in r.get_data(as_text=True))
    s.check("while everything on the current key still reveals",
            oc.post(v_url + "/reveal").get_json().get("password") == "a-new-password-5520")
    _keys("too-short")
    r = oc.get("/management/vault")
    s.check("a current key that is not a key: the page opens", r.status_code == 200, r)
    s.check("and says so", "not a vault key" in " ".join(r.get_data(as_text=True).split()))
    line = _readiness(conn)
    s.check("readiness makes it a blocker",
            line["severity"] == "blocker" and line["ok"] is False, detail=str(line))

    s.section("Guards")
    _keys(B, A)
    s.check("an employee cannot move the keys",
            ec.post("/management/vault/relock").status_code in (302, 403))
    _keys("")
    s.check("with no vault key at all it is a 404",
            oc.post("/management/vault/relock").status_code == 404)


if __name__ == "__main__":
    print(run().report())
