"""A stored moment shown with its time, as the house reads it.

The automation page's Job status table printed

    run['last_ran_at'][:19].replace('T', ' ')

and its Last worked line the same. A moment is stored in UTC, so the slice
showed UTC's clock: every run an hour or two early, and one after midnight on
the day before. What changed, on every record, shows its stamps through
local_datetime_str(), which is the house's way, and now both do.

The same cut was in eleven more templates, four ways:

  - past the date, [:16] or [:19]: when an email came in, when a waitlisted
    guest's offer runs out, a text still waiting to go, a guest's messages;
  - to the time alone, [11:16]: staff chat, the assistant, the kitchen's
    shopping list, and the driver's list of transfers -- which dated a pickup
    at half past midnight correctly and timed it 22:30, so the run read as due
    nearly a whole day after the plane was in;
  - dressed with replace('T', ' '), whatever the value is called. Only a raw
    stamp has a T to take out, which is how a guest's message history was
    found under msg.when, a name no search for _at reaches;
  - a slice of local_datetime_str's ANSWER. That is words, "September 29,
    2026 14:20", not a stamp, so [:10] printed "September " for the day a key
    was handed over and the day an incident went to the insurer, and [:16]
    printed "September 29, 20" for when Pennylane was last pulled.

READ FROM THE SOURCE, every template, public ones included, because several of
these only render when there is a value to cut: a check on output alone passes
or fails with the database. A stamp cut to its DAY, [:10], is test_utc_slices'.

Two pages are rendered as well, from rows written for them, so the rule and
the pages are seen to agree: the automation page in each of the three things a
job's time can be -- a time, never, not recorded -- and the driver's list. The
times they are held to are worked out here from the zone, not asked of
local_datetime_str, or a helper that stopped converting would agree with
itself.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZSTAMP"
TEMPLATES = os.path.join(_harness.ROOT, "templates")

# A moment's column ends _at, by this schema's own convention, and a slice of
# one is written three ways: x_at[:16], x['x_at'][:16], (x['x_at'] or '')[:16].
_AT_SLICE = re.compile(
    r"""\b\w*_at\b['"]?\s*\]?\s*(?:\bor\s*(?:''|"")\s*\)\s*)?"""
    r"""(?P<cut>\[\s*(?P<lo>\d*)\s*:\s*(?P<hi>\d*)\s*\])""")
# Character 11 is where an ISO stamp's time begins. Nothing else a page shows
# is cut from there, so this one needs no name to go on.
_TIME_CUT = re.compile(r"\[\s*11\s*:\s*\d*\s*\]")
# The T between a stamp's date and its time. Taking it out is how a raw stamp
# is dressed up to be read, whatever the value is called.
_T_OUT = re.compile(r"""\breplace\(\s*(['"])T\1\s*,""")
# What the date formatters hand back is words, so a slice of it cuts words.
_WORDS_CUT = re.compile(r"\|\s*(?:house_when|date_short|date_human)\s*\)\s*\[")
_CONVERTER = re.compile(r"\blocal_datetime_str\s*\(")

# Three real jobs, one for each thing a job's time can be. Their rows are put
# back as they were at the end.
JOBS = ("weather", "exchange_rates", "page_translation")
RAN = "2026-09-03T23:30:00+00:00"       # half past one on the 4th, at the house
WORKED = "2026-09-01T22:15:00+00:00"    # a quarter past midnight on the 2nd


def _blank_comments(src):
    """{# #} comments as spaces, the same length, so line numbers stay true."""
    return re.sub(r"\{#.*?#\}", lambda c: re.sub(r"[^\n]", " ", c.group(0)),
                  src, flags=re.S)


def _end_of_call(code, i):
    """Just past the ) that closes the ( at i, reading past quoted strings."""
    depth, quote = 0, None
    for j in range(i, len(code)):
        c = code[j]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
    return None


def cut_stamps(src):
    """(line, what) for each line of a template that cuts a stored moment to
    show it."""
    code = _blank_comments(src)
    at = {}
    for mo in _AT_SLICE.finditer(code):
        lo, hi = mo.group("lo"), mo.group("hi")
        if (hi and int(hi) > 10) or (lo and int(lo) >= 11):
            at.setdefault(mo.start("cut"), "a moment cut to show its time, which is UTC's")
    for mo in _TIME_CUT.finditer(code):
        at.setdefault(mo.start(), "a stamp cut to its time, which is UTC's")
    for mo in _T_OUT.finditer(code):
        at.setdefault(mo.start(), "a raw stamp dressed up to be read")
    for mo in _WORDS_CUT.finditer(code):
        at.setdefault(mo.end() - 1, "a formatted date cut mid-word")
    for mo in _CONVERTER.finditer(code):
        end = _end_of_call(code, mo.end() - 1)
        if end is None:
            continue
        rest = code[end:]
        if rest.lstrip().startswith("["):
            at.setdefault(end + len(rest) - len(rest.lstrip()),
                          "local_datetime_str's words, cut mid-word")
    lines = {}
    for pos in sorted(at):
        lines.setdefault(code.count("\n", 0, pos) + 1, at[pos])
    return sorted(lines.items())


def _house_clock(stamp):
    """(HH:MM, day patterns) the house's clock read at a stored moment.

    Worked out from the zone here rather than asked of local_datetime_str, so
    a helper that stopped converting could not agree with itself. The day is
    matched either way round, so a change of format is not a failure of this.
    """
    when = datetime.fromisoformat(stamp).astimezone(m.LOCAL_TZ)
    return when.strftime("%H:%M"), _day_patterns(when)


def _utc_clock(stamp):
    when = datetime.fromisoformat(stamp).astimezone(timezone.utc)
    return when.strftime("%H:%M"), _day_patterns(when) + [re.escape(when.date().isoformat())]


def _day_patterns(when):
    month = when.strftime("%B")
    return [r"\b%s %d\b" % (month, when.day), r"\b%d %s\b" % (when.day, month)]


def _reads(text, clock):
    hhmm, days = clock
    return hhmm in text and any(re.search(p, text) for p in days)


def _shows_any(text, clock):
    hhmm, days = clock
    return hhmm in text or any(re.search(p, text) for p in days)


def _job_row(body, label):
    """The cells a person reads in one job's row of the Job status table."""
    at = body.find(">Job status<")
    table = body[at:body.find("</table>", at)] if at >= 0 else ""
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.S):
        cells = [visible_text(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S)]
        if cells and cells[0] == label:
            return cells
    return None


def _cleanup(conn):
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A stored moment shown with its time")
    conn = db()
    oc, _ec, _owner, _emp = clients()
    now = datetime.now(timezone.utc).isoformat()
    _cleanup(conn)
    marks = ",".join("?" * len(JOBS))
    saved = [dict(r) for r in conn.execute(
        f"SELECT * FROM automation_runs WHERE job_name IN ({marks})", JOBS)]

    s.section("The premise: a slice reads UTC's clock")
    # Without this nothing below proves anything: if the stamp and the house
    # agreed about the hour, a slice would pass every check a conversion does.
    local = datetime.fromisoformat(RAN).astimezone(m.LOCAL_TZ)
    s.check("the fixture's hour at the house is not the hour written in it",
            local.strftime("%H:%M") != RAN[11:16],
            detail=f"{RAN[11:16]} in the stamp, {local:%H:%M} here")
    s.check("nor is its day", local.date().isoformat() != RAN[:10],
            detail=f"{RAN[:10]} in the stamp, {local.date()} here")
    words = m.local_datetime_str(RAN)
    s.check("local_datetime_str answers in words, so a slice of it is not a date",
            m.parse_date(words[:10]) is None, detail=f"{words!r}, cut to {words[:10]!r}")
    s.check("and a stamp it cannot read comes back as the words it is given",
            m.local_datetime_str("not a stamp", unknown="not recorded") == "not recorded"
            and m.local_datetime_str(None, unknown="not recorded") == "not recorded",
            detail=repr(m.local_datetime_str("not a stamp", unknown="not recorded")))
    s.check("or as ? to every caller that gives none, as before",
            m.local_datetime_str("not a stamp") == "?" and m.local_time_str("") == "?")

    try:
        conn.execute(f"DELETE FROM automation_runs WHERE job_name IN ({marks})", JOBS)
        conn.execute(
            """INSERT INTO automation_runs (job_name, last_ran_at, last_status,
                   last_message, consecutive_failures, last_ok_at)
               VALUES ('weather', ?, 'failed', ?, 2, ?)""",
            (RAN, TAG + " the forecast did not answer", WORKED))
        # NOT NULL, so a row whose time cannot be read has something in it.
        conn.execute(
            """INSERT INTO automation_runs (job_name, last_ran_at, last_status,
                   last_message, consecutive_failures, last_ok_at)
               VALUES ('exchange_rates', 'not a stamp', 'failed', ?, 1, NULL)""",
            (TAG + " no rates came back",))
        conn.commit()
        r = oc.get("/admin/automation")
        body = r.get_data(as_text=True)
        labels = m.AUTOMATION_JOB_LABELS

        s.section("The automation page tells a run's time by the house's clock")
        s.check("the page opens", r.status_code == 200, response=r)
        row = _job_row(body, labels["weather"])
        s.check("the job has its row in Job status", row is not None and len(row) >= 3,
                detail=str(row))
        ran_cell, how = (row[1], row[2]) if row and len(row) >= 3 else ("", "")
        s.check("Last ran reads the house's time and day", _reads(ran_cell, _house_clock(RAN)),
                detail=f"{ran_cell!r}, for a run at {local:%H:%M} on {local:%B} {local.day} here")
        s.check("and not UTC's, an hour or two early and the day before",
                not _shows_any(ran_cell, _utc_clock(RAN)), detail=repr(ran_cell))
        worked_at = how.split("Last worked:", 1)[-1].strip() if "Last worked:" in how else ""
        s.check("a failed job still says when it last worked", bool(worked_at), detail=repr(how))
        s.check("in the house's time and day too", _reads(worked_at, _house_clock(WORKED)),
                detail=repr(worked_at))
        s.check("not UTC's", not _shows_any(worked_at, _utc_clock(WORKED)),
                detail=repr(worked_at))

        s.section("And says plainly when a job's time is not known")
        row = _job_row(body, labels["exchange_rates"])
        ran_cell, how = (row[1], row[2]) if row and len(row) >= 3 else ("", "")
        s.check("a run whose time cannot be read says it was not recorded",
                ran_cell == "not recorded", detail=repr(ran_cell))
        s.check("rather than a question mark, or the stamp as it is",
                "?" not in ran_cell and "not a stamp" not in ran_cell, detail=repr(ran_cell))
        # Not "never": last_ok_at came after the jobs, so a blank one can be a
        # job that worked for months before anything wrote down when.
        s.check("and a job with no success on record says that, not never",
                "Last worked: not recorded" in how and "never" not in how and "?" not in how,
                detail=repr(how))
        row = _job_row(body, labels["page_translation"])
        s.check("a job that has never run here says never",
                bool(row) and len(row) >= 2 and row[1] == "never", detail=str(row))
    finally:
        conn.execute(f"DELETE FROM automation_runs WHERE job_name IN ({marks})", JOBS)
        for old in saved:
            cols = list(old)
            conn.execute(
                f"INSERT INTO automation_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", [old[c] for c in cols])
        conn.commit()

    s.section("The driver's list gives a pickup the house's time")
    # Half past midnight: the day before in UTC, and an hour or two back, in
    # either half of the year -- which is a delayed flight into Toulouse.
    tomorrow = house_today() + timedelta(days=1)
    pickup = datetime.combine(tomorrow, datetime.min.time().replace(minute=30)
                              ).replace(tzinfo=m.LOCAL_TZ).astimezone(timezone.utc).isoformat()
    s.check("the fixture is the day before, and another hour, in UTC",
            pickup[:10] != tomorrow.isoformat() and pickup[11:16] != "00:30",
            detail=pickup)
    conn.execute("INSERT INTO vehicles (name, license_plate, created_at) VALUES (?, ?, ?)",
                 (TAG + " Defender", "ZZ-001-ZZ", now))
    vehicle = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction,
               scheduled_at, notes, created_at)
           VALUES (?, ?, 'pickup', ?, 'Toulouse, the last flight in', ?)""",
        (vehicle, TAG + " Okonkwo", pickup, now))
    conn.commit()
    try:
        r = oc.get("/transfers")
        body = r.get_data(as_text=True)
        at = body.find(TAG + " Okonkwo")
        start = body.rfind('<div class="mini-row">', 0, at) if at >= 0 else -1
        end = body.find('<div class="mini-row">', at) if at >= 0 else -1
        line = visible_text(body[start:end if end > at else at + 800]) if start >= 0 else ""
        s.check("the pickup is on the list", bool(line), response=r)
        s.check("at half past midnight", "00:30" in line, detail=line[:160])
        s.check("and not at UTC's hour, which read as most of a day late",
                pickup[11:16] not in line, detail=line[:160])
    finally:
        _cleanup(conn)

    s.section("No template cuts a stored moment to show it")
    names = sorted(n for n in os.listdir(TEMPLATES) if n.endswith(".html"))
    found = []
    for name in names:
        with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
            src = f.read()
        lines = src.splitlines()
        found += ["%s:%d %s: %s" % (name, n, why, lines[n - 1].strip()[:80])
                  for n, why in cut_stamps(src)]
    # Counted, so a sweep that stopped reading templates would say so.
    s.check("the sweep reads %d templates" % len(names), len(names) > 250,
            detail="too few to be reading the templates at all")
    s.check("none cuts a stored moment to show it", not found,
            detail="%d, e.g. %s -- local_datetime_str() shows a moment, "
                   "local_time_str() its time, |date_short its day"
                   % (len(found), "; ".join(found[:3])))

    s.section("And the sweep can tell the difference")
    # Every rule has a line here that only it catches (3 and 4 the _at slice,
    # 6 the time, 7 the T, 8 and 9 the words), so a rule that stops working
    # shows as a line gone missing. 1, 2 and 5 are as the templates had them.
    probe = "\n".join([
        "{{ run['last_ran_at'][:19].replace('T', ' ') }}",               # 1  the page, as it was
        "{{ e['offer_expires_at'][:16]|replace('T', ' ') }}",            # 2  as a filter
        "{{ msg.created_at[:16] }}",                                     # 3  undressed, still UTC
        "{{ (r['sent_at'] or '')[:16] }}",                               # 4  through or ''
        "{{ (msg['executed_at'] or '')[11:16] }}",                       # 5  its time alone
        "{{ shopping.as_of[11:16] }} UTC",                               # 6  a name not _at
        "{{ (msg.when or '')[:16]|replace('T', ' ') }}",                 # 7  nor this, but the T
        "{{ local_datetime_str(h['issued_at'])[:10] }}",                 # 8  its words, cut
        "{{ (x.at|date_short)[:6] }}",                                   # 9  a formatter's, cut
        "{{ local_datetime_str(run['last_ran_at'], unknown='not recorded') }}",  # 10 whole
        "{{ local_datetime_str(x) }} [draft]",                           # 11 a bracket in the text
        "{{ h['created_at'][:10] }}",                                    # 12 a day: test_utc_slices'
        "{{ e['description'][:80] }}",                                   # 13 not a stamp
        "{% for g in compliance_gaps[:12] %}",                           # 14 not a stamp
        "{{ (r.serial_number or '')[:14] }}",                            # 15 not a stamp
        "{# {{ run['last_ran_at'][:19] }} #}",                           # 16 a comment
        "{{ x.replace('Tuesday', 'Tue') }}",                             # 17 a T that is a word's
        "{{ created_attribute[:16] }}",                                  # 18 only starts like _at
        "{{ t['scheduled_at']|house_day }} {{ local_time_str(t['scheduled_at'], unknown='—') }}",
    ])
    got = [n for n, _why in cut_stamps(probe)]
    s.check("it names the nine that cut a moment and none of the ten that do not",
            got == list(range(1, 10)), detail="named lines %s, expected 1 to 9" % got)

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
