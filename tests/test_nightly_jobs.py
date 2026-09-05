"""Three nightly jobs whose bodies had never run in a test.

    run_backup_email_job          1 of 24 lines
    run_campaign_triggers_job     1 of 21
    run_stale_shift_cleanup_job   2 of 26

Each is the only thing standing between the house and a specific loss, and
none of them has ever been exercised past its first line.

THE BACKUP is the one that matters most, because its failure is invisible by
construction: if it stops working nothing changes anywhere until the day
somebody needs the backup. It also has a fallback nobody has ever run — when
the full zip is too big to email it sends the database alone, on the reasoning
that "a smaller backup that actually arrives beats a bigger one that a mail
provider silently drops". A fallback that has never executed is a guess.

THE TRIGGERS send mail to guests on a schedule, deduplicated by a key so a
guest gets each message exactly once however often the job runs. Idempotence
that has never been tested by running the job twice is a claim.

THE STALE SHIFT CLEANUP writes hours, and hours are pay. It caps the recorded
clock-out at clock_in + threshold rather than at the moment the job noticed —
its own docstring says why: using the run time "would inflate hours further
the longer it goes unnoticed". Nothing had ever checked which of the two it
used, and the difference is money in somebody's wages.
"""
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room

import _harness

m = _harness.m
TAG = "njob-"


def _cleanup(conn):
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_sends WHERE dedupe_key LIKE ?", ("trigger:%",))
    conn.commit()


def run():
    s = Suite("Three nightly jobs whose bodies had never run")
    _oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    # ================================================== the stale shift
    s.section("A forgotten clock-out is closed at the threshold, not at noticing")
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Forgetful", TAG + "f@example.invalid", now.isoformat()))
    conn.commit()
    who = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "f@example.invalid",)).fetchone()["id"]
    # Clocked in three days ago and never out. The job runs today.
    clocked_in = now - timedelta(days=3)
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
        (who, clocked_in.isoformat()))
    conn.commit()

    result = m.run_stale_shift_cleanup_job(conn, 12)
    entry = conn.execute(
        "SELECT clock_out_at, auto_closed FROM time_entries WHERE user_id = ?",
        (who,)).fetchone()
    s.check("the shift is closed", bool(entry["clock_out_at"]),
            detail=str(dict(entry)))
    s.check("and marked as closed automatically", entry["auto_closed"] == 1,
            detail="a silent database change to somebody's hours is not a "
                   "correction, it is a discrepancy they cannot explain")
    closed = m.parse_datetime_iso(entry["clock_out_at"])
    hours = (closed - clocked_in).total_seconds() / 3600
    s.check("capped at the threshold, not at the moment it was noticed",
            abs(hours - 12) < 0.02,
            detail=f"{hours:.1f} hours recorded — using the run time would "
                   "have written 72, and would write more every day the job "
                   "went unnoticed. Hours are pay.")
    s.check("and the job says how many it closed", "1 stale shift" in result,
            detail=result)

    s.section("And both the person and the owner are told")
    notes = conn.execute(
        "SELECT * FROM notifications WHERE user_id = ? AND kind = 'shift_auto_closed'",
        (who,)).fetchall()
    s.check("the employee is notified", notes, detail=str(len(notes)))
    s.check("with a link to a page that exists",
            notes and notes[0]["link"] == f"/directory/{who}",
            detail=f"{notes[0]['link'] if notes else None} — it used to point "
                   "at /profile, which is not a route, so the notification "
                   "telling somebody to check their hours 404'd")
    owner_row = conn.execute(
        "SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if owner_row:
        told = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? "
            "AND kind = 'shift_auto_closed'", (owner_row["id"],)).fetchone()["c"]
        s.check("and so is the owner", told >= 1, detail=str(told))

    s.section("A shift inside the threshold is left alone")
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
        (who, (now - timedelta(hours=2)).isoformat()))
    conn.commit()
    m.run_stale_shift_cleanup_job(conn, 12)
    still_open = conn.execute(
        "SELECT COUNT(*) AS c FROM time_entries WHERE user_id = ? "
        "AND clock_out_at IS NULL", (who,)).fetchone()["c"]
    s.check("somebody who is still on shift stays on shift", still_open == 1,
            detail=f"{still_open} — closing a shift somebody is in the "
                   "middle of costs them the rest of the day")

    # ================================================== campaign triggers
    s.section("An automated guest email fires once, however often the job runs")
    room = ensure_room()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                   guest_email, arrival_date, departure_date, party_size, status,
                   total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "ARR", TAG + "tok", TAG + " Arriving",
         TAG + "arriving@example.invalid", today.isoformat(),
         (today + timedelta(days=2)).isoformat(), now.isoformat()))
    conn.execute(
        """INSERT INTO campaign_templates (name, subject, body, trigger_active,
                   trigger_event, trigger_offset_days, created_at)
           VALUES (?, 'Welcome', 'See you today.', 1, 'arrival', 0, ?)""",
        (TAG + " on arrival", now.isoformat()))
    conn.commit()

    first = m.run_campaign_triggers_job(conn)
    sent_after_one = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_sends WHERE dedupe_key LIKE 'trigger:%'"
    ).fetchone()["c"]
    second = m.run_campaign_triggers_job(conn)
    sent_after_two = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_sends WHERE dedupe_key LIKE 'trigger:%'"
    ).fetchone()["c"]
    s.check("the first run sends something", sent_after_one >= 1,
            detail=f"{first!r}")
    s.check("and the second sends nothing more",
            sent_after_two == sent_after_one,
            detail=f"{first!r} then {second!r} — the loop ticks every five "
                   "minutes, so a trigger that is not idempotent writes to a "
                   "guest 288 times in a day")

    s.section("Somebody who has opted out is not written to")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                   guest_email, arrival_date, departure_date, party_size, status,
                   total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "OPT", TAG + "tok2", TAG + " Optedout",
         TAG + "optout@example.invalid", (today + timedelta(days=1)).isoformat(),
         (today + timedelta(days=3)).isoformat(), now.isoformat()))
    conn.execute(
        """INSERT INTO campaign_templates (name, subject, body, trigger_active,
                   trigger_event, trigger_offset_days, created_at)
           VALUES (?, 'Tomorrow', 'See you tomorrow.', 1, 'arrival', -1, ?)""",
        (TAG + " day before", now.isoformat()))
    conn.execute("INSERT OR IGNORE INTO email_optouts (email, created_at) "
                 "VALUES (?, ?)", (TAG + "optout@example.invalid", now.isoformat()))
    conn.commit()
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_sends").fetchone()["c"]
    m.run_campaign_triggers_job(conn)
    after = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_sends").fetchone()["c"]
    wrote_to_them = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_sends WHERE recipient_email = ?",
        (TAG + "optout@example.invalid",)).fetchone()["c"]
    s.check("the opted-out guest gets nothing", wrote_to_them == 0,
            detail=f"{wrote_to_them} — an opt-out that only stops the manual "
                   "sends is not an opt-out")
    s.check("and the run is still accounted for", after >= before,
            detail=f"{before} then {after}")

    s.section("With no active trigger it says so rather than doing nothing quietly")
    conn.execute("UPDATE campaign_templates SET trigger_active = 0 "
                 "WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    others = conn.execute(
        "SELECT COUNT(*) AS c FROM campaign_templates "
        "WHERE trigger_active = 1 AND trigger_event IS NOT NULL").fetchone()["c"]
    quiet = m.run_campaign_triggers_job(conn)
    s.check("the message is honest either way",
            ("no active triggers" in quiet) if not others
            else ("email(s) sent" in quiet),
            detail=f"{quiet!r} with {others} other live trigger(s) — the "
                   "automation page shows this line and nothing else")

    # ====================================================== the backup
    s.section("The backup refuses loudly when there is nowhere to send it")
    was_owner_email = m.owner_email
    try:
        m.owner_email = lambda _conn: ""
        failed = None
        try:
            with m.app.test_request_context("/"):
                m.run_backup_email_job(conn)
        except Exception as e:
            failed = e
        s.check("it raises rather than returning quietly",
                isinstance(failed, m.JobFailed),
                detail=f"{type(failed).__name__ if failed else 'nothing'} — a "
                       "backup job that returns 'ok, sent nowhere' is the one "
                       "failure nobody notices until they need the backup")
        s.check("saying what is missing",
                failed and "owner email" in str(failed), detail=str(failed))
    finally:
        m.owner_email = was_owner_email

    s.section("And it drops the photographs before it gives up")
    # The fallback that has never run: too big with media, small enough
    # without. Its own reasoning is that a smaller backup which arrives beats
    # a bigger one a provider silently drops — untested until now.
    calls = []
    was_build = m.build_backup_zip
    was_send = m.send_backup_email
    was_limit = m.BACKUP_EMAIL_MAX_BYTES
    try:
        def fake_build(include_media=True, skipped_out=None):
            calls.append(include_media)
            return b"x" * (5000 if include_media else 100)

        sent = {}

        def fake_send(to, data, filename, note):
            sent.update({"to": to, "size": len(data), "note": note,
                         "filename": filename})
            return True, None

        m.build_backup_zip = fake_build
        m.send_backup_email = fake_send
        m.BACKUP_EMAIL_MAX_BYTES = 1000
        # The real loop wraps every job in a request context, because
        # log_audit reads the session. A job called outside one throws — a
        # coupling worth knowing about, and the reason this is here.
        with m.app.test_request_context("/"):
            message = m.run_backup_email_job(conn)
    finally:
        m.build_backup_zip = was_build
        m.send_backup_email = was_send
        m.BACKUP_EMAIL_MAX_BYTES = was_limit

    s.check("it tries the whole thing first", calls and calls[0] is True,
            detail=str(calls))
    s.check("then builds one without the photographs",
            calls == [True, False], detail=str(calls))
    s.check("and sends the smaller one", sent.get("size") == 100,
            detail=str(sent))
    s.check("saying the media were left out", bool(sent.get("note")),
            detail=f"{sent.get('note')!r} — a backup that silently lacks the "
                   "room photographs is one somebody restores from and then "
                   "discovers what is missing")
    s.check("and the job's own message says so too",
            "database only" in message, detail=message)

    s.section("If even the database alone is too big, it refuses")
    was_build = m.build_backup_zip
    was_limit = m.BACKUP_EMAIL_MAX_BYTES
    try:
        m.build_backup_zip = lambda include_media=True, skipped_out=None: b"x" * 9000
        m.BACKUP_EMAIL_MAX_BYTES = 1000
        blew_up = None
        try:
            with m.app.test_request_context("/"):
                m.run_backup_email_job(conn)
        except Exception as e:
            blew_up = e
        s.check("it raises rather than sending something truncated",
                isinstance(blew_up, m.JobFailed), detail=str(blew_up))
        s.check("and says where to get one by hand",
                blew_up and "Backup" in str(blew_up),
                detail=f"{blew_up} — 'it failed' with no next step is a "
                       "message somebody reads once and stops reading")
    finally:
        m.build_backup_zip = was_build
        m.BACKUP_EMAIL_MAX_BYTES = was_limit

    s.section("A successful send is written down where the panel can see it")
    was_build = m.build_backup_zip
    was_send = m.send_backup_email
    try:
        m.build_backup_zip = lambda include_media=True, skipped_out=None: b"x" * 50
        m.send_backup_email = lambda *a, **k: (True, None)
        with m.app.test_request_context("/"):
            m.run_backup_email_job(conn)
        conn.commit()
    finally:
        m.build_backup_zip = was_build
        m.send_backup_email = was_send
    logged = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'backup_auto_sent'"
    ).fetchone()["c"]
    s.check("the audit line is written", logged >= 1, detail=str(logged))
    import inspect as _inspect
    # Against the behaviour, not the source. The panel asks
    # backup_age_hours, which reads the audit log rather than a file on
    # disk — "what counts is that a copy went somewhere else, not that one
    # was written to the volume that would be lost along with it".
    s.check("and the panel can now see a backup arrived",
            m.backup_age_hours(conn) is not None
            and m.backup_age_hours(conn) < 1,
            detail=f"{m.backup_age_hours(conn)} hours — the morning panel "
                   "calls a missing backup a blocker, and this row is the "
                   "only thing that clears it")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
