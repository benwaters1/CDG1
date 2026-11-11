"""RetreatGuru's waitlist, brought into the guest list so it can be written to.

The owner, 10 November 2026: the people who joined a waitlist on the old
system are to be emailed along with past guests, so put them "somewhere like
in the guest list so we can bulk email". The export is one row per retreat a
person waited for, so the same address turns up five times; some addresses
have stray spaces or capitals; some of the people are past guests already.

So this holds the guest import to the waitlist file through the owner's page:

  - one profile per person, never two, and an existing profile only added to;
  - the retreats each waited for written on the profile;
  - everybody in the file tagged, so the guest list can show them as a group;
  - that tag offered as a campaign audience, counted right, with anybody who
    unsubscribed left out and a past guest on both lists written to once;
  - a run with "show me first" writes nothing, and a second run changes
    nothing.

No letter is sent from here.
"""
from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZWL"
LOW = TAG.lower()
HEADER = ("first_name,last_name,ID,Date Added,Position,Last Emailed,Email,Emailed,Program,"
          "Program Dates,Price Choice,Registration")


def _row(first, last, rid, email, position, program, dates):
    return (f'"{first}","{last}","{rid}","2025-05-12 17:21:25","{position}",'
            f'"2025-07-20 21:23:20","{email}","Yes","{program}","{dates}","",""')


def _file():
    a, b, c, d = (f"{LOW}.{x}@example.invalid" for x in ("ana", "bo", "cleo", "dot"))
    return "\n".join([
        HEADER,
        _row("Ana", f"{TAG}Ruiz", 1, a, "174 - 1", "Cooking In The Cuisine July 2026",
             "Jul 10 - 15, 2026"),
        # The same person on three waitlists, once in capitals with a space.
        _row("Ana", f"{TAG}Ruiz", 2, a.upper() + " ", "173 - 5",
             "The Long Weekender July 2026", "Jul 4 - 7, 2026"),
        _row("Ana", f"{TAG}Ruiz", 3, a, "175 - 2", "Seven Starry Nights July 2026",
             "Jul 18 - 25, 2026"),
        _row("Bo", f"{TAG}Lang", 4, b, "168 - 3", "The Long Weekender June 2026",
             "Jun 6 - 9, 2026"),
        # Already a guest here, with a phone and a note somebody typed.
        _row("Cleo", f"{TAG}Fern", 5, c, "56 - 2", "Cooking In The Cuisine June 2022",
             "Jun 25 - 30, 2022"),
        # Asked to be left alone already: tagged, but never written to.
        _row("Dot", f"{TAG}Quiet", 6, d, "49 - 3", "The Weekender July 2022",
             "Jul 2 - 5, 2022"),
    ]) + "\n"


def _clean():
    conn = db()
    like = f"{LOW}.%"
    conn.execute("DELETE FROM guest_tags WHERE guest_id IN (SELECT id FROM guests "
                 "WHERE email LIKE ?)", (like,))
    conn.execute("DELETE FROM guest_profile_changes WHERE guest_id IN (SELECT id FROM guests "
                 "WHERE email LIKE ?)", (like,))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _upload(oc, *, dry_run, tag="Waitlist "):
    import io
    data = {"people": (io.BytesIO(_file().encode("utf-8")), "waitlistlist.csv"), "tag": tag}
    if dry_run:
        data["dry_run"] = "on"
    return oc.post("/management/import-guests", data=data, content_type="multipart/form-data")


def _mine(conn):
    return {g["email"]: g for g in conn.execute(
        "SELECT * FROM guests WHERE email LIKE ? AND merged_into_id IS NULL ORDER BY id",
        (f"{LOW}.%",))}


def _tags(conn, gid):
    return [r["tag"] for r in conn.execute(
        "SELECT tag FROM guest_tags WHERE guest_id = ? ORDER BY tag", (gid,))]


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append(to_address)
        return True


def run():
    s = Suite("RetreatGuru's waitlist, into the guest list")
    oc, _ec, _owner, _emp = clients()
    _clean()
    mail = Recorder()
    real = m.send_email
    m.send_email = mail
    try:
        _suite(s, oc, mail)
    finally:
        m.send_email = real
        _clean()
    return s


def _suite(s, oc, mail):
    a, b, c, d = (f"{LOW}.{x}@example.invalid" for x in ("ana", "bo", "cleo", "dot"))
    conn = db()
    now = _harness.datetime_now()
    conn.execute("INSERT INTO guests (name, email, phone, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                 (f"Cleo {TAG}Fern", c, "+33 6 00 00 00 03", "Loves the tower room.", now))
    conn.execute("INSERT INTO email_optouts (email, created_at) VALUES (?, ?)", (d, now))
    conn.commit()
    conn.close()

    s.section("Reading the file")
    people, problems = m.parse_guest_import(_file())
    ana = next((p for p in people if p["email"] == a), None)
    s.check("one person per address, however the address was typed",
            sorted(p["email"] for p in people) == sorted([a, b, c, d]) and not problems,
            detail=str([p["email"] for p in people]))
    s.check("with every retreat they waited for, and its dates",
            ana and ana["notes"] == [
                "On the waitlist for Cooking In The Cuisine July 2026 (Jul 10 - 15, 2026)",
                "On the waitlist for The Long Weekender July 2026 (Jul 4 - 7, 2026)",
                "On the waitlist for Seven Starry Nights July 2026 (Jul 18 - 25, 2026)"],
            detail=str(ana and ana["notes"]))
    s.check("the waitlist's own numbers are not read as a person's",
            ana and ana["programs"] == "" and ana["person_ids"] == []
            and ana["created_at"] == "2025-05-12 17:21:25", detail=str(ana))

    s.section("Shown first, with nothing written")
    conn = db()
    before = len(_mine(conn))
    conn.close()
    r = _upload(oc, dry_run=True)
    page = visible_text(r.get_data(as_text=True))
    conn = db()
    s.check("it answers, and writes nothing", r.status_code == 200 and len(_mine(conn)) == before,
            detail=f"{r.status_code} {before} -> {len(_mine(conn))}")
    conn.close()
    s.check("and says it read a waitlist, and who would be tagged",
            "Read as RetreatGuru's waitlist" in page
            and "Would be tagged “waitlist” 4" in page, detail=page[:0])

    s.section("Brought in")
    r = _upload(oc, dry_run=False)
    said = " ".join(flashes(r))
    conn = db()
    mine = _mine(conn)
    s.check("three new profiles and the guest already here, four in all",
            sorted(mine) == sorted([a, b, c, d]) and len(mine) == 4, detail=str(sorted(mine)))
    cleo = mine.get(c)
    s.check("the guest already here keeps her name, phone and note",
            cleo and cleo["name"] == f"Cleo {TAG}Fern" and cleo["phone"] == "+33 6 00 00 00 03"
            and cleo["notes"].startswith("Loves the tower room."), detail=str(dict(cleo or {})))
    s.check("with what she waited for added beneath, and where it came from",
            cleo and "On the waitlist for Cooking In The Cuisine June 2022 (Jun 25 - 30, 2022)"
            in cleo["notes"] and "From the old workshop booking system's waitlist" in cleo["notes"],
            detail=cleo and cleo["notes"])
    s.check("every one of them tagged “waitlist”, once",
            all(_tags(conn, g["id"]) == ["waitlist"] for g in mine.values()),
            detail=str({e: _tags(conn, g["id"]) for e, g in mine.items()}))
    s.check("and the summary says so", "4 tagged “waitlist”" in said, detail=said)
    conn.close()

    s.section("Found in the guest list as a group")
    page = visible_text(oc.get("/guests?tag=waitlist&q=" + TAG).get_data(as_text=True))
    s.check("the tag is a chip there, and finds them",
            all(n in page for n in (f"Ana {TAG}Ruiz", f"Bo {TAG}Lang", f"Cleo {TAG}Fern")),
            detail=page[:600])

    s.section("A campaign can be sent to them")
    conn = db()
    conn.execute(
        """INSERT INTO campaign_templates (name, subject, body, area, created_at)
           VALUES (?, 'Hello {{first_name}}', 'Dates for 2027.', 'workshops', ?)""",
        (f"{TAG} letter", _harness.datetime_now()))
    tid = conn.execute("SELECT id FROM campaign_templates WHERE name = ?",
                       (f"{TAG} letter",)).fetchone()["id"]
    conn.commit()
    with m.app.test_request_context():
        audience = m.campaign_audience(conn, ["tag:waitlist"])
        both = m.campaign_audience(conn, ["tag:waitlist", "profiles"])
        profiles = m.campaign_audience(conn, ["profiles"])
    conn.close()
    ours = {e for e in audience if e.startswith(f"{LOW}.")}
    s.check("the tag's audience is the waitlist, without the one who unsubscribed",
            ours == {a, b, c}, detail=str(sorted(ours)))
    s.check("ticked with every guest profile, each person is written to once",
            len(both) == len(set(both)) == len(set(profiles) | set(audience)),
            detail=f"{len(both)} vs {len(set(profiles) | set(audience))}")
    page = oc.get(f"/admin/emails/{tid}").get_data(as_text=True)
    s.check("the campaign page offers it as an audience",
            'value="tag:waitlist"' in page and "Tagged “waitlist”" in visible_text(page),
            detail=visible_text(page)[:0])
    s.check("a tag only resembling one is not accepted",
            m.valid_segments(["tag:waitlist", "tag:", "tag:Waitlist", "tag:wait  list",
                              "tag:a:b"]) == ["tag:waitlist"])
    r = oc.post(f"/admin/emails/{tid}/send", data={"segments": ["tag:waitlist"],
                                                   "confirm_count": "999"},
                follow_redirects=True)
    s.check("and a send still wants the count typed back, which here it was not",
            any("Type " in f and "into the confirm box" in f for f in flashes(r))
            and not mail.calls, detail=f"{flashes(r)} {mail.calls}")

    s.section("A second run changes nothing")
    conn = db()
    before = {e: (g["notes"], _tags(conn, g["id"])) for e, g in _mine(conn).items()}
    conn.close()
    r = _upload(oc, dry_run=False)
    conn = db()
    after = {e: (g["notes"], _tags(conn, g["id"])) for e, g in _mine(conn).items()}
    conn.close()
    s.check("no profile, note or tag is added twice", before == after,
            detail=f"{before} -> {after}")
    s.check("and it says nobody gained the tag", "0 tagged “waitlist”" in " ".join(flashes(r)),
            detail=str(flashes(r)))

    s.section("Nothing was sent")
    s.check("no email of any kind", not mail.calls, detail=str(mail.calls))


if __name__ == "__main__":
    print(run().report())
