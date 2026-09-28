"""The Instagram token: what Meta says it is, and making it the kind that lasts.

The token was the one silent failure left in publishing. A person's token
lasts about sixty days; everything works, everybody forgets, and one evening
a post does not go out. The first answer was a date typed into a form and a
warning built on it -- right exactly as long as somebody remembered to change
the date.

Meta's own documentation changes that:

  - Instagram publishing through Facebook Login takes a PAGE access token.
  - A long-lived Page token "do[es] not have an expiration date", and the
    documented way to one is me/accounts, asked with a long-lived user token.

So the app ASKS Meta (debug_token) what the stored token is, and when it is
the kind that lapses, swaps it for the Page's own. At the moment it is saved,
because the Graph API Explorer's token lasts about an hour; and daily after.

  NOTHING IS REPLACED UNLESS EVERY STEP WORKED. A swap that stopped halfway
  and wrote the long-lived PERSON's token over a working one would be a token
  that lapses in sixty days, put there by the thing meant to stop that.

  NEVER SIMPLY THE FIRST PAGE META LISTS. Somebody who manages two Pages
  would have the house posting as the other one.

  WHAT META SAID IS ABOUT ONE TOKEN. Paste a new one and the old verdict goes,
  Meta's date included, or the page shows a judgement on a token that is no
  longer there.

  INSTAGRAM HAS ITS OWN CLOCK. Meta's ninety-day data access expiry spares the
  Page's permissions and not Instagram's, so a token with no expiry can still
  stop posting to Instagram on a date Meta states. Only a person can renew
  that, so the owner home says when.

  AND "NO EXPIRY" IS SAID, NOT "NEVER". debug_token reports expires_at 0 for
  a token with no expiry date, and its reference page does not define 0.

Meta is stood in at meta_request -- the one door out, and the one the harness
already blocks -- and the stand-in is checked put back.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
PAGE = "1234509877"
OTHER_PAGE = "5550001111"
IG = "17841400000000009"
APP_ID = "987654321"
SECRET = "zz-app-secret-do-not-echo-Qz9w"
USER_TOKEN = "EAAzz-user-token-lapses-in-ten-days-0001"
FRESH_TOKEN = "EAAzz-explorer-token-lasts-an-hour-0005"
PAGE_TOKEN = "EAAzz-page-token-reports-no-expiry-0002"
OTHER_PAGE_TOKEN = "EAAzz-somebody-elses-page-token-0006"
DEAD_TOKEN = "EAAzz-token-meta-says-is-dead-0003"
CONNECT = "/management/social/connect"
# Everything the house's two accounts need, as a token made by the steps on
# the connect page would carry it.
FULL_SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
               "instagram_basic", "instagram_content_publish", "public_profile"]


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'meta\\_%' ESCAPE '\\'")
    conn.execute("DELETE FROM audit_log WHERE action IN "
                 "('meta_token_swapped_for_page_token', 'social_accounts_connected')")
    conn.execute("DELETE FROM automation_runs WHERE job_name = 'meta_token'")
    conn.commit()
    conn.close()


def _set(**values):
    conn = db()
    for key, value in values.items():
        m._set_meta(conn, key, value)
    conn.commit()
    conn.close()


def _get(key):
    conn = db()
    try:
        return m.meta_setting(conn, key)
    finally:
        conn.close()


def _state():
    conn = db()
    try:
        return m.meta_token_state(conn)
    finally:
        conn.close()


def _unix(days):
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())


def _house_day(unix):
    return m.house_date_iso(datetime.fromtimestamp(unix, timezone.utc).isoformat())


class FakeGraph:
    """Answers the Graph calls the token code makes, and remembers them."""

    def __init__(self, exchange_fails=False, page_missing=False,
                 unreachable=False, data_access_days=80, malformed=False,
                 recheck_fails=False, scopes=FULL_SCOPES, granular=None):
        self.calls = []
        self.exchange_fails = exchange_fails
        self.page_missing = page_missing
        self.unreachable = unreachable
        self.malformed = malformed
        self.recheck_fails = recheck_fails
        self.scopes = scopes
        self.granular = granular
        self.in_ten_days = _unix(10)
        self.data_access = _unix(data_access_days)

    def _grants(self, answer):
        if self.scopes is not None:
            answer["scopes"] = list(self.scopes)
        if self.granular is not None:
            answer["granular_scopes"] = self.granular
        return answer

    def paths(self):
        return [c[1] for c in self.calls]

    def __call__(self, path, params, method="POST"):
        self.calls.append((method, path, dict(params)))
        if self.unreachable:
            return False, "could not reach Meta (timed out)"
        if path == "debug_token":
            token = params.get("input_token")
            if self.malformed:
                return True, {"data": {}}
            if self.recheck_fails and token == PAGE_TOKEN:
                return False, "could not reach Meta (timed out)"
            if token == DEAD_TOKEN:
                return True, {"data": {
                    "is_valid": False, "type": "PAGE",
                    "error": {"message": "The session has been invalidated "
                                         "because the user changed their password."}}}
            if token in (PAGE_TOKEN, OTHER_PAGE_TOKEN):
                return True, {"data": self._grants({
                    "is_valid": True, "type": "PAGE", "expires_at": 0,
                    "data_access_expires_at": self.data_access})}
            return True, {"data": self._grants({
                "is_valid": True, "type": "USER", "expires_at": self.in_ten_days,
                "data_access_expires_at": self.data_access})}
        if path == "oauth/access_token":
            if self.exchange_fails:
                return False, "Error validating client secret."
            return True, {"access_token": "EAAzz-long-lived-user-token",
                          "token_type": "bearer", "expires_in": 5183944}
        if path == "me/accounts":
            # Somebody else's Page FIRST, so taking the first one listed is a
            # mistake these checks can see.
            pages = [{"id": OTHER_PAGE, "access_token": OTHER_PAGE_TOKEN}]
            if not self.page_missing:
                pages.append({"id": PAGE, "access_token": PAGE_TOKEN})
            return True, {"data": pages}
        return False, "unexpected call to %s" % path


@contextmanager
def standing_in(fake):
    real = m.meta_request
    m.meta_request = fake
    try:
        yield fake
    finally:
        m.meta_request = real


def _meta_lines():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return [w for w in m.owner_home_warnings(conn, m.house_today())
                    if "Meta" in w["title"]]
    finally:
        conn.close()


def _call(fn):
    """fn(conn) the way the automation loop runs a job, and committed.

    Inside a request context, because automation_tick opens one around every
    job -- it is what lets a job write the audit trail or build a link. Called
    bare, the swap's audit line raises "outside of request context", which is
    a fault in the test and not in the app.
    """
    conn = db()
    try:
        with m.app.test_request_context("/"):
            result = fn(conn)
        conn.commit()
        return result
    finally:
        conn.close()


def _run_job():
    """What the daily job returned, or what it raised.

    Caught rather than let through, so a control that makes it raise fails a
    check and the suite carries on -- a crash would also hide every check
    after it.
    """
    try:
        return _call(m.run_meta_token_job), None
    except Exception as e:
        return None, e


def _save(client, **form):
    base = {"page_id": PAGE, "ig_user_id": IG, "app_id": APP_ID,
            "app_secret": "", "access_token": ""}
    base.update(form)
    return client.post(CONNECT, data=base, follow_redirects=True)


def run():
    s = Suite("The Instagram token, from Meta")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    door = m.meta_request
    s.check("the door out to Meta is shut in here", door.__name__ == "_blocked")

    s.section("Without the app's id and secret, nothing is asked")
    _set(meta_page_id=PAGE, meta_ig_user_id=IG, meta_access_token=USER_TOKEN)
    with standing_in(FakeGraph()) as fake:
        ok, said = _call(m.check_meta_token)
        job, err = _run_job()
    s.check("it says which two are missing",
            not ok and "app ID" in said and "app secret" in said, detail=said)
    s.check("and the daily job sends nothing",
            err is None and job == "not connected" and not fake.calls,
            detail=f"{job!r}, {err!r}, {len(fake.calls)} call(s)")
    lines = _meta_lines()
    s.check("the owner home says the app could ask, given them",
            any("app ID and secret" in w["detail"] for w in lines),
            detail=str([w["title"] for w in lines]))

    s.section("Saving the app's id and secret asks Meta there and then")
    with standing_in(FakeGraph()) as fake:
        saved = _save(oc, app_secret=SECRET, expires_on="")
    html = saved.get_data(as_text=True)
    said = " ".join(flashes(saved))
    s.check("Meta is asked, the token extended, the Pages listed, and asked again",
            fake.paths() == ["debug_token", "oauth/access_token", "me/accounts",
                             "debug_token"], detail=str(fake.paths()))
    s.check("every one a GET, which is what Meta documents for these",
            all(c[0] == "GET" for c in fake.calls),
            detail=str([c[0] for c in fake.calls]))
    s.check("the stored token is the house's Page's own, not the first one listed",
            _get("meta_access_token") == PAGE_TOKEN,
            detail=("somebody else's Page" if _get("meta_access_token") == OTHER_PAGE_TOKEN
                    else f"...{_get('meta_access_token')[-6:]}"))
    s.check("Meta reports no expiry for it", _state()["kind"] == "no_expiry",
            detail=_state()["kind"])
    s.check("and the page says so without promising 'never'",
            "reports no expiry" in (said + html).lower()
            and "never expires" not in (said + html).lower(),
            detail="Meta's reference page does not define expires_at 0")
    s.check("the app id is kept, and shown", _get("meta_app_id") == APP_ID
            and APP_ID in html)
    s.check("the secret is kept", _get("meta_app_secret") == SECRET)
    s.check("and never sent back to the browser",
            SECRET not in html and "Qz9w" in html,
            detail="the last four only, as with the token")
    s.check("the Explorer instructions list every permission to tick",
            all(p in html for p in m.META_PERMISSIONS_TO_TICK),
            detail=str([p for p in m.META_PERMISSIONS_TO_TICK if p not in html]))
    conn = db()
    swaps = conn.execute("SELECT target, details FROM audit_log WHERE action = "
                         "'meta_token_swapped_for_page_token'").fetchall()
    trail = conn.execute("SELECT target, details FROM audit_log WHERE action IN "
                         "('meta_token_swapped_for_page_token', "
                         "'social_accounts_connected')").fetchall()
    conn.close()
    words = " ".join((r["target"] or "") + " " + (r["details"] or "") for r in trail)
    s.check("the swap is in the audit trail, naming the Page",
            len(swaps) == 1 and swaps[0]["target"] == PAGE,
            detail=str([dict(r) for r in swaps]))
    s.check("and no token or secret is",
            not any(v in words for v in (SECRET, USER_TOKEN, PAGE_TOKEN)),
            detail="an audit trail holding a live credential is a second place "
                   "it has to be kept safe")
    lines = _meta_lines()
    s.check("and the owner home has nothing to say", not lines,
            detail=f"{[w['title'] for w in lines]} — a panel that can never be "
                   "empty becomes furniture")

    s.section("Pressing the button, with a token that already lasts")
    with standing_in(FakeGraph()) as fake:
        pressed = oc.post(CONNECT + "/check", follow_redirects=True)
    s.check("Meta is asked once and nothing is swapped",
            fake.paths() == ["debug_token"], detail=str(fake.paths()))
    s.check("and says so", "reports no expiry" in " ".join(flashes(pressed)).lower(),
            detail=str(flashes(pressed)))

    s.section("A token pasted from the Explorer is made to last as it is saved")
    # It lasts about an hour. Left for the daily job, it would be dead before
    # anybody asked Meta to make it last. And the date box is not on the page
    # once Meta is answering, so the browser does not send one.
    with standing_in(FakeGraph()) as fake:
        _save(oc, access_token=FRESH_TOKEN)
    s.check("it is swapped for the Page's own in the same save",
            _get("meta_access_token") == PAGE_TOKEN and "me/accounts" in fake.paths(),
            detail=str(fake.paths()))
    s.check("and an empty secret box kept the secret",
            _get("meta_app_secret") == SECRET)

    s.section("A swap that stops halfway changes nothing")
    for label, fake, word in (
            ("the exchange refused", FakeGraph(exchange_fails=True), "extend"),
            ("the Page not among those listed", FakeGraph(page_missing=True), PAGE)):
        _set(meta_access_token=USER_TOKEN)
        with standing_in(fake):
            ok, said = _call(m.make_meta_token_last)
        s.check(f"with {label}, it says which step", not ok and word in said,
                detail=said)
        s.check("and the token is the one that was there",
                _get("meta_access_token") == USER_TOKEN,
                detail=("somebody else's Page" if _get("meta_access_token") == OTHER_PAGE_TOKEN
                        else "writing the long-lived PERSON's token here would be a "
                             "token that lapses in sixty days, put there by the "
                             "thing meant to stop that"))

    # The swap worked and the question after it went unanswered. The token is
    # the Page's now; what Meta said about the USER token must not be shown
    # against it.
    _set(meta_access_token=USER_TOKEN, **{key: "" for key in m.META_VERDICT_KEYS})
    with standing_in(FakeGraph(recheck_fails=True)):
        _call(m.settle_meta_token)
    s.check("a swap whose last question goes unanswered keeps no verdict on "
            "the old token",
            _get("meta_access_token") == PAGE_TOKEN and _state()["kind"] == "unknown",
            detail=f"{_state()['kind']} — the person's ten days, shown against "
                   "the Page's token")

    s.section("The expiry is Meta's, not typed")
    _set(meta_access_token=USER_TOKEN,
         meta_token_expires_at=(m.house_today() + timedelta(days=200)).isoformat(),
         **{key: "" for key in m.META_VERDICT_KEYS})
    with standing_in(FakeGraph(exchange_fails=True)) as fake:
        _call(m.settle_meta_token)
    want = _house_day(fake.in_ten_days)
    s.check("the date is the one Meta gave, on the house's calendar",
            _get("meta_token_expiry_from_meta") == want,
            detail=f"{_get('meta_token_expiry_from_meta')} against {want}")
    s.check("and the typed one has gone, being about the same token",
            _get("meta_token_expires_at") == "",
            detail="two dates for one token is one more than anybody can act on")
    lines = _meta_lines()
    s.check("ten days out, the owner home warns",
            any("about to run out" in w["title"] for w in lines),
            detail=str([w["title"] for w in lines]))
    s.check("and the line goes to the page that puts it right",
            lines and all(w["href"].endswith(CONNECT) for w in lines),
            detail=str([w["href"] for w in lines]))
    page = oc.get(CONNECT).get_data(as_text=True)
    s.check("the page has no date box to type into now",
            'name="expires_on"' not in page, detail="Meta is answering for it")

    s.section("When Meta does not answer, the job says it failed")
    with standing_in(FakeGraph(unreachable=True)):
        job, err = _run_job()
    s.check("it raises rather than recording a success",
            isinstance(err, m.JobFailed) and "could not be asked" in str(err),
            detail=f"returned {job!r}, raised {err!r}")
    s.check("and Meta's last real answer stands",
            _get("meta_token_expiry_from_meta") == want
            and bool(_get("meta_token_checked_at")),
            detail="a call that never arrived is not an answer")
    with standing_in(FakeGraph(malformed=True)):
        ok, said = _call(m.check_meta_token)
    s.check("an answer that does not say whether it works is not taken as dead",
            not ok and not _get("meta_token_problem")
            and _get("meta_token_expiry_from_meta") == want,
            detail=f"{said!r}; problem {_get('meta_token_problem')!r}")
    with standing_in(FakeGraph(unreachable=True)):
        oc.post("/admin/automation/run/meta_token", follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT last_status, last_message FROM automation_runs "
                       "WHERE job_name = 'meta_token'").fetchone()
    conn.close()
    s.check("run by hand, it is recorded as failed, in Meta's words",
            row is not None and row["last_status"] == "failed"
            and "could not reach Meta" in (row["last_message"] or ""),
            detail=str(dict(row) if row else None))

    s.section("A new token forgets what Meta said about the old one")
    with standing_in(FakeGraph(unreachable=True)):
        pasted = _save(oc, access_token=FRESH_TOKEN)
    st = _state()
    s.check("Meta's date goes with it", not _get("meta_token_expiry_from_meta")
            and st["kind"] == "unknown",
            detail=f"{st['kind']}, {_get('meta_token_expiry_from_meta')!r} — a "
                   "date for a token that is no longer here")
    s.check("and the save says Meta could not be asked, rather than nothing",
            "could not be asked" in " ".join(flashes(pasted)),
            detail=str(flashes(pasted)))
    s.check("with the date box back, since Meta is not answering for it",
            'name="expires_on"' in pasted.get_data(as_text=True))

    s.section("A box that is not sent is left alone")
    # A tab opened before this page had the app ID on it posts without it.
    _set(meta_token_expires_at="2027-05-01")
    oc.post(CONNECT, data={"page_id": PAGE, "ig_user_id": IG},
            follow_redirects=True)
    s.check("the app ID is still there", _get("meta_app_id") == APP_ID,
            detail="a box that is not on the page is not a request to empty it")
    s.check("and so is the date", _get("meta_token_expires_at") == "2027-05-01")
    _set(meta_token_expires_at="")

    s.section("A token Meta says is dead")
    _set(meta_access_token=DEAD_TOKEN)
    with standing_in(FakeGraph()):
        job, err = _run_job()
    s.check("is recorded as dead, in Meta's words",
            "invalidated" in _get("meta_token_problem"),
            detail=repr(_get("meta_token_problem")))
    s.check("which is an answer, so the job does not call itself failed",
            err is None and "no longer works" in (job or ""),
            detail=f"returned {job!r}, raised {err!r}")
    lines = _meta_lines()
    s.check("and it is a blocker on the owner home, whatever a date says",
            len(lines) == 1 and lines[0]["severity"] == "blocker"
            and "no longer works" in lines[0]["title"],
            detail=str([(w["severity"], w["title"]) for w in lines]))
    page = oc.get(CONNECT).get_data(as_text=True)
    s.check("the connect page says so in Meta's words",
            "no longer works" in page and "invalidated" in page)
    post = {"id": 0, "status": "scheduled", "approved_at": "2026-09-01T09:00:00+00:00",
            "image_filename": "never-read.jpg", "caption": "words",
            "platform": "Instagram"}
    with standing_in(FakeGraph()) as fake:
        ok, said = _call(lambda conn: m.publish_social_post(conn, post))
    s.check("and publishing does not even try it",
            not ok and "no longer works" in said and not fake.calls,
            detail=f"{said!r}, {len(fake.calls)} call(s)")
    with standing_in(FakeGraph()):
        _save(oc, access_token=FRESH_TOKEN)
    s.check("a new token clears the blocker by itself", not _meta_lines(),
            detail=str([w["title"] for w in _meta_lines()]))

    s.section("Instagram's own clock")
    for days, severity, title in ((5, "watch", "about to end"),
                                  (-2, "blocker", "has ended")):
        with standing_in(FakeGraph(data_access_days=days)):
            _call(m.check_meta_token)
        lines = _meta_lines()
        s.check(f"data access {abs(days)} days {'away' if days > 0 else 'gone'} "
                f"is a {severity}",
                len(lines) == 1 and lines[0]["severity"] == severity
                and title in lines[0]["title"],
                detail=str([(w["severity"], w["title"]) for w in lines]))
    _set(meta_ig_user_id="")
    s.check("and without Instagram connected, nothing: the Page keeps its "
            "permissions", not _meta_lines(),
            detail=str([w["title"] for w in _meta_lines()]))
    _set(meta_ig_user_id=IG)

    s.section("What the token is allowed to do")
    # Asked about a Page token with everything this house's two accounts need.
    _set(meta_access_token=PAGE_TOKEN, meta_ig_user_id=IG)
    with standing_in(FakeGraph()):
        _call(m.check_meta_token)
    s.check("a token with every permission ticked has nothing said against it",
            not _get("meta_token_missing") and not _meta_lines(),
            detail=repr(_get("meta_token_missing")))
    lacking = [p for p in FULL_SCOPES if p != "instagram_content_publish"]
    with standing_in(FakeGraph(scopes=lacking)):
        ok, said = _call(m.check_meta_token)
    s.check("one that was made without instagram_content_publish is named for it",
            "instagram_content_publish" in _get("meta_token_missing")
            and "instagram_content_publish" in said, detail=said)
    lines = _meta_lines()
    s.check("on the owner home, as something to put right",
            any("cannot do everything" in w["title"] and w["severity"] == "watch"
                for w in lines), detail=str([(w["severity"], w["title"]) for w in lines]))
    s.check("and on the connect page",
            "instagram_content_publish" in oc.get(CONNECT).get_data(as_text=True))
    with standing_in(FakeGraph(unreachable=True)):
        _save(oc, access_token=FRESH_TOKEN)
    s.check("a new token forgets what Meta said the old one lacked",
            not _get("meta_token_missing"),
            detail="it was said about a token that is no longer here")
    with standing_in(FakeGraph(granular=[
            {"scope": "pages_manage_posts", "target_ids": ["999000999"]},
            {"scope": "instagram_content_publish", "target_ids": [IG]}])):
        _call(m.check_meta_token)
    s.check("a permission granted for somebody else's Page is caught too",
            "pages_manage_posts is for another account" in _get("meta_token_missing")
            and "instagram_content_publish is for another" not in _get("meta_token_missing"),
            detail=repr(_get("meta_token_missing")))
    with standing_in(FakeGraph(scopes=None)):
        _call(m.check_meta_token)
    s.check("and when Meta does not say what was granted, nothing is said",
            not _get("meta_token_missing"),
            detail="a warning built on an answer that was never given is believed")

    s.section("The daily job")
    names = [row[0] for row in m.AUTOMATION_JOBS]
    s.check("is registered", "meta_token" in names)
    s.check("and shown by name where jobs are shown",
            "meta_token" in m.AUTOMATION_JOB_LABELS)
    conn = db()
    s.check("and on unless somebody turns it off",
            m.get_automation_settings(conn).get("automation_meta_token_enabled") == "1")
    conn.close()
    with standing_in(FakeGraph()) as fake:
        refused = ec.post(CONNECT + "/check")
    s.check("an employee cannot press the button, and Meta is not asked",
            refused.status_code != 200 and not fake.calls,
            detail=f"HTTP {refused.status_code}, {len(fake.calls)} call(s)")
    s.check("and the stand-in is put back",
            m.meta_request is door and m.meta_request.__name__ == "_blocked",
            detail="left in place, a later suite could reach Meta")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
