"""The no-login endpoints, and the posture they are supposed to keep.

Four routes with no login behind them, guarded by a shared secret. Every test
of them so far reached the guard, got the 404 it gives when no token is set,
and moved on -- so the coverage figure counted them while nothing had ever
been past the door.

The postures they document, none of which anything checked:

  A WRONG TOKEN IS 404, NOT 403. "So a prober learns nothing" -- a 403 says
  there is something here to get into, and 404 says there is not. It is one
  word in one line and it is the whole of that decision.

  AN UNSET TOKEN MEANS THE ENDPOINT IS NOT THERE. `not GUEST_LOOKUP_TOKEN or
  not compare_digest(...)`. Drop the first half and an unconfigured
  deployment answers to an empty token, which is every deployment that has
  not set one.

  AND A GET CAN NEVER CARRY A VALID ONE. The guest-lookup docstring is
  explicit: the token is read from the BODY, because a token in a query
  string lands in every access log line, on every lookup, for ever -- "a
  permanent, unrevoked read-any-guest credential to anyone who can ever read
  those logs". GET is accepted only so the same guard can 404 it rather than
  letting Flask answer 405 and confirm the endpoint exists.

  That last one is a single word away from gone. request.form -> request.values
  is the kind of edit somebody makes to be helpful, it breaks nothing, no page
  changes, and the credential starts appearing in the logs. It is checked
  here by sending a VALID token in the query string and requiring a 404.

The two scheduler endpoints read theirs from the query string on purpose --
an external cron calls a URL -- and the difference is now written down where
somebody would otherwise harmonise it the wrong way.
"""
from _harness import Suite, db

import _harness

m = _harness.m
TAG = "ZZAPI"
TOKEN = "zz-test-token-" + "0123456789abcdef"


class _Tokens:
    """Set the shared secrets for the length of this suite.

    They come from the environment and are empty here, which is why every
    one of these endpoints has only ever answered 404. Set on the module
    rather than in os.environ, because app.py reads them once at import --
    and put back afterwards, or every later suite is running against an app
    with live add-in credentials.
    """

    NAMES = ("GUEST_LOOKUP_TOKEN", "DIGEST_TOKEN", "ICAL_SYNC_TOKEN")

    def __enter__(self):
        self.was = {n: getattr(m, n, "") for n in self.NAMES}
        for n in self.NAMES:
            setattr(m, n, TOKEN)
        return self

    def __exit__(self, *_exc):
        for n, v in self.was.items():
            setattr(m, n, v)
        return False


def _seed_rate_limit(conn, action, count):
    """Fill the window rather than making sixty requests to fill it."""
    now = m.datetime.now(m.timezone.utc).isoformat()
    for _ in range(count):
        conn.execute(
            "INSERT INTO submission_log (ip_address, action, created_at) "
            "VALUES ('127.0.0.1', ?, ?)", (action, now))
    conn.commit()


def run():
    s = Suite("the no-login endpoints and their posture")
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action IN "
                 "('guest_lookup', 'check_send_conflict')")
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG + "%",))
    conn.commit()
    c = m.app.test_client()

    s.section("With no token set, there is nothing there")
    # The state every deployment starts in, and the reason these endpoints
    # have only ever answered 404 in this suite.
    r = c.post("/api/guest-lookup", data={"token": "", "email": "a@b.invalid"})
    s.check("an empty token is refused", r.status_code == 404,
            detail=f"status {r.status_code}")
    r = c.post("/api/guest-lookup", data={"token": TOKEN,
                                          "email": "a@b.invalid"})
    s.check("and so is a real-looking one", r.status_code == 404,
            detail="an unconfigured deployment must not answer to a guess")

    with _Tokens():
        s.section("A wrong token is a 404, never a 403")
        for path, data in (("/api/guest-lookup", {"token": "wrong"}),
                           ("/api/check-send-conflict", {"token": "wrong"})):
            r = c.post(path, data=data)
            s.check(f"{path} refuses without admitting it exists",
                    r.status_code == 404,
                    detail=f"status {r.status_code} — 403 tells a prober "
                           "there is something here to get into")

        r = c.get("/api/owner-digest?token=wrong")
        s.check("/api/owner-digest the same", r.status_code == 404,
                detail=f"status {r.status_code}")

        s.section("A GET can never carry a valid token")
        # The one that would go quietly. request.form -> request.values is an
        # edit that breaks nothing, changes no page, and starts writing a
        # read-any-guest credential into every access log line.
        r = c.get(f"/api/guest-lookup?token={TOKEN}&email=a@b.invalid")
        s.check("the guest lookup refuses it", r.status_code == 404,
                detail=f"status {r.status_code} — a token in a query string "
                       "is in the logs for ever, and cannot be revoked by "
                       "anyone who does not know it is there")
        r = c.get(f"/api/check-send-conflict?token={TOKEN}")
        s.check("and so does the send check", r.status_code == 404,
                detail=f"status {r.status_code}")

        s.section("With the token in the body, it answers")
        conn.execute(
            "INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
            (TAG + " Visitor", f"{TAG}.v@example.invalid".lower(),
             m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()
        r = c.post("/api/guest-lookup",
                   data={"token": TOKEN,
                         "email": f"{TAG}.v@example.invalid".lower()})
        s.check("the lookup runs", r.status_code == 200,
                detail=f"status {r.status_code}")
        s.check("and answers with JSON rather than a page",
                r.is_json, detail=r.content_type)

        s.section("A bad address is answered, not raised")
        r = c.post("/api/guest-lookup", data={"token": TOKEN,
                                              "email": "not-an-address"})
        s.check("it is a 400", r.status_code == 400,
                detail=f"status {r.status_code}")
        s.check("with a reason the add-in can read",
                r.is_json and "error" in r.get_json())

        s.section("The send check answers false rather than blocking")
        # Its own docstring: any failure here must never be the reason a
        # legitimate email cannot send. A clean draft is 'no conflict', and
        # that is what the add-in treats an error as too.
        r = c.post("/api/check-send-conflict",
                   data={"token": TOKEN, "recipient_email": "nobody@example.invalid",
                         "subject": "Hello", "body": "Nothing about money here."})
        s.check("it answers", r.status_code == 200,
                detail=f"status {r.status_code}")
        s.check("and says there is no conflict",
                r.is_json and r.get_json().get("conflict") is False,
                detail=str(r.get_json())[:120])

        s.section("And it says no rather than failing open when hammered")
        _seed_rate_limit(conn, "guest_lookup", 60)
        r = c.post("/api/guest-lookup",
                   data={"token": TOKEN,
                         "email": f"{TAG}.v@example.invalid".lower()})
        s.check("the lookup is rate limited", r.status_code == 429,
                detail=f"status {r.status_code} — a shared secret with no "
                       "limit behind it is a way to read the guest list one "
                       "address at a time")
        s.check("and says so rather than erroring",
                r.is_json and "error" in r.get_json())

        s.section("The scheduler endpoints take theirs from the URL, on purpose")
        r = c.get(f"/api/owner-digest?token={TOKEN}")
        s.check("the digest runs", r.status_code == 200,
                detail=f"status {r.status_code}")
        s.check("and says who it went to",
                r.is_json and "to" in r.get_json(),
                detail=str(r.get_json())[:120])

    s.section("And the tokens are back off afterwards")
    s.check("guest lookup is a 404 again",
            c.post("/api/guest-lookup",
                   data={"token": TOKEN}).status_code == 404,
            detail="left set, every later suite would be running an app with "
                   "live add-in credentials")

    conn.execute("DELETE FROM submission_log WHERE action IN "
                 "('guest_lookup', 'check_send_conflict')")
    conn.execute("DELETE FROM guests WHERE email LIKE ?",
                 ("%" + TAG.lower() + "%",))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
