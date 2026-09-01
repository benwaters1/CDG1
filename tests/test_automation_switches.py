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

    s.section("The hand-written list matches what the page actually has")
    # The constant exists so the generated block knows what to skip. When
    # it drifts from the template, a job either appears twice or not at
    # all — and typing it from the ROUTE's dict rather than the template is
    # exactly how two jobs came to have no switch.
    on_page = set(re.findall(r'name="(automation_[a-z_]+_enabled)"', body))
    claimed = set(m.AUTOMATION_EXPLAINED_ON_PAGE)
    s.check("nothing is claimed as hand-written that is not there",
            not (claimed - on_page),
            detail=str(sorted(claimed - on_page)))

    s.section("Saving does not switch anything off by accident")
    # The live bug. Posting the form with a job's box ticked must leave it
    # on; posting with a box that does not exist must not read as "off".
    conn = db()
    before = {k: m.get_automation_settings(conn)[k] for k in sorted(registry)}
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
    for key, value in before.items():
        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value))
    conn.commit()
    restored = m.get_automation_settings(conn)
    conn.close()
    s.check("and the suite puts them back as it found them",
            all(restored[k] == before[k] for k in before),
            detail="a suite that leaves every automation switched off is a "
                   "suite that quietly disables the house")

    s.section("An employee cannot change what runs")
    r = ec.post("/admin/automation/settings", data={}, follow_redirects=False)
    s.check("the form is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    return s


if __name__ == "__main__":
    print(run().report())
