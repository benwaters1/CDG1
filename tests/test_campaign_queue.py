"""A campaign to a list of 25,000, sent a little at a time.

Send used to write every letter inside the request that pressed it. The
Squarespace newsletter import puts about 25,000 people on the list; a send to
it would have run for hours inside one request, been ended by Railway's
two-minute limit wherever it had got to, and met Microsoft 365's ceiling of
about 10,000 recipients a day long before that -- taking the day's booking
confirmations down with it.

Now Send writes the letter and the list down and is done; the automation
thread sends at a pace under the day's limit, picks up where it was after a
restart, and never writes to anybody twice. What this file checks is each of
those claims, against the code rather than the words:

  * A 25,000-person send queues in one short request and sends nothing in it.
  * The pace: never more than the day's limit in any 24 hours, nor a
    twenty-fourth of it in any hour, counted from what went -- and a list
    drains over simulated days, everybody once.
  * A restart in the middle of a letter: the letter that may have gone is
    reported and NOT sent again; one that never started goes.
  * Everything the old loop did per letter: held back for somebody who asked
    us to stop, the dedupe key, the unsubscribe key written before the send,
    kept and refused told apart -- and now also an unsubscribe that arrives
    while the send is under way.
  * Write to guests off, wording not approved, no provider: the send waits
    as one line, and NOTHING is put in Held Email.
  * Pause, carry on and stop, and the send's own page saying where it is.

The negative controls are inline where they can be (the pace check is shown
a run over the cap and has to say so), and were run by hand against the code
for the rest: see the commit.
"""
import re
import time
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZCQ"
DOMAIN = "zzcq.example.invalid"


def _addr(i, prefix="p"):
    return f"{prefix}{i}@{DOMAIN}"


def _cleanup(conn):
    sends = [r[0] for r in conn.execute(
        "SELECT send_id FROM campaign_runs WHERE template_name LIKE ?", (TAG + "%",))]
    for sid in sends:
        conn.execute("DELETE FROM campaign_queue WHERE send_id = ?", (sid,))
    conn.execute("DELETE FROM campaign_runs WHERE template_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_sends WHERE template_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", ("%@" + DOMAIN,))
    conn.execute("UPDATE guests SET restricted_at = NULL WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM app_settings WHERE key IN ("
                 "'automation_campaign_daily_limit_mailbox', "
                 "'automation_campaign_daily_limit_resend', "
                 "'automation_campaign_queue_enabled')")
    conn.commit()


def _template(conn, name, subject="A word from Gudanes", body="Dear {{name}}, hello."):
    conn.execute("INSERT INTO campaign_templates (name, subject, body, area, created_at) "
                 "VALUES (?, ?, ?, 'general', ?)",
                 (f"{TAG} {name}", subject, body, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT * FROM campaign_templates WHERE name = ?",
                        (f"{TAG} {name}",)).fetchone()


def _set(conn, key, value):
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()


def _queue_states(conn, send_id):
    return {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM campaign_queue WHERE send_id = ? GROUP BY state",
        (send_id,))}


def _age(conn, send_id, delta):
    """Move a send's letters back in time, as if `delta` had passed since."""
    for table, col in (("campaign_sends", "created_at"), ("campaign_queue", "claimed_at"),
                       ("campaign_queue", "done_at")):
        rows = conn.execute(f"SELECT rowid, {col} FROM {table} WHERE send_id = ? "
                            f"AND {col} IS NOT NULL", (send_id,)).fetchall()
        conn.executemany(
            f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
            [((datetime.fromisoformat(r[1]) - delta).isoformat(), r[0]) for r in rows])
    conn.commit()


def _windows(conn, send_id, start, end, step=timedelta(hours=1)):
    """The busiest hour and busiest 24 hours of a send's letters, as they
    were stamped -- the figures the pace promises to keep under."""
    stamps = sorted(datetime.fromisoformat(r[0]) for r in conn.execute(
        "SELECT created_at FROM campaign_sends WHERE send_id = ? AND status = 'sent'",
        (send_id,)))
    hour = day = 0
    t = start
    while t <= end:
        hour = max(hour, sum(1 for x in stamps if t - timedelta(hours=1) < x <= t))
        day = max(day, sum(1 for x in stamps if t - timedelta(hours=24) < x <= t))
        t += step
    return hour, day


class Killed(BaseException):
    """The process dying in the middle of a letter -- not an error the send
    can catch and record, which is what makes it a restart."""


def run():
    s = Suite("A campaign to a list of 25,000, a little at a time")
    oc, ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    real = {name: getattr(m, name) for name in (
        "send_email", "email_enabled", "resend_enabled", "guest_mail_live",
        "guest_letter_held_why", "campaign_audience", "MAIL_REDIRECT_TO")}
    sent = []                 # (to, subject, body, token row at the moment of sending)

    def capture(to_address, subject, body, *a, **kw):
        token = re.search(r"/unsubscribe/([A-Za-z0-9_-]+)", body or "")
        row = conn.execute(
            "SELECT outcome FROM campaign_sends WHERE unsubscribe_token = ?",
            (token.group(1) if token else "",)).fetchone() if token else None
        sent.append((to_address, subject, body, row["outcome"] if row else None))
        return True

    # The pace counts every campaign letter of the last day, and the suites
    # before this one sent some. Moved out of the window while this runs and
    # put back after, so the figures here are this suite's own.
    recent = [tuple(r) for r in conn.execute(
        "SELECT id, created_at FROM campaign_sends WHERE created_at >= ?",
        ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),))]
    conn.executemany("UPDATE campaign_sends SET created_at = ? WHERE id = ?",
                     [((datetime.fromisoformat(at) - timedelta(days=3)).isoformat(), i)
                      for i, at in recent])
    conn.commit()

    def mail_can_go():
        m.send_email = capture
        m.email_enabled = lambda: True
        m.resend_enabled = lambda: False
        m.guest_mail_live = lambda: True
        m.guest_letter_held_why = lambda key: None
        m.MAIL_REDIRECT_TO = None

    try:
        mail_can_go()

        # ============================================================ queueing
        s.section("25,000 people queue in one short request, and nobody is written to in it")
        big = _template(conn, "Newsletter")
        audience = {_addr(i): f"Reader {i}" for i in range(25000)}
        m.campaign_audience = lambda *a, **k: dict(audience)
        began = time.monotonic()
        r = oc.post(f"/admin/emails/{big['id']}/send",
                    data={"segments": ["newsletter"], "confirm_count": "25000"})
        took = time.monotonic() - began
        print(f"       (Send queued 25,000 in {took:.1f}s)")
        m.campaign_audience = real["campaign_audience"]
        run_row = conn.execute("SELECT * FROM campaign_runs WHERE template_id = ?",
                               (big["id"],)).fetchone()
        big_id = run_row["send_id"] if run_row else ""
        s.check("Send answers well inside Railway's two minutes", took < 30,
                detail=f"{took:.1f}s for 25,000")
        s.check("and sends nothing itself", not sent, detail=f"{len(sent)} sent in the request")
        s.check("everybody is written down to go",
                _queue_states(conn, big_id) == {"queued": 25000},
                detail=str(_queue_states(conn, big_id)))
        s.check("with the letter as it is now", run_row and
                run_row["subject"] == big["subject"] and run_row["total"] == 25000,
                detail=str(run_row and dict(run_row)))
        s.check("each with an unsubscribe key of their own", conn.execute(
            "SELECT COUNT(DISTINCT unsubscribe_token) FROM campaign_queue WHERE send_id = ?",
            (big_id,)).fetchone()[0] == 25000)
        s.check("and the owner is taken to the send itself",
                r.status_code == 302 and f"/admin/emails/sends/{big_id}" in r.headers["Location"],
                detail=f"HTTP {r.status_code} {r.headers.get('Location')}")
        page = oc.get(f"/admin/emails/sends/{big_id}")
        said = " ".join(flashes(page))
        s.check("which says how many and when the last should go",
                "Queued for 25,000 people" in said and "at up to 4,000 a day" in said
                and "the last should go about" in said, detail=said)
        html = page.get_data(as_text=True)
        s.check("the page counts what is still to go", "25,000" in html and "Still to go" in html)
        s.check("and lists them a page at a time, not 25,000 rows",
                html.count(f"@{DOMAIN}") < 200, detail=f"{html.count('@' + DOMAIN)} addresses drawn")
        progress = m.campaign_progress(conn, big_id)
        days = (progress["finish"] - datetime.now(timezone.utc)).total_seconds() / 86400 \
            if progress and progress["finish"] else None
        s.check("4,000 a day puts the end about six and a quarter days off",
                days is not None and 6.0 < days < 6.5, detail=f"{days}")

        s.section("Pressing Send twice does not write to anybody twice")
        m.campaign_audience = lambda *a, **k: dict(audience)
        r = oc.post(f"/admin/emails/{big['id']}/send",
                    data={"segments": ["newsletter"], "confirm_count": "25000"},
                    follow_redirects=True)
        m.campaign_audience = real["campaign_audience"]
        runs = conn.execute("SELECT COUNT(*) FROM campaign_runs WHERE template_id = ?",
                            (big["id"],)).fetchone()[0]
        s.check("the second press queues nobody", runs == 1, detail=f"{runs} sends")
        s.check("and says why", any("already on a send of this campaign" in f
                                    for f in flashes(r)), r)

        s.section("Stop for good leaves the rest off")
        code = ec.post(f"/admin/emails/sends/{big_id}/stop").status_code
        s.check("an employee cannot stop it", code in (302, 403) and
                _queue_states(conn, big_id) == {"queued": 25000}, detail=f"HTTP {code}")
        r = oc.post(f"/admin/emails/sends/{big_id}/stop", follow_redirects=True)
        s.check("the owner can", _queue_states(conn, big_id) == {"stopped": 25000},
                detail=str(_queue_states(conn, big_id)))
        s.check("and nothing of it goes afterwards",
                not sent and m.drain_campaign_queue(conn, sleep=None) is not None and not sent,
                detail=f"{len(sent)} sent")
        s.check("an unknown control is a 404",
                oc.post(f"/admin/emails/sends/{big_id}/explode").status_code == 404)
        _cleanup(conn)

        # ================================================================ pace
        s.section("The pace: under the day's limit, a twenty-fourth of it an hour")
        # 250 a day is 11 an hour rounded up, and 24 hours of 11 is 264: a
        # limit that does not divide by 24 is the one where the day's cap
        # does work the hourly one cannot.
        _set(conn, "automation_campaign_daily_limit_mailbox", 250)
        paced = _template(conn, "Paced")
        people = {_addr(i, "pace"): None for i in range(300)}
        queued = m.queue_campaign(conn, paced, people, owner["id"])
        pid = queued["send_id"]
        gaps = []
        before = m.campaign_allowance(conn)
        m.drain_campaign_queue(conn, sleep=gaps.append)
        went = len(sent)
        s.check("a run sends what the hour allows and stops",
                went == before["now"] and before["now"] <= 11,
                detail=f"{went} sent; allowance was {before['now']}")
        s.check("through the house mailbox, under its thirty a minute",
                gaps and set(gaps) == {2.4}, detail=str(sorted(set(gaps))))
        m.drain_campaign_queue(conn, sleep=None)
        s.check("a second run in the same hour sends nothing", len(sent) == went,
                detail=f"{len(sent) - went} more")
        # Inline negative control: the window check is shown a run over the
        # cap and must say so -- a check that cannot fail is no check.
        start = datetime.now(timezone.utc)
        hour, day = _windows(conn, pid, start - timedelta(hours=1), start)
        s.check("(the window check reads what went)", hour == went, detail=f"{hour} vs {went}")

        s.section("Over simulated days, everybody once, never over the limit")
        clock = timedelta()
        for _ in range(40):                 # forty hours
            m.drain_campaign_queue(conn, sleep=None)
            _age(conn, pid, timedelta(hours=1))
            clock += timedelta(hours=1)
        now = datetime.now(timezone.utc)
        hour, day = _windows(conn, pid, now - clock, now, step=timedelta(minutes=30))
        counts = {}
        for to, *_rest in sent:
            counts[to] = counts.get(to, 0) + 1
        s.check("no hour carried more than eleven", hour <= 11, detail=f"busiest hour {hour}")
        s.check("no day more than 250, though 24 hours of 11 is 264", day <= 250,
                detail=f"busiest 24 hours {day}")
        s.check("nobody was written to twice", max(counts.values()) == 1,
                detail=str([a for a, n in counts.items() if n > 1][:3]))
        s.check("and the list went at the pace: 300 in about thirty hours",
                len(counts) == 300 and _queue_states(conn, pid) == {"done": 300},
                detail=f"{len(counts)} sent; {_queue_states(conn, pid)}")
        s.check("the send says it is finished", conn.execute(
            "SELECT state FROM campaign_runs WHERE send_id = ?", (pid,)).fetchone()[0] == "done")
        # The negative control for the pace itself: given more than the hour
        # allows, the same check above has to see it.
        over = _template(conn, "Over")
        oid = m.queue_campaign(conn, over, {_addr(i, "over"): None for i in range(30)},
                               owner["id"])["send_id"]
        m.drain_campaign_queue(conn, sleep=None, budget=30)
        after = datetime.now(timezone.utc)
        hour, _day = _windows(conn, oid, after, after)
        s.check("(and the same check catches a run that ignores the pace)", hour > 11,
                detail=f"{hour}")

        s.section("Each way out has its own limit")
        _set(conn, "automation_campaign_daily_limit_resend", 4800)
        m.resend_enabled = lambda: True
        pace = m.campaign_allowance(conn)
        s.check("through Resend, Resend's limit", pace["transport"] == "resend"
                and pace["limit"] == 4800 and pace["hour_limit"] == 200, detail=str(pace))
        rid = m.queue_campaign(conn, _template(conn, "Resend"),
                               {_addr(i, "rs"): None for i in range(3)}, owner["id"])["send_id"]
        gaps.clear()
        m.drain_campaign_queue(conn, sleep=gaps.append)
        s.check("and its own gap between letters", gaps and set(gaps) == {0.6},
                detail=str(sorted(set(gaps))))
        m.resend_enabled = lambda: False
        _cleanup(conn)
        sent.clear()

        # ============================================================ restarts
        s.section("A restart in the middle of a letter")
        _set(conn, "automation_campaign_daily_limit_mailbox", 100000)
        crash = _template(conn, "Crash")
        cid = m.queue_campaign(conn, crash, {_addr(i, "c"): None for i in range(10)},
                               owner["id"])["send_id"]

        def dies_on_fourth(to_address, subject, body, *a, **kw):
            capture(to_address, subject, body)
            if len(sent) == 4:
                raise Killed()          # it went, then the house stopped
            return True
        m.send_email = dies_on_fourth
        try:
            m.drain_campaign_queue(conn, sleep=None)
        except Killed:
            pass
        m.send_email = capture
        caught = [r["recipient_email"] for r in conn.execute(
            "SELECT recipient_email FROM campaign_queue WHERE send_id = ? AND state = 'sending'",
            (cid,))]
        s.check("the letter that was going is left saying so", len(caught) == 1,
                detail=str(caught))
        s.check("the lock is let go when the house stops", m._CAMPAIGN_DRAIN_LOCK.acquire(
            blocking=False) and (m._CAMPAIGN_DRAIN_LOCK.release() or True))
        m.drain_campaign_queue(conn, sleep=None)
        s.check("a run straight away leaves it alone: it may still be going",
                conn.execute("SELECT state FROM campaign_queue WHERE recipient_email = ? "
                             "AND send_id = ?", (caught[0], cid)).fetchone()[0] == "sending"
                if caught else False)
        # A row that was claimed but never got as far as its record -- the
        # house stopped between the two -- has certainly not gone.
        never = _addr(99, "c")
        conn.execute("INSERT INTO campaign_queue (send_id, recipient_email, state, "
                     "unsubscribe_token, claimed_at) VALUES (?, ?, 'sending', 'zzcq-never', ?)",
                     (cid, never, (datetime.now(timezone.utc)
                                   - timedelta(minutes=30)).isoformat()))
        conn.commit()
        _age(conn, cid, timedelta(minutes=30))          # the restart came later
        m.drain_campaign_queue(conn, sleep=None)
        counts = {}
        for to, *_rest in sent:
            counts[to] = counts.get(to, 0) + 1
        s.check("after the restart, nobody is written to twice",
                max(counts.values()) == 1 and caught and counts.get(caught[0]) == 1,
                detail=str(counts))
        s.check("the one that may have gone is said to, and was not sent again",
                conn.execute("SELECT outcome FROM campaign_sends WHERE recipient_email = ? "
                             "AND send_id = ?", (caught[0], cid)).fetchone()[0]
                == "interrupted" if caught else False)
        s.check("the one that never started went", counts.get(never) == 1,
                detail=f"{counts.get(never)}")
        s.check("and everybody else went once", len(counts) == 11, detail=str(len(counts)))
        page = oc.get(f"/admin/emails/sends/{cid}").get_data(as_text=True)
        s.check("the page says which may have gone", "May have gone" in page)

        s.section("One run at a time, and a claim is a claim")
        m._CAMPAIGN_DRAIN_LOCK.acquire()
        try:
            busy = m.drain_campaign_queue(conn, sleep=None)
        finally:
            m._CAMPAIGN_DRAIN_LOCK.release()
        s.check("a run while another is going does nothing", busy == "already sending",
                detail=busy)
        oid = m.queue_campaign(conn, _template(conn, "Claimed"),
                               {_addr(1, "cl"): None, _addr(2, "cl"): None},
                               owner["id"])["send_id"]
        conn.execute("UPDATE campaign_queue SET state = 'sending', claimed_at = ? "
                     "WHERE send_id = ? AND recipient_email = ?",
                     (datetime.now(timezone.utc).isoformat(), oid, _addr(1, "cl")))
        conn.commit()
        sent.clear()
        m.drain_campaign_queue(conn, sleep=None)
        s.check("a letter another runner has just claimed is not sent by this one",
                [t for t, *_r in sent] == [_addr(2, "cl")], detail=str([t for t, *_r in sent]))
        _cleanup(conn)
        sent.clear()

        # ======================================================= letter rules
        s.section("Everything the old loop did for each letter")
        _set(conn, "automation_campaign_daily_limit_mailbox", 100000)
        ruled = _template(conn, "Rules")
        stopped_asking, late_unsub, fine = _addr(1, "r"), _addr(2, "r"), _addr(3, "r")
        late_restrict = _addr(4, "r")
        now_iso = datetime.now(timezone.utc).isoformat()
        for a in (stopped_asking, late_restrict):
            conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                         (f"{TAG} {a}", a, now_iso))
        conn.execute("UPDATE guests SET restricted_at = ? WHERE email = ?",
                     (now_iso, stopped_asking))
        conn.commit()
        rq = m.queue_campaign(conn, ruled, {stopped_asking: "A", late_unsub: "B", fine: "C",
                                            late_restrict: "D"}, owner["id"])
        s.check("somebody who asked us to stop is left off at Send",
                rq["withheld"] == 1 and rq["total"] == 3 and conn.execute(
                    "SELECT outcome FROM campaign_sends WHERE recipient_email = ? "
                    "AND send_id = ?", (stopped_asking, rq["send_id"])).fetchone()[0]
                == "withheld", detail=str(rq))
        message, _cat = m.campaign_queued_message(conn, rq)
        s.check("and Send says so", "held back because they asked us to stop" in message,
                detail=message)
        # While the send is under way: one unsubscribes, one asks us to stop.
        conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'x', ?)",
                     (late_unsub.upper(), now_iso))
        conn.execute("UPDATE guests SET restricted_at = ? WHERE email = ?",
                     (now_iso, late_restrict))
        ruled_edit = "Changed my mind about the subject"
        conn.execute("UPDATE campaign_templates SET subject = ? WHERE id = ?",
                     (ruled_edit, ruled["id"]))
        conn.commit()
        m.drain_campaign_queue(conn, sleep=None)
        to = [t for t, *_r in sent]
        outcomes = {r["recipient_email"]: r["outcome"] for r in conn.execute(
            "SELECT recipient_email, outcome FROM campaign_sends WHERE send_id = ?",
            (rq["send_id"],))}
        s.check("an unsubscribe that arrives mid-send is honoured",
                late_unsub not in to and outcomes.get(late_unsub) == "unsubscribed",
                detail=f"{outcomes.get(late_unsub)}")
        s.check("and so is a request to stop using their details",
                late_restrict not in to and outcomes.get(late_restrict) == "withheld",
                detail=f"{outcomes.get(late_restrict)}")
        s.check("the rest are written to", to == [fine], detail=str(to))
        s.check("with the letter as it was when Send was pressed, not as edited since",
                sent and sent[0][1] == ruled["subject"], detail=sent[0][1] if sent else "")
        s.check("the unsubscribe key's row existed before the letter went",
                sent and sent[0][3] == "sending", detail=str(sent[0][3] if sent else None))
        token = re.search(r"/unsubscribe/([A-Za-z0-9_-]+)", sent[0][2]).group(1) if sent else ""
        s.check("and the link in the letter opens the unsubscribe page",
                _harness.m.app.test_client().get(f"/unsubscribe/{token}").status_code == 200)

        s.section("The dedupe key")
        once = _template(conn, "Once")
        sent.clear()
        first = m.queue_campaign(conn, once, {_addr(1, "d"): None}, owner["id"],
                                 dedupe_key="zzcq:once")
        m.drain_campaign_queue(conn, sleep=None)
        again = m.queue_campaign(conn, once, {_addr(1, "d"): None}, owner["id"],
                                 dedupe_key="zzcq:once")
        m.drain_campaign_queue(conn, sleep=None)
        s.check("a letter with a key that has gone does not go again",
                len(sent) == 1 and conn.execute(
                    "SELECT outcome FROM campaign_queue WHERE send_id = ?",
                    (again["send_id"],)).fetchone()[0] == "skipped",
                detail=f"{len(sent)} sent")
        page = oc.get(f"/admin/emails/sends/{again['send_id']}").get_data(as_text=True)
        s.check("and its page says it was sent before", "Already sent before" in page)
        s.check("(the first went)", first["send_id"] and conn.execute(
            "SELECT status FROM campaign_sends WHERE send_id = ?",
            (first["send_id"],)).fetchone()[0] == "sent")

        s.section("Kept, refused and failed are told apart")
        sent.clear()
        kept_id = m.queue_campaign(conn, _template(conn, "Kept"),
                                   {_addr(i, "k"): None for i in range(6)},
                                   owner["id"])["send_id"]

        def refuses(to_address, subject, body, *a, report=None, **kw):
            capture(to_address, subject, body)
            if report is not None:
                report["why"] = "Resend refused it (429): daily quota reached"
            return False
        m.send_email = refuses
        m.drain_campaign_queue(conn, sleep=None)
        s.check("a refused letter stops the run there, not the whole list later",
                len(sent) == 1, detail=f"{len(sent)} tried")
        m.drain_campaign_queue(conn, sleep=None)
        m.drain_campaign_queue(conn, sleep=None)
        state = conn.execute("SELECT state, problem, refusals FROM campaign_runs "
                             "WHERE send_id = ?", (kept_id,)).fetchone()
        s.check("three refusals running pause the send",
                state["state"] == "paused" and state["refusals"] == 3, detail=str(dict(state)))
        m.drain_campaign_queue(conn, sleep=None)
        s.check("and a paused send is not offered to the provider again", len(sent) == 3,
                detail=f"{len(sent)} tried")
        outcomes = [r[0] for r in conn.execute(
            "SELECT outcome FROM campaign_sends WHERE send_id = ? ORDER BY id", (kept_id,))]
        s.check("each is kept, not failed", outcomes == ["held"] * 3, detail=str(outcomes))
        page = oc.get(f"/admin/emails/sends/{kept_id}").get_data(as_text=True)
        s.check("the page says what the provider said, and that it is paused",
                "daily quota reached" in page and "paused until you carry it on" in page)

        def breaks(to_address, *a, **kw):
            capture(to_address, "", "")
            raise ValueError("header has a newline in it")
        m.send_email = breaks
        oc.post(f"/admin/emails/sends/{kept_id}/resume")
        m.drain_campaign_queue(conn, sleep=None)
        outcomes = [r[0] for r in conn.execute(
            "SELECT outcome FROM campaign_sends WHERE send_id = ? ORDER BY id", (kept_id,))]
        s.check("carried on, a letter that breaks is failed and the rest still go",
                outcomes == ["held"] * 3 + ["failed"] * 3, detail=str(outcomes))
        s.check("and the send finishes", conn.execute(
            "SELECT state FROM campaign_runs WHERE send_id = ?", (kept_id,)).fetchone()[0]
            == "done")
        m.send_email = capture
        _cleanup(conn)
        sent.clear()

        # =============================================================== holds
        s.section("Mail that cannot go yet waits as one send, not 25,000 held letters")
        _set(conn, "automation_campaign_daily_limit_mailbox", 100000)
        held = _template(conn, "Held")
        hid = m.queue_campaign(conn, held, {_addr(i, "h"): None for i in range(5)},
                               owner["id"])["send_id"]
        for label, change, words in (
                ("Write to guests off", ("guest_mail_live", lambda: False),
                 "Write to guests is switched off"),
                ("the wording not approved", ("guest_letter_held_why",
                                              lambda key: "this letter's wording is not approved yet"),
                 "not approved yet"),
                ("no provider", ("email_enabled", lambda: False), "no way of sending email")):
            mail_can_go()
            setattr(m, change[0], change[1])
            m.drain_campaign_queue(conn)
            outbox = conn.execute("SELECT COUNT(*) FROM email_outbox WHERE to_address LIKE ?",
                                  ("%@" + DOMAIN,)).fetchone()[0]
            page = oc.get(f"/admin/emails/sends/{hid}").get_data(as_text=True)
            s.check(f"{label}: nothing goes and nothing is put in Held Email",
                    not sent and not outbox and _queue_states(conn, hid) == {"queued": 5},
                    detail=f"{len(sent)} sent, {outbox} held")
            s.check(f"{label}: the page says what it is waiting for", words in page)
        mail_can_go()
        _set(conn, "automation_campaign_queue_enabled", "0")
        page = oc.get(f"/admin/emails/sends/{hid}").get_data(as_text=True)
        s.check("switched off on the Automation page: the page says so",
                "switched off on the Automation page" in page)
        _set(conn, "automation_campaign_queue_enabled", "1")
        m.drain_campaign_queue(conn, sleep=None)
        s.check("and the moment it can, it goes", len(sent) == 5, detail=f"{len(sent)}")
        _cleanup(conn)
        sent.clear()

        # =========================================================== controls
        s.section("Pause and carry on")
        _set(conn, "automation_campaign_daily_limit_mailbox", 100000)
        ctl = _template(conn, "Control")
        kid = m.queue_campaign(conn, ctl, {_addr(i, "p2"): None for i in range(4)},
                               owner["id"])["send_id"]
        r = oc.post(f"/admin/emails/sends/{kid}/pause", follow_redirects=True)
        m.drain_campaign_queue(conn, sleep=None)
        s.check("paused, nothing goes", not sent and "Paused" in r.get_data(as_text=True),
                detail=f"{len(sent)}")
        s.check("pausing twice says it is paused already",
                any("paused" in f.lower() for f in flashes(
                    oc.post(f"/admin/emails/sends/{kid}/pause", follow_redirects=True))))
        hub = oc.get("/admin/emails").get_data(as_text=True)
        s.check("Guest email lists it under Going out now",
                "Going out now" in hub and f"/admin/emails/sends/{kid}" in hub)
        oc.post(f"/admin/emails/sends/{kid}/resume")
        m.drain_campaign_queue(conn, sleep=None)
        s.check("carried on, it goes", len(sent) == 4, detail=f"{len(sent)}")
        hub = oc.get("/admin/emails").get_data(as_text=True)
        s.check("and once finished it leaves Going out now",
                f"/admin/emails/sends/{kid}" not in hub.split("Recent sends")[0])

        s.section("A promo code's send is queued the same way")
        conn.execute("INSERT INTO promo_codes (code, discount_type, discount_value, created_at) "
                     "VALUES (?, 'percent', 10, ?)", (f"{TAG}TEN", datetime.now(timezone.utc)
                                                      .isoformat()))
        conn.commit()
        code_id = conn.execute("SELECT id FROM promo_codes WHERE code = ?",
                               (f"{TAG}TEN",)).fetchone()["id"]
        real_blast = m.promo_blast_recipients
        m.promo_blast_recipients = lambda *a, **k: {_addr(i, "promo"): f"Guest {i}"
                                                    for i in range(3)}
        sent.clear()
        try:
            r = oc.post(f"/admin/promo-codes/{code_id}/blast/send",
                        data={"segment": "room", "subject": "Ten off",
                              "body": "Hi {guest_name}, use {promo_code}."},
                        follow_redirects=True)
        finally:
            m.promo_blast_recipients = real_blast
        said = " ".join(flashes(r))
        s.check("Send queues it and says when it will have gone",
                "Queued for 3 people" in said and not sent, detail=said)
        m.drain_campaign_queue(conn, sleep=None)
        s.check("and the letters go with the code in them",
                len(sent) == 3 and all(f"{TAG}TEN" in b for _t, _s, b, _o in sent),
                detail=str([b[:40] for _t, _s, b, _o in sent]))
        conn.execute("DELETE FROM promo_codes WHERE code = ?", (f"{TAG}TEN",))
        conn.commit()

        s.section("The automation thread runs it")
        jobs = {name: (key, cooldown, fn) for name, key, _i, cooldown, fn in m.AUTOMATION_JOBS}
        entry = jobs.get("campaign_queue")
        s.check("it is one of the thread's jobs, switched by its own setting",
                entry and entry[0] == "automation_campaign_queue_enabled"
                and entry[2] is m.run_campaign_queue_job, detail=str(entry))
        s.check("run on every pass of the thread, not every other",
                entry and entry[1] < m.AUTOMATION_TICK_SECONDS,
                detail=f"{entry and entry[1]}s against a {m.AUTOMATION_TICK_SECONDS}s pass")
        s.check("on by default", m.AUTOMATION_SETTING_DEFAULTS.get(
            "automation_campaign_queue_enabled") == "1")
        s.check("and the mailbox's default is well under its 10,000 a day",
                int(m.AUTOMATION_SETTING_DEFAULTS["automation_campaign_daily_limit_mailbox"])
                <= 5000)
    finally:
        for name, value in real.items():
            setattr(m, name, value)
        _cleanup(conn)
        conn.executemany("UPDATE campaign_sends SET created_at = ? WHERE id = ?",
                         [(at, i) for i, at in recent])
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
