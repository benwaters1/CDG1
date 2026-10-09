"""Ten of eighteen scheduled jobs had no off switch anywhere.

update_automation_settings was a hand-typed dict of toggles, and
AUTOMATION_JOBS grew past it. Every job registered since somebody last
edited that dict ran nightly with nothing on any page able to stop it —
including two that message guests, one that charges a card and one that
DELETES dietary and medical notes. If any of them started behaving badly
the owner could not turn it off without editing the database.

Four of them were mine, registered earlier the same day. I did not notice
I was adding an unstoppable job, which is the point: nothing told me.

AND A LIVE BUG UNDERNEATH IT. The route wrote two settings —
automation_hr_escalation_enabled and automation_campaign_triggers_enabled —
as `"1" if request.form.get(key) else "0"`, and neither has ever had a
checkbox on that page. A box that does not exist sends nothing, exactly
like an unticked one, so EVERY SAVE READ THEM AS OFF. Change the backup
interval, press Save, and HR escalation and campaign triggers stop
running. Nothing says so and nothing looks different.

THE SHAPE IS THE SUITES REGISTRY AGAIN: a hand-kept list beside a real
one, agreeing right up until somebody adds to the real one. So the fix is
the same — the form is built FROM the registry — and these checks are what
stop it drifting back.
"""
import re

from _harness import Suite, clients, db

import _harness

m = _harness.m


def run():
    s = Suite("Every job can be switched off")
    oc, ec, _owner, _emp = clients()

    registry = {key for _n, key, _iv, _e, _f in m.AUTOMATION_JOBS if key}
    body = oc.get("/admin/automation").get_data(as_text=True)

    s.section("Every scheduled job has a switch")
    missing = sorted(k for k in registry if f'name="{k}"' not in body)
    s.check(f"all {len(registry)} of them are on the page", not missing,
            detail=("no switch for: " + ", ".join(missing)) if missing else "")

    s.section("And exactly one")
    # Two switches for one job is what the fix could plausibly break: a
    # hand-written toggle AND a generated one, disagreeing with each other
    # depending on which the browser sends last.
    twice = sorted(k for k in registry if body.count(f'name="{k}"') > 1)
    s.check("none is written out twice", not twice,
            detail=("two switches for: " + ", ".join(twice)) if twice else "")

    s.section("The page is drawn from one list, and the list is what is on it")
    # There was a hand-written set of the switches the template wrote out by
    # itself, so the generated block knew what to skip -- and it drifted, as
    # every list beside the real one does. The page is now drawn entirely
    # from AUTOMATION_SWITCHES; test_automation_page holds what each entry
    # says, and this holds that the page shows exactly the list.
    on_page = set(re.findall(r'name="(automation_[a-z_]+_enabled)"', body))
    listed = {e["key"] for e in m.AUTOMATION_SWITCHES}
    s.check("every switch on the list is on the page, and nothing else is",
            on_page == listed,
            detail=f"listed, not shown {sorted(listed - on_page)}; "
                   f"shown, not listed {sorted(on_page - listed)}")

    s.section("Saving does not switch anything off by accident")
    # The live bug. Posting the form with a job's box ticked must leave it
    # on; posting with a box that does not exist must not read as "off".
    #
    # And what is put back afterwards is EVERY switch, not every job. The empty POST below turns off everything
    # the form carries, and five of those are settings with no scheduled job
    # behind them -- waitlist auto-notify, the three balance reminders, the
    # stale-shift alert. (Two of the reminders, room and event, were not on
    # the form at all until 9 October 2026; test_automation_page holds that
    # every switch is.) Restoring only the registry left those five off for
    # the rest of the run, and the check below could not see it because it
    # compared the same list it had restored.
    #
    # The snapshot is the whole settings table, not the switches the suite
    # knows about. The form also carries Write to guests, which is no job's
    # switch; restoring only the automation settings left it off after the
    # empty POST, and twenty-odd suites later the texting suite found every
    # message held for a reason it had never asked about.
    conn = db()
    all_settings = m.get_automation_settings(conn)
    before = {k: all_settings[k] for k in sorted(all_settings)}
    every_setting = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_settings").fetchall()}
    conn.close()

    # Send every switch as ticked, which is what a browser does when the
    # owner has them all on and presses Save.
    oc.post("/admin/automation/settings",
            data={k: "on" for k in registry}, follow_redirects=True)
    conn = db()
    after = m.get_automation_settings(conn)
    conn.close()
    off = sorted(k for k in registry if after[k] != "1")
    s.check("everything ticked stays on", not off,
            detail=("switched off despite being ticked: " + ", ".join(off))
                   if off else "")

    # And the reverse: nothing ticked switches everything off, which is the
    # correct reading of an empty form and proves the checks above are not
    # simply passing because the route ignores the form.
    oc.post("/admin/automation/settings", data={}, follow_redirects=True)
    conn = db()
    after_none = m.get_automation_settings(conn)
    conn.close()
    on = sorted(k for k in registry if after_none[k] != "0")
    s.check("and nothing ticked switches them off", not on,
            detail=("still on with an empty form: " + ", ".join(on)) if on else
                   "which also proves the check above is not passing because "
                   "the route ignores what it was sent")

    # Put them back as they were, so this suite does not leave the house's
    # automation in whatever state its last POST happened to set.
    conn = db()
    for key, value in every_setting.items():
        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value))
    # And anything the POSTs added that was not there before goes again, so
    # a default the code supplies is a default again.
    for (key,) in conn.execute("SELECT key FROM app_settings").fetchall():
        if key not in every_setting:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    conn.commit()
    restored = m.get_automation_settings(conn)
    restored_all = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_settings").fetchall()}
    conn.close()
    left_off = sorted(k for k in before if restored.get(k) != before[k])
    left_off += sorted(k for k in set(every_setting) | set(restored_all)
                       if k not in before and restored_all.get(k) != every_setting.get(k))
    s.check("and the suite puts them back as it found them",
            not left_off,
            detail="left changed: " + ", ".join(left_off) + " — a suite that "
                   "leaves an automation switched off does not fail, it makes "
                   "every suite after it test the switched-off path")
    s.check("and Write to guests is as it was",
            restored_all.get("guest_mail_live") == every_setting.get("guest_mail_live"),
            detail=f"{every_setting.get('guest_mail_live')!r} before, "
                   f"{restored_all.get('guest_mail_live')!r} after -- the empty form "
                   "switches it off, and every suite after this one would test a "
                   "house that is not writing to anybody")
    s.check("including the switches that have no scheduled job behind them",
            all(k in before for k in
                ("automation_waitlist_autonotify_enabled",
                 "automation_stale_shift_enabled",
                 "automation_room_balance_reminder_enabled")),
            detail="these are on the form and not in AUTOMATION_JOBS, which "
                   "is exactly why they were the ones left off")

    s.section("An employee cannot change what runs")
    # Read first, because the status code cannot answer this on its own: a
    # SUCCESSFUL save on this route redirects with 302, so "302 or 403" is
    # true whether the employee was turned away or had just switched the
    # whole house off with an empty form. What settles it is the settings.
    conn = db()
    before_emp = m.get_automation_settings(conn)
    conn.close()
    r = ec.post("/admin/automation/settings", data={}, follow_redirects=False)
    conn = db()
    after_emp = m.get_automation_settings(conn)
    conn.close()
    s.check("the form is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    changed = sorted(k for k in before_emp if after_emp.get(k) != before_emp[k])
    s.check("and nothing they sent took effect", not changed,
            detail="changed by an employee: " + ", ".join(changed) + " — an "
                   "empty form is the most destructive shape this page has, "
                   "and a redirect looks the same either way")

    return s


if __name__ == "__main__":
    print(run().report())
