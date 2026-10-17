"""The two things put right once on 16 October 2026, on the owner's word.

"Fix these and push", about two things the live site still had wrong after
the z22 install:

- The stay confirmation stored on the site said "Five bedrooms" and "the
  beds ... are new". Stored letters are the owner's words and the app only
  NAMES one that breaks a rule; this is the one exception, and it is a
  sentence-for-sentence swap, so nothing else in the letter moves.
- The Gold Suite had lost "Mountain view" to the old room form, which had no
  field for the view and wrote it blank on every save.

Both run once. A flag in app_settings is what stops either undoing an edit
the owner makes afterwards, so each check below also puts the old words back
after the run and proves the second run leaves them.
"""
from _harness import Suite, db, keep_rows
import _harness

m = _harness.m

# The stay confirmation as stored before 5 October and never edited -- the
# local copy's own, trimmed -- with one sentence of the owner's own added.
OLD_LETTER = (
    "Your room is held, {first_name}\n\nFive bedrooms of ninety-four are finished, and one of "
    "them is yours for the dates below.\n\n{stay_details}\n\nThe walls of this house are not "
    "finished and will not be while you are here — that is the point of it. The floors, the "
    "beds and the bathrooms are new. If anything at all needs saying before you arrive, reply "
    "to this message; it reaches us.\n\nZZOWNER: Bruce will meet you at the gates.")


def run():
    s = Suite("Put right once, 16 October")
    conn = db()
    put_letters = keep_rows(conn, "email_templates", "template_key = 'room_confirmed'")
    # The room's view and name only, put back by UPDATE: keep_rows deletes and
    # re-inserts, and a room row is what its bookings and photographs hang from.
    # The Gold Suite is the Marquis Suite since the names of 16 October, which
    # run after this on every boot; the view is put back under the name it had.
    was = conn.execute("SELECT id, name, outlook FROM rooms WHERE name IN "
                       "('The Marquis Suite', 'The Gold Suite')").fetchone()

    def put_rooms():
        if was:
            conn.execute("UPDATE rooms SET name = ?, outlook = ? WHERE id = ?",
                         (was["name"], was["outlook"], was["id"]))
            conn.commit()
    put_flags = keep_rows(conn, "app_settings",
                          "key IN ('letter_words_2026_10_16', 'room_views_2026_10_16')")
    try:
        s.section("The stay confirmation stored on the site")
        conn.execute("DELETE FROM app_settings WHERE key = 'letter_words_2026_10_16'")
        conn.execute("UPDATE email_templates SET body = ? WHERE template_key = 'room_confirmed'",
                     (OLD_LETTER,))
        conn.commit()
        before = {b["rule"] for b in m.letter_rule_breaches("room_confirmed", "x", OLD_LETTER)}
        s.check("the rules name it first, for five bedrooms and the new beds",
                {"room_words", "new_beds"} <= before, detail=str(before))
        changed = m.apply_letter_words_2026_10_16(conn)
        body = conn.execute("SELECT body FROM email_templates WHERE template_key = "
                            "'room_confirmed'").fetchone()["body"]
        s.check("it is corrected", changed >= 1, detail=f"{changed} letter(s)")
        s.check("five guest rooms, and the rooms furnished from the brocantes",
                "Five guest rooms are open, and one of them is yours" in body
                and "the rooms are furnished from the brocantes" in body, detail=body[:300])
        after = {b["rule"] for b in m.letter_rule_breaches("room_confirmed", "x", body)}
        s.check("and the rules no longer name it for either, nor for ninety-four",
                not ({"room_words", "new_beds", "room_count"} & after), detail=str(after))
        s.check("every other word in it is the owner's, as it was",
                body.endswith("ZZOWNER: Bruce will meet you at the gates.")
                and "that is the point of it." in body, detail=body[-200:])

        s.section("Once, so an edit made afterwards stays made")
        conn.execute("UPDATE email_templates SET body = ? WHERE template_key = 'room_confirmed'",
                     (OLD_LETTER,))
        conn.commit()
        s.check("a second run changes nothing", m.apply_letter_words_2026_10_16(conn) == 0)
        s.check("and the letter is left as it was written",
                conn.execute("SELECT body FROM email_templates WHERE template_key = "
                             "'room_confirmed'").fetchone()["body"] == OLD_LETTER)

        s.section("The Gold Suite's view")
        gold = was
        if not gold:
            s.check("there is a Gold Suite (the Marquis Suite) to put it back on", False,
                    detail="reported rather than skipped")
        else:
            # Under the name the 16 October rename found it by.
            conn.execute("UPDATE rooms SET name = 'The Gold Suite' WHERE id = ?", (gold["id"],))
            conn.execute("DELETE FROM app_settings WHERE key = 'room_views_2026_10_16'")
            conn.execute("UPDATE rooms SET outlook = NULL WHERE id = ?", (gold["id"],))
            conn.commit()
            s.check("a blank view is put back",
                    m.apply_room_views_2026_10_16(conn) == 1
                    and conn.execute("SELECT outlook FROM rooms WHERE id = ?",
                                     (gold["id"],)).fetchone()["outlook"] == "mountain")
            page = m.app.test_client().get("/book").get_data(as_text=True)
            card = page[page.find(f'id="room-{gold["id"]}"'):]
            card = card[:card.find("</article>")]
            s.check("and the Stay card says it", "Mountain view" in card,
                    detail="the card reads the outlook as '<word> view'")
            conn.execute("DELETE FROM app_settings WHERE key = 'room_views_2026_10_16'")
            conn.execute("UPDATE rooms SET outlook = 'garden' WHERE id = ?", (gold["id"],))
            conn.commit()
            s.check("one the owner has typed is kept",
                    m.apply_room_views_2026_10_16(conn) == 0
                    and conn.execute("SELECT outlook FROM rooms WHERE id = ?",
                                     (gold["id"],)).fetchone()["outlook"] == "garden")
            conn.execute("UPDATE rooms SET outlook = NULL WHERE id = ?", (gold["id"],))
            conn.commit()
            s.check("and once run, one he clears stays cleared",
                    m.apply_room_views_2026_10_16(conn) == 0
                    and conn.execute("SELECT outlook FROM rooms WHERE id = ?",
                                     (gold["id"],)).fetchone()["outlook"] is None)
    finally:
        put_letters()
        put_rooms()
        put_flags()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
