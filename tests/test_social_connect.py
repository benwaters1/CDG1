"""Where the Instagram and Page details are put in, and what must not leak.

Small page, three rules, and all three fail in a way nobody would see.

  THE TOKEN IS NEVER SENT BACK. Not in a value, not in a placeholder, not in a
  comment. A password field is no help at all here: it still puts the real
  characters in the HTML, so View Source, every cache between here and there,
  and anybody looking over a shoulder all have it. The page shows the last
  four and nothing else.

  AN EMPTY BOX MEANS "LEAVE IT ALONE". The field somebody actually comes back
  to edit is the expiry date, and if saving the form with an empty token box
  wiped the token, correcting that date would silently disconnect the
  account — and the first sign would be a post that did not go out.

  AND THE AUDIT TRAIL RECORDS THE CHANGE, NEVER THE VALUE. A log holding a
  live credential is a second place it has to be kept safe, and the one nobody
  thinks of.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TOKEN = "zzconnect-token-abcdefghijkl-7788"
LATER = "zzconnect-second-token-mnopqrst-9911"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key IN "
                 "('meta_page_id','meta_ig_user_id','meta_access_token',"
                 "'meta_token_expires_at')")
    conn.execute("DELETE FROM audit_log WHERE action = 'social_accounts_connected'")
    conn.commit()
    conn.close()


def _setting(key):
    conn = db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                           (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Connecting Instagram and the Page")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("Before anything is connected")
    page = oc.get("/management/social/connect")
    body = page.get_data(as_text=True)
    s.check("the owner can open it", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    s.check("and it says plainly that nothing is connected",
            "not connected" in body.lower(),
            detail="a page that looks configured when it is not is how a post "
                   "fails at ten at night")
    s.check("an employee cannot",
            ec.get("/management/social/connect").status_code != 200)

    s.section("Putting the details in")
    saved = oc.post("/management/social/connect", data={
        "page_id": "1234509876", "ig_user_id": "17841400000000001",
        "access_token": TOKEN, "expires_on": "2027-01-31",
    }, follow_redirects=True)
    s.check("it saves", saved.status_code == 200)
    s.check("the Page id is kept", _setting("meta_page_id") == "1234509876")
    s.check("the Instagram id too",
            _setting("meta_ig_user_id") == "17841400000000001")
    s.check("and the token", _setting("meta_access_token") == TOKEN)
    s.check("with the day it runs out",
            _setting("meta_token_expires_at") == "2027-01-31",
            detail="the only reason anybody is warned before a post fails")

    s.section("What the page gives back")
    back = oc.get("/management/social/connect").get_data(as_text=True)
    s.check("the token is NOT in the page",
            TOKEN not in back,
            detail="a password field still puts the real characters in the "
                   "HTML — View Source, and every cache in between, has it")
    s.check("not even most of it",
            "abcdefghijkl" not in back,
            detail="a partly masked secret is a secret")
    s.check("but it says one is held, by its last four",
            "7788" in back,
            detail="enough to tell one token from another, worth nothing to "
                   "anybody who sees the screen")
    s.check("and the ids ARE shown, because they are not secrets",
            "1234509876" in back and "17841400000000001" in back)

    s.section("Saving again with the token box empty")
    # The field somebody comes back to edit is the date.
    oc.post("/management/social/connect", data={
        "page_id": "1234509876", "ig_user_id": "17841400000000001",
        "access_token": "", "expires_on": "2027-03-15",
    }, follow_redirects=True)
    s.check("the date changes", _setting("meta_token_expires_at") == "2027-03-15")
    s.check("and the token is still there",
            _setting("meta_access_token") == TOKEN,
            detail=f"{'gone' if not _setting('meta_access_token') else 'held'} "
                   "— wiping it here would disconnect the account silently, "
                   "while the page went on saying Connected")

    s.section("Replacing the token")
    oc.post("/management/social/connect", data={
        "page_id": "1234509876", "ig_user_id": "17841400000000001",
        "access_token": LATER, "expires_on": "2027-03-15",
    }, follow_redirects=True)
    s.check("typing a new one replaces it",
            _setting("meta_access_token") == LATER)

    s.section("A date that is not a date")
    oc.post("/management/social/connect", data={
        "page_id": "1234509876", "ig_user_id": "17841400000000001",
        "access_token": "", "expires_on": "the end of March",
    }, follow_redirects=True)
    s.check("is refused rather than stored",
            _setting("meta_token_expires_at") == "2027-03-15",
            detail=f"{_setting('meta_token_expires_at')} — an unreadable date "
                   "is no expiry at all, and no expiry means no warning")

    s.section("What the audit trail keeps")
    conn = db()
    entries = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'social_accounts_connected'"
    ).fetchall()
    conn.close()
    s.check("the change is recorded", len(entries) >= 2,
            detail=f"{len(entries)} entries")
    joined = " ".join((e["target"] or "") for e in entries)
    s.check("saying what changed", "token" in joined, detail=f"{joined!r}")
    s.check("and never the value itself",
            TOKEN not in joined and LATER not in joined,
            detail="an audit trail holding a live credential is a second place "
                   "it has to be kept safe, and the one nobody thinks of")

    s.section("Once it is connected")
    now = oc.get("/management/social/connect").get_data(as_text=True)
    s.check("the page says so", "connected" in now.lower()
            and "not connected" not in now.lower())
    s.check("and warns that publishing is still switched off",
            "switched" in now.lower() and "off" in now.lower(),
            detail="connected but not publishing is the state somebody would "
                   "otherwise discover by a post not going out")

    _cleanup()
    return s
