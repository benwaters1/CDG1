# -*- coding: utf-8 -*-
"""The live site says so when it is not running what is on main.

From 20 to 22 October 2026 every push to main built on Railway and then failed
at "Pre-deploy > Migrate volumes": a change staged on the Railway canvas was
holding the /data volume. Production sat on 4bcf711 for two days, several
sessions reported their work live because they had pushed it, and nothing in
the app could have said otherwise. /status printed the commit it was running
and nothing compared it with anything.

WHAT THIS PINS.
  - IT ASKS GITHUB, and only through github_request, which the harness stands
    down. Every answer here is a stand-in; nothing reaches the network.
  - NO TOKEN IS NOT A FAILURE, and not a silence either. The repository is
    private, so without GITHUB_READ_TOKEN there is nothing to ask: the job
    says it could not run, and the readiness page says why.
  - AN HOUR BEHIND, IT SAYS SO: on the owner home and as a watch task, with
    how many commits, since when, and where to look -- Railway, CDG1,
    Deployments, and what "Migrate volumes" means.
  - SINCE WHEN IS THE PUSH, NOT THE COMMIT. Work here is committed, the owner
    is asked, and it is pushed when they say. Dated by the commit, every such
    push would read as an hour late the moment it landed.
  - IT CLOSES ITSELF, when the check finds the live site on main's head, and
    the moment a deploy changes what is running: the kept answer is about the
    commit that was running when it was asked.
  - A REFUSAL FROM GITHUB IS KEPT BESIDE THE LAST ANSWER. A GitHub outage
    during a stuck deploy must not make the stuck deploy look fixed.
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from _harness import Suite, clients, db
import _harness

m = _harness.m

RUNNING = "4bcf711" + "a" * 33
MID = "9f0e1d2" + "c" * 33
HEAD = "e252290" + "b" * 33
OLDER = "0123456" + "d" * 33
TOKEN = "test-token-never-sent-anywhere"
ENV = ("RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_BRANCH", "RAILWAY_GIT_REPO_OWNER",
       "RAILWAY_GIT_REPO_NAME", "GITHUB_REPO")
TITLE = "Find out why the live site is behind main"


def _at(hours_ago):
    """A moment as GitHub writes one: UTC, with a Z."""
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _push(before, after, hours_ago, kind="push"):
    return {"activity_type": kind, "before": before, "after": after,
            "ref": "refs/heads/main", "timestamp": _at(hours_ago)}


class FakeGitHub:
    """Stands in for github_request: canned answers, and a note of every ask."""

    def __init__(self, head, compare=None, activity=None, refuse=None):
        self.head, self.compare, self.activity = head, compare, activity
        self.refuse = refuse or {}
        self.calls = []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        for part, answer in self.refuse.items():
            if part in path:
                return answer
        if "/git/ref/heads/" in path:
            return 200, {"ref": "refs/heads/main",
                         "object": {"sha": self.head, "type": "commit"}}
        if "/compare/" in path:
            return 200, self.compare
        if path.endswith("/activity"):
            return 200, self.activity
        return 404, "Not Found"


def _run(fake, fresh=False):
    """The job, once, against `fake`: (what it returned or raised, the kept state).

    `fresh` forgets the last answer first. Kept for the same commit, it carries
    GitHub's "since" forward, which is right in the house and would make every
    scenario here after the first one inherit the first one's date."""
    m.github_request = fake
    conn = db()
    if fresh:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.DEPLOY_CHECK_SETTING,))
        conn.commit()
    try:
        try:
            out = m.run_deploy_check_job(conn)
        except Exception as e:          # the scheduler records these; so do we
            out = e
        return out, m.deploy_check_state(conn)
    finally:
        conn.close()


def _keep(state):
    conn = db()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (m.DEPLOY_CHECK_SETTING, json.dumps(state)))
    conn.commit()
    conn.close()


def _warnings():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return [w for w in m.owner_home_warnings(conn, m.house_today())
                    if w["title"].startswith("The live site is")]
    finally:
        conn.close()


def _findings():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            found, _dropped = m.watch_task_findings(conn, m.house_today())
        return [f for f in found if f[0] == "deploy"]
    finally:
        conn.close()


def _readiness():
    """The line as readiness_checks lists it, so it is on the page, not only in a helper."""
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return next((c for c in m.readiness_checks(conn, include_slow=False)
                         if c["label"] == "The live site is running main"), None)
    finally:
        conn.close()


def _task():
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE origin = ? AND title = ? ORDER BY id DESC LIMIT 1",
            (m.WATCH_TASK_ORIGIN, TITLE)).fetchone()
    finally:
        conn.close()


def _watch_pass():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.generate_watch_tasks(conn)
    finally:
        conn.close()


def _behind(hours_ago, ahead=5, extra=0, status="ahead"):
    """GitHub saying main moved past RUNNING `hours_ago` and is `ahead` commits on."""
    return FakeGitHub(
        HEAD,
        compare={"status": status, "ahead_by": ahead, "behind_by": extra,
                 # The commits' own dates, a day old, to prove they are not read.
                 "commits": [{"sha": MID, "commit": {"committer": {"date": _at(26)}}}]},
        activity=[_push(MID, HEAD, max(0.1, hours_ago - 0.5)),
                  _push(RUNNING, MID, hours_ago),
                  _push(OLDER, RUNNING, hours_ago + 30)])


def run():
    s = Suite("The live site says when it is behind main")
    oc, _ec, _owner, _emp = clients()

    blocked = m.github_request
    real_urlopen = m.urlopen
    was_token = m.GITHUB_READ_TOKEN
    was_env = {k: os.environ.get(k) for k in ENV}
    conn = db()
    kept = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                        (m.DEPLOY_CHECK_SETTING,)).fetchone()
    task_high = conn.execute("SELECT COALESCE(MAX(id), 0) AS c FROM tasks").fetchone()["c"]
    task_rows = [dict(r) for r in conn.execute(
        """SELECT id, status, completed_at, notes, priority, due_date, assigned_to_user_id
             FROM tasks WHERE origin = ?""", (m.WATCH_TASK_ORIGIN,)).fetchall()]
    conn.close()

    try:
        for k in ENV:
            os.environ.pop(k, None)

        s.section("Nothing in the suite reaches GitHub")
        s.check("github_request is stood down by the harness",
                blocked.__name__ == "_blocked",
                detail="a run would ask GitHub from a laptop, on the token's allowance")

        s.section("Not on Railway: nothing to compare, and it says so")
        fake = FakeGitHub(RUNNING)
        out, _state = _run(fake)
        s.check("the job could not run, which is not a failure",
                isinstance(out, m.JobNotSetUp), detail=repr(out))
        s.check("and GitHub was not asked", not fake.calls, detail=str(fake.calls))
        line = _readiness()
        s.check("readiness says there is no deployed commit, as optional",
                line and line["severity"] == "info" and "Railway" in line["detail"],
                detail=str(line))

        s.section("No token: the readiness page says so rather than the job failing")
        os.environ["RAILWAY_GIT_COMMIT_SHA"] = RUNNING
        m.GITHUB_READ_TOKEN = ""
        fake = FakeGitHub(RUNNING)
        out, _state = _run(fake)
        s.check("the job could not run, and says why",
                isinstance(out, m.JobNotSetUp) and "GITHUB_READ_TOKEN" in str(out),
                detail=repr(out))
        s.check("and GitHub was not asked without one", not fake.calls,
                detail=str(fake.calls))
        line = _readiness()
        s.check("readiness names the variable and the repository",
                line and not line["ok"] and line["severity"] == "warn"
                and "GITHUB_READ_TOKEN" in line["detail"]
                and m.HOUSE_GITHUB_REPO in line["detail"]
                and "private" in line["detail"],
                detail=str(line))
        s.check("and where to read how to make one",
                line and "DEPLOY.md" in line["detail"], detail=str(line))
        page = oc.get("/admin/readiness")
        s.check("on the readiness page itself",
                page.status_code == 200 and "GITHUB_READ_TOKEN" in page.get_data(as_text=True),
                detail=f"HTTP {page.status_code}")
        s.check("and not on the home page: a missing setting is the readiness page's",
                not _warnings(), detail=str(_warnings()))

        s.section("Caught up: one small read, and nothing to say")
        m.GITHUB_READ_TOKEN = TOKEN
        fake = FakeGitHub(RUNNING)
        out, state = _run(fake)
        s.check("the job says what is running",
                out == "running 4bcf711, the head of main", detail=repr(out))
        s.check("one read, of where main is, in the house's repository",
                [c[0] for c in fake.calls] == ["repos/benwaters1/CDG1/git/ref/heads/main"],
                detail=str(fake.calls))
        s.check("kept as identical", state.get("status") == "identical"
                and state.get("behind_by") == 0, detail=str(state))
        s.check("nothing on the home page", not _warnings(), detail=str(_warnings()))
        s.check("nothing to raise", not _findings(), detail=str(_findings()))
        line = _readiness()
        s.check("readiness passes, naming the commit",
                line and line["ok"] and "4bcf711" in line["detail"], detail=str(line))

        s.section("Three hours behind: on the home page, as a task, and on readiness")
        fake = _behind(3)
        out, state = _run(fake, fresh=True)
        asked = [c[0] for c in fake.calls]
        s.check("it asked where main is, how far apart, and when main moved",
                asked == ["repos/benwaters1/CDG1/git/ref/heads/main",
                          f"repos/benwaters1/CDG1/compare/{RUNNING}...{HEAD}",
                          "repos/benwaters1/CDG1/activity"],
                detail=str(fake.calls))
        s.check("the pushes to main, not to any branch",
                fake.calls[-1][1].get("ref") == "main", detail=str(fake.calls[-1]))
        s.check("kept as five commits behind",
                state.get("behind_by") == 5 and state.get("head") == HEAD, detail=str(state))
        pushed = m._github_time(fake.activity[1]["timestamp"])
        s.check("since the push that left it behind, as GitHub recorded it",
                state.get("since") == pushed and state.get("since_from") == "push",
                detail=f"{state.get('since')} from {state.get('since_from')}, wanted {pushed}")
        s.check("the job's own line says so",
                isinstance(out, str) and out.startswith("5 commits behind main since"),
                detail=repr(out))
        s.check("the token is nowhere in what is kept",
                TOKEN not in json.dumps(state), detail="the kept answer holds the token")

        warned = _warnings()
        w = warned[0] if warned else {}
        when = m.local_datetime_str(pushed)
        s.check("one line on the home page, with the count in its title",
                len(warned) == 1 and w["title"] == "The live site is 5 commits behind main",
                detail=str(warned))
        s.check("a warning at three hours, not yet a blocker",
                w.get("severity") == "warn", detail=str(w.get("severity")))
        detail = w.get("detail", "")
        for words, why in (
                ("4bcf711", "the commit that is running"),
                ("5 commits", "how many"),
                (when, "since when"),
                ("3 hours ago", "how long"),
                ("Railway → CDG1 → Deployments", "where to look"),
                ("Migrate volumes", "what the failure looked like on 20 October"),
                ("staged on the Railway canvas is holding the volume", "what it means"),
                ("Deploy on the canvas", "what to press")):
            s.check(f"it names {why}", words in detail,
                    detail=f"{words!r} not in: {detail}")
        s.check("and it links to the readiness page",
                w.get("href", "").endswith("/admin/readiness"), detail=str(w.get("href")))

        found = _findings()
        s.check("one watch finding, titled by nothing that moves",
                len(found) == 1 and found[0][1] == TITLE and found[0][4] == "high",
                detail=str(found))
        s.check("worded as the home page words it",
                found and found[0][2] == detail, detail=str(found))
        _watch_pass()
        task = _task()
        s.check("generate_watch_tasks raises it", task and task["status"] != "done"
                and "5 commits" in (task["notes"] or ""),
                detail=str(dict(task)) if task else "no task")
        s.check("on the calendar today",
                task and task["due_date"] == m.house_today().isoformat(),
                detail=str(task["due_date"]) if task else "")
        line = _readiness()
        s.check("readiness fails with the same sentence",
                line and not line["ok"] and line["detail"] == detail, detail=str(line))

        s.section("Pushed twenty minutes ago: Railway is still at it, so nothing yet")
        # The commits in the comparison are dated a day ago. A check that read
        # those would call this a day late; it is twenty minutes old.
        fake = _behind(1 / 3)
        out, state = _run(fake, fresh=True)
        s.check("kept as behind", state.get("behind_by") == 5, detail=str(state))
        s.check("not on the home page", not _warnings(), detail=str(_warnings()))
        s.check("not raised as a task", not _findings(), detail=str(_findings()))
        line = _readiness()
        s.check("readiness passes, saying it is not live yet",
                line and line["ok"] and "not live yet" in line["detail"], detail=str(line))

        s.section("A day behind is a blocker")
        _run(_behind(30), fresh=True)
        w = (_warnings() or [{}])[0]
        s.check("on the home page", w.get("severity") == "blocker", detail=str(w))
        s.check("and in hours", "30 hours ago" in w.get("detail", ""), detail=w.get("detail"))
        line = _readiness()
        s.check("and on the readiness page", line and line["severity"] == "blocker",
                detail=str(line))
        _run(_behind(50), fresh=True)
        w = (_warnings() or [{}])[0]
        s.check("past two days it counts days", "2 days ago" in w.get("detail", ""),
                detail=w.get("detail"))

        s.section("Running a commit main does not have")
        _run(_behind(5, ahead=3, extra=2, status="diverged"), fresh=True)
        w = (_warnings() or [{}])[0]
        s.check("it says so", "also running 2 commits main does not have" in w.get("detail", ""),
                detail=w.get("detail"))
        s.check("and counts what main has that it does not",
                w.get("title") == "The live site is 3 commits behind main", detail=str(w))

        s.section("GitHub refusing: kept beside the last answer, and the job fails")
        _run(_behind(3), fresh=True)
        _watch_pass()
        fake = FakeGitHub(HEAD, refuse={"/git/ref/": (401, "Bad credentials")})
        out, state = _run(fake)
        s.check("the job fails, so a dead token surfaces like any failing job",
                isinstance(out, Exception) and not isinstance(out, m.JobNotSetUp),
                detail=repr(out))
        s.check("in GitHub's words, and what they mean",
                "Bad credentials" in (state.get("error") or "")
                and "HTTP 401" in state["error"] and "expired or been revoked" in state["error"],
                detail=str(state.get("error")))
        s.check("the stuck deploy is still on the home page",
                len(_warnings()) == 1, detail=str(_warnings()))
        s.check("and still a task", _findings() and _findings()[0][1] == TITLE,
                detail=str(_findings()))

        _run(FakeGitHub(RUNNING))
        out, state = _run(FakeGitHub(HEAD, refuse={"/git/ref/": (404, "Not Found")}))
        line = _readiness()
        s.check("with nothing behind, readiness carries the refusal",
                line and not line["ok"] and "could not ask GitHub" in line["detail"]
                and "Contents: read" in line["detail"] and m.HOUSE_GITHUB_REPO in line["detail"],
                detail=str(line))
        s.check("and when GitHub last answered",
                line and "GitHub last answered on" in line["detail"], detail=str(line))
        out, state = _run(FakeGitHub(HEAD, refuse={"/git/ref/": (0, "could not reach GitHub (timed out)")}))
        s.check("no answer at all is a failure too, without an HTTP status",
                isinstance(out, RuntimeError) and "HTTP" not in state["error"]
                and "timed out" in state["error"], detail=repr(out))

        s.section("Catching up closes it")
        _run(_behind(3), fresh=True)
        _watch_pass()
        s.check("open while it is behind", _task() and _task()["status"] != "done",
                detail=str(dict(_task())) if _task() else "no task")
        _run(FakeGitHub(RUNNING))
        r = _watch_pass()
        s.check("the next pass ticks the task off", _task() and _task()["status"] == "done",
                detail=f"{dict(_task()) if _task() else 'no task'}; pass said {r}")
        s.check("and the home page is quiet", not _warnings(), detail=str(_warnings()))

        s.section("A deploy makes the kept answer about another commit")
        _run(_behind(3), fresh=True)
        os.environ["RAILWAY_GIT_COMMIT_SHA"] = HEAD
        s.check("the home page does not repeat it", not _warnings(), detail=str(_warnings()))
        s.check("and no task is raised from it", not _findings(), detail=str(_findings()))
        line = _readiness()
        s.check("readiness says this deploy has not been checked yet",
                line and not line["ok"] and "Not checked since e252290" in line["detail"],
                detail=str(line))
        os.environ["RAILWAY_GIT_COMMIT_SHA"] = RUNNING

        s.section("When GitHub's record does not reach back, the first sighting stands in")
        m.github_request = FakeGitHub(
            HEAD, compare={"status": "ahead", "ahead_by": 4, "behind_by": 0},
            refuse={"/activity": (404, "Not Found")})
        first = datetime.now(timezone.utc) - timedelta(hours=2)
        state = m.check_deploy_against_main(RUNNING, {}, now=first)
        s.check("first seen now, and said to be a sighting",
                state["since"] == first.isoformat() and state["since_from"] == "noticed",
                detail=str(state))
        later = m.check_deploy_against_main(RUNNING, state, now=first + timedelta(hours=2))
        s.check("kept while it stays true",
                later["since"] == first.isoformat() and later["noticed_at"] == first.isoformat(),
                detail=str(later))
        asked = [c[0] for c in m.github_request.calls]
        s.check("and GitHub's record is asked again each time",
                asked.count("repos/benwaters1/CDG1/activity") == 2, detail=str(asked))
        _keep(later)
        w = (_warnings() or [{}])[0]
        s.check("the home page says it is at least that long",
                "when this check first saw it" in w.get("detail", ""), detail=str(w))
        fake = _behind(3)
        m.github_request = fake
        state = m.check_deploy_against_main(RUNNING, {})
        again = m.check_deploy_against_main(RUNNING, state)
        s.check("once GitHub has said when, it is not asked again",
                [c[0] for c in fake.calls].count("repos/benwaters1/CDG1/activity") == 1
                and again["since"] == state["since"], detail=str(fake.calls))
        caught = m.check_deploy_against_main(RUNNING, dict(state, behind_by=0, since=None,
                                                           since_from=None, noticed_at=None))
        s.check("and a fresh lapse asks GitHub afresh",
                caught["since"] == state["since"] and caught["since_from"] == "push"
                and [c[0] for c in fake.calls].count("repos/benwaters1/CDG1/activity") == 2,
                detail=str(caught))

        s.section("Which push left it behind")
        fp = m.first_push_past

        def t(push):
            return m._github_time(push["timestamp"])
        chain = [_push(MID, HEAD, 1), _push(RUNNING, MID, 2), _push(OLDER, RUNNING, 9)]
        s.check("the push that started from it", fp(chain, RUNNING) == t(chain[1]),
                detail=str(fp(chain, RUNNING)))
        forced = [_push(MID, HEAD, 1, "force_push"), _push(OLDER, RUNNING, 9)]
        s.check("after a force push, the one after the push that brought it in",
                fp(forced, RUNNING) == t(forced[0]), detail=str(fp(forced, RUNNING)))
        s.check("none when the newest push brought it in",
                fp([_push(OLDER, RUNNING, 1)], RUNNING) is None)
        s.check("none when the record does not go back that far",
                fp(chain[:1], RUNNING) is None)
        moved = [_push(RUNNING, "0" * 40, 1, "branch_deletion"), _push(RUNNING, MID, 2)]
        s.check("a deleted branch is not a move", fp(moved, RUNNING) == t(moved[1]),
                detail=str(fp(moved, RUNNING)))
        s.check("GitHub's Z is read on any Python",
                m._github_time("2026-10-20T14:05:12Z") == "2026-10-20T14:05:12+00:00",
                detail=str(m._github_time("2026-10-20T14:05:12Z")))

        s.section("The repository and branch Railway names are the ones asked")
        os.environ["RAILWAY_GIT_REPO_OWNER"] = "someone"
        os.environ["RAILWAY_GIT_REPO_NAME"] = "elsewhere"
        os.environ["RAILWAY_GIT_BRANCH"] = "live"
        fake = FakeGitHub(RUNNING)
        _run(fake)
        s.check("Railway's repository and branch",
                fake.calls and fake.calls[0][0] == "repos/someone/elsewhere/git/ref/heads/live",
                detail=str(fake.calls))
        os.environ["GITHUB_REPO"] = "house/own"
        fake = FakeGitHub(RUNNING)
        _run(fake)
        s.check("GITHUB_REPO over both",
                fake.calls and fake.calls[0][0] == "repos/house/own/git/ref/heads/live",
                detail=str(fake.calls))
        for k in ("RAILWAY_GIT_REPO_OWNER", "RAILWAY_GIT_REPO_NAME", "RAILWAY_GIT_BRANCH",
                  "GITHUB_REPO"):
            os.environ.pop(k, None)

        s.section("The request itself")
        sent = []

        class _Answer:
            status = 200

            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _urlopen(answer):
            def opener(req, timeout=None):
                sent.append((req, timeout))
                if isinstance(answer, Exception):
                    raise answer
                return _Answer(answer)
            return opener

        real = _harness.REAL_GITHUB_REQUEST
        m.urlopen = _urlopen(b'{"object": {"sha": "abc"}}')
        status, said = real("repos/x/y/activity", {"ref": "main", "per_page": 100})
        req, timeout = sent[-1]
        s.check("a GET to GitHub's API with the query on it",
                req.get_method() == "GET" and req.full_url ==
                "https://api.github.com/repos/x/y/activity?ref=main&per_page=100",
                detail=f"{req.get_method()} {req.full_url}")
        s.check("carrying the token as a bearer",
                req.get_header("Authorization") == f"Bearer {TOKEN}",
                detail=str(req.header_items()))
        s.check("asking for GitHub's JSON", req.get_header("Accept") ==
                "application/vnd.github+json", detail=str(req.header_items()))
        s.check("with a time limit", timeout and timeout <= 30, detail=str(timeout))
        s.check("and the answer read", status == 200 and said == {"object": {"sha": "abc"}},
                detail=f"{status} {said}")
        m.urlopen = _urlopen(HTTPError("u", 401, "Unauthorized", {},
                                       io.BytesIO(b'{"message": "Bad credentials"}')))
        s.check("a refusal comes back in GitHub's words",
                real("repos/x/y/git/ref/heads/main") == (401, "Bad credentials"))
        m.urlopen = _urlopen(URLError("timed out"))
        status, said = real("repos/x/y/git/ref/heads/main")
        s.check("no answer comes back as a sentence, not an exception",
                status == 0 and said.startswith("could not reach GitHub"),
                detail=f"{status} {said}")
        m.GITHUB_READ_TOKEN = ""
        m.urlopen = _urlopen(b"{}")
        real("repos/x/y/git/ref/heads/main")
        s.check("without a token, no Authorization header",
                sent[-1][0].get_header("Authorization") is None,
                detail=str(sent[-1][0].header_items()))
        m.GITHUB_READ_TOKEN = TOKEN
        m.urlopen = real_urlopen

        s.section("A job the house runs, every quarter of an hour")
        jobs = [j[0] for j in m.AUTOMATION_JOBS]
        entry = next((j for j in m.AUTOMATION_JOBS if j[0] == "deploy_check"), None)
        s.check("registered, every fifteen minutes",
                entry and entry[3] == 900 and entry[4] is m.run_deploy_check_job,
                detail=str(entry))
        s.check("switched on by default", m.AUTOMATION_SETTING_DEFAULTS.get(
            "automation_deploy_check_enabled") == "1")
        s.check("ahead of the photographs, which can take a minute",
                "deploy_check" in jobs and jobs.index("deploy_check") < jobs.index("photo_mirror"),
                detail=str(jobs[-4:]))
        switch = next((e for e in m.AUTOMATION_SWITCHES if e["job"] == "deploy_check"), {})
        s.check("on the automation page, writing to nobody",
                switch.get("group") == "records" and switch.get("reaches") == (),
                detail=str(switch))
        s.check("and its task has somebody it can be routed to",
                "deploy" in m.WATCH_TASK_KINDS, detail=str(sorted(m.WATCH_TASK_KINDS)))
    finally:
        m.github_request = blocked
        m.urlopen = real_urlopen
        m.GITHUB_READ_TOKEN = was_token
        for k, v in was_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        conn = db()
        if kept:
            conn.execute("UPDATE app_settings SET value = ? WHERE key = ?",
                         (kept["value"], m.DEPLOY_CHECK_SETTING))
        else:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (m.DEPLOY_CHECK_SETTING,))
        # generate_watch_tasks rebuilds the whole house's watch list, so what it
        # made and closed for other findings is put back exactly.
        conn.execute("DELETE FROM tasks WHERE id > ?", (task_high,))
        for row in task_rows:
            conn.execute(
                """UPDATE tasks SET status = ?, completed_at = ?, notes = ?, priority = ?,
                     due_date = ?, assigned_to_user_id = ? WHERE id = ?""",
                (row["status"], row["completed_at"], row["notes"], row["priority"],
                 row["due_date"], row["assigned_to_user_id"], row["id"]))
        conn.commit()
        conn.close()

    s.section("And the panel is quiet again")
    s.check("nothing left on the home page", not _warnings(), detail=str(_warnings()))
    return s


if __name__ == "__main__":
    print(run().report())
