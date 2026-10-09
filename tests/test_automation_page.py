"""The automation page says who every switch reaches, and the code agrees.

WHAT WENT WRONG. Until 9 October 2026 the page was 29 switches in one flat
list -- half of them a bare label, half with a paragraph -- and then a second
table naming every job again. Nothing said which switches write to a guest,
and two attempts to list them from the labels were both wrong, because the
labels do not say it:

    "Delete dietary and medical notes once the event is over" also lets
    waitlist offers lapse, and offering the room to the next guest waiting
    writes to them by email AND by text.

    "Housekeeping" declines a room request nobody answered in 48 hours, and
    the guest is emailed that it was declined.

    The room and event balance reminders, both of which email guests, had no
    switch on the page at all, and the save route never read them, so they
    could not be turned off.

WHAT THIS PINS.
  - THE LIST IS COMPLETE. Every automation switch the house has is on it once:
    every registered job, every job the scheduler runs by hand, and the
    waitlist behaviour.
  - IT CLAIMS WHAT THE CODE DOES. Each entry names the functions that do its
    work, and this reads them: what they can reach -- an email, a text, a
    guest letter, a notification, a task, a card charge, a post -- has to be
    covered by what the entry says it reaches, and everything the entry says
    has to be something the code can do. A job that starts texting is red
    until its row says so; a row that says "writes to guests" about a job
    that cannot is red too.
  - THE PAGE SHOWS IT. Every switch that reaches a guest is under "Writes to
    guests" and carries the words on its own row.
  - SAVE MEANS EVERY SWITCH, and the numbers with them.
"""
import ast
import re

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m

# The functions a letter to a guest goes through: every template the Email
# Wording page lists is one, and so is the stay's own writer, the atelier's,
# a campaign, a text, and the card-refused note.
GUEST_DOORS = {"render_email_template", "write_about_stay", "send_workshop_email",
               "send_campaign", "send_sms", "send_autocharge_failed_email"}
CHANNELS = {"send_email": "email", "send_backup_email": "email",
            "sms_provider_send": "text", "send_notification": "notification",
            "publish_social_post": "post"}
# What each thing the code can do needs the entry to admit to...
NEEDS = {"guest letter": {"guests"}, "text": {"guests"}, "post": {"public"},
         "charge": {"money"}, "email": {"guests", "you", "team"},
         "notification": {"you", "team"}, "task": {"you", "team"}}
# ...and what each claim needs the code to be able to do.
SUPPORTS = {"guests": {"guest letter", "text"}, "money": {"charge"}, "public": {"post"},
            "you": {"email", "notification", "task"},
            "team": {"email", "notification", "task"}}


def _code():
    with open(m.__file__, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    calls = {}
    for name, fn in funcs.items():
        found = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                named = (f.id if isinstance(f, ast.Name)
                         else f.attr if isinstance(f, ast.Attribute) else None)
                if named:
                    found.add(named)
        calls[name] = found
    return src, funcs, calls


def reach(roots, src, funcs, calls):
    """Everything the named functions can do, following every call they make."""
    seen, stack, found = set(), list(roots), set()
    while stack:
        fn = stack.pop()
        if fn in seen or fn not in funcs:
            continue
        seen.add(fn)
        for c in calls[fn]:
            if c in CHANNELS:
                found.add(CHANNELS[c])
            if c in GUEST_DOORS:
                found.add("guest letter")
        body = ast.get_source_segment(src, funcs[fn]) or ""
        if "stripe.PaymentIntent.create" in body:
            found.add("charge")
        if "INSERT INTO tasks" in body:
            found.add("task")
        stack.extend(c for c in calls[fn] if c in funcs)
    return found


def _row(page, key):
    """One switch's row on the page, as text."""
    at = page.find(f'id="{key}"')
    if at < 0:
        return ""
    end = page.find('<div class="auto-row', at + 10)
    end = page.find("</section>", at) if end < 0 else min(end, page.find("</section>", at))
    return page[at:end]


def run():
    s = Suite("The automation page says who each switch reaches")
    oc, ec, _owner, _emp = clients()
    entries = m.AUTOMATION_SWITCHES
    keys = [e["key"] for e in entries]

    s.section("The list is complete")
    switches = {k for k in m.AUTOMATION_SETTING_DEFAULTS
                if k.startswith("automation_") and k.endswith("_enabled")}
    s.check(f"every one of the house's {len(switches)} switches is on it",
            switches == set(keys),
            detail=f"missing {sorted(switches - set(keys))}, "
                   f"not a switch {sorted(set(keys) - switches)}")
    s.check("and none twice", len(keys) == len(set(keys)),
            detail=str(sorted(k for k in keys if keys.count(k) > 1)))
    registry = {name: (key, fn.__name__) for name, key, _i, _e, fn in m.AUTOMATION_JOBS}
    jobs = {e["job"]: e for e in entries if e["job"]}
    every_job = set(registry) | set(m.PARAMETERISED_JOBS)
    s.check("every job the scheduler can run has its row",
            every_job == set(jobs),
            detail=f"no row {sorted(every_job - set(jobs))}, "
                   f"not a job {sorted(set(jobs) - every_job)}")
    wrong = [name for name, (key, fn) in registry.items()
             if name in jobs and (jobs[name]["key"] != key or fn not in jobs[name]["runs"])]
    s.check("and each row is switched by the job's own setting and reads the job's own code",
            not wrong, detail=", ".join(wrong))
    fields = [f for e in entries for f, _l, kind in e.get("fields", ())
              if f not in m.AUTOMATION_SETTING_DEFAULTS
              or kind not in ("hours", "days", "days_back")]
    s.check("every number on it is a real setting", not fields, detail=str(fields))

    s.section("It claims what the code does")
    src, funcs, calls = _code()
    missing_fn = [f for e in entries for f in e["runs"] if f not in funcs]
    s.check("every function a row names exists", not missing_fn, detail=str(missing_fn))
    under, over = [], []
    for e in entries:
        does = reach(e["runs"], src, funcs, calls)
        said = set(e["reaches"])
        for thing in sorted(does):
            if not (NEEDS[thing] & said):
                under.append(f"{e['label']} can send {thing} and does not say so")
        for claim in sorted(said):
            if not (SUPPORTS[claim] & does):
                over.append(f"{e['label']} says it {m.AUTOMATION_REACH_WORDS[claim].lower()} "
                            f"and its code cannot")
    s.check("no row says less than its code can do", not under, detail="; ".join(under))
    s.check("and no row says more", not over, detail="; ".join(over))
    by_key = {e["key"]: e for e in entries}
    s.check("the privacy clean-up says it writes to guests",
            "guests" in by_key["automation_health_notes_purge_enabled"]["reaches"],
            detail="it offers a lapsed waitlist place to the next guest, by email and text")
    s.check("and so does housekeeping",
            "guests" in by_key["automation_housekeeping_enabled"]["reaches"],
            detail="an unanswered room request is declined and the guest is told")
    misplaced = [e["label"] for e in entries
                 if ("guests" in e["reaches"]) != (e["group"] in ("guests", "money"))
                 or (e["group"] == "records" and e["reaches"])]
    s.check("everything that reaches a guest is grouped with the others that do, and the "
            "last group writes to nobody", not misplaced, detail=", ".join(misplaced))

    s.section("The page shows it")
    page = oc.get("/admin/automation")
    body = page.get_data(as_text=True)
    s.check("the page opens", page.status_code == 200, page)
    counts = {k: body.count(f'name="{k}"') for k in keys}
    s.check("every switch is on the page exactly once",
            all(v == 1 for v in counts.values()),
            detail=str({k: v for k, v in counts.items() if v != 1}))
    titles = [t for _k, t, _a in m.AUTOMATION_GROUPS]
    where = [body.find(f">{t}<") for t in titles]
    s.check("in its groups, in order", all(w > 0 for w in where) and where == sorted(where),
            detail=str(dict(zip(titles, where))))
    unsaid = [e["label"] for e in entries
              if "guests" in e["reaches"] and "Writes to guests" not in _row(body, e["key"])]
    s.check("every switch that reaches a guest says so on its own row", not unsaid,
            detail=", ".join(unsaid))
    said_wrongly = [e["label"] for e in entries
                    if "guests" not in e["reaches"] and "Writes to guests" in _row(body, e["key"])]
    s.check("and no other does", not said_wrongly, detail=", ".join(said_wrongly))
    no_run = [e["label"] for e in entries if e["job"]
              and f'/admin/automation/run/{e["job"]}"' not in _row(body, e["key"])]
    s.check("every job's row can be run from it", not no_run, detail=", ".join(no_run))
    s.check("and the Write to guests switch points at the ones it holds",
            'href="#writes-to-guests"' in body and 'id="writes-to-guests"' in body)

    s.section("Save means every switch, and the numbers with them")
    conn = db()
    before = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'automation_%' "
        "OR key = 'guest_mail_live'").fetchall()}
    conn.close()
    backup_hours = m.get_automation_settings(db())["automation_backup_interval_hours"]
    try:
        form = {k: "on" for k in keys}
        form["guest_mail_live"] = "on" if before.get("guest_mail_live", "1") != "0" else ""
        form["automation_room_balance_reminder_days_before"] = "5"
        form["automation_backup_interval_hours"] = "not a number"
        oc.post("/admin/automation/settings", data=form, follow_redirects=True)
        conn = db()
        after = m.get_automation_settings(conn)
        conn.close()
        off = [k for k in keys if after[k] != "1"]
        s.check("everything ticked is on", not off, detail=", ".join(off))
        s.check("including the room and event balance reminders, which had no switch",
                after["automation_room_balance_reminder_enabled"] == "1"
                and after["automation_event_balance_reminder_enabled"] == "1")
        s.check("a number is saved", after["automation_room_balance_reminder_days_before"] == "5",
                detail=after["automation_room_balance_reminder_days_before"])
        s.check("and something that is not a number keeps what was there",
                after["automation_backup_interval_hours"] == backup_hours,
                detail=f"{after['automation_backup_interval_hours']!r}, was {backup_hours!r}")
        oc.post("/admin/automation/settings", data={"guest_mail_live": form["guest_mail_live"]},
                follow_redirects=True)
        conn = db()
        after = m.get_automation_settings(conn)
        conn.close()
        on = [k for k in keys if after[k] != "0"]
        s.check("and nothing ticked is off -- every one of them", not on, detail=", ".join(on))
        r = oc.post("/admin/automation/run/room_balance_reminder", follow_redirects=True)
        s.check("a job the scheduler runs by hand runs from its row",
                r.status_code == 200 and "Ran now" in " ".join(flashes(r)), r)
    finally:
        conn = db()
        for key, value in before.items():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        marks = ",".join("?" * len(before))
        conn.execute("DELETE FROM app_settings WHERE (key LIKE 'automation_%' "
                     f"OR key = 'guest_mail_live') AND key NOT IN ({marks})", list(before))
        conn.commit()
        restored = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'automation_%' "
            "OR key = 'guest_mail_live'").fetchall()}
        conn.close()
    s.check("and the suite leaves every switch as it found it", restored == before,
            detail=str({k: (before.get(k), restored.get(k)) for k in set(before) | set(restored)
                        if before.get(k) != restored.get(k)}))

    s.section("An employee cannot open it")
    r = ec.get("/admin/automation", follow_redirects=False)
    s.check("the page is the owner's", r.status_code in (302, 403), detail=str(r.status_code))
    return s


if __name__ == "__main__":
    print(run().report())
