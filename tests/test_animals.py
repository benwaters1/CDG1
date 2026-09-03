"""The house has six cats, two dogs and chickens, and says so.

Which means it has to say the other half too. Somebody with an animal allergy
reading a warm paragraph about Bruce going where he likes needs the sentence
that tells them to bring what they need and say so in advance — and they need
it on the page they are ACTUALLY BOOKING FROM, not on an estate page they may
never open.

So the rule is a pairing, not a presence: no public page may name the animals
without carrying the allergy note. Checked on the RENDERED page rather than in
the source, because the note travels inside a macro — a source-level check
passes on a page that imports the partial and never calls it, which is exactly
the page that would ship the charm without the caveat.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m

# The animals by name, as the copy actually mentions them.
NAMES = re.compile(r"\bBruce\b|six cats|two dogs", re.I)
NOTE = re.compile(r"allerg", re.I)


def run():
    s = Suite("the animals")
    clients()
    anon = m.app.test_client()
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    session = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY start_date DESC LIMIT 1").fetchone()
    conn.close()

    pages = ["/", "/book", "/facilities", "/workshops", "/restaurant",
             "/restoration", "/gallery", "/whats-on", "/story", "/press"]
    if room:
        pages.append("/book/%s" % room["id"])
    if session:
        pages.append("/workshops/register/%s" % session["id"])

    s.section("Wherever the animals are mentioned, so is the allergy note")
    rendered = {}
    broken = []
    for path in pages:
        r = anon.get(path)
        if r.status_code != 200:
            broken.append("%s (HTTP %s)" % (path, r.status_code))
            continue
        rendered[path] = r.get_data(as_text=True)
    s.check("every public page opens", not broken, detail=", ".join(broken))

    naming = [p for p, body in rendered.items() if NAMES.search(body)]
    # If nothing names them, the two checks below pass on an empty list and
    # prove nothing at all.
    s.check("the animals are on the site somewhere", naming,
            detail="nothing mentions them, so the pairing below is untested")

    silent = [p for p in naming if not NOTE.search(rendered[p])]
    s.check("and no page names them without the allergy note", not silent,
            detail="; ".join(silent) + " — somebody who reacts to cats is "
                   "reading the charming half and booking on it")

    s.section("It reaches the pages somebody commits on")
    # An estate page is where you read about the house. These are where you
    # hand over a card, and they are the ones that have to carry it.
    committing = ["/book"]
    if room:
        committing.append("/book/%s" % room["id"])
    if session:
        committing.append("/workshops/register/%s" % session["id"])
    missing = [p for p in committing
               if p in rendered and not NOTE.search(rendered[p])]
    s.check("the booking pages carry it", not missing,
            detail="; ".join(missing) + " — the note is no use on a page "
                   "nobody reads before paying")

    s.section("It says what to do, not just that a risk exists")
    body = rendered.get("/book", "")
    s.check("it asks the guest to tell the house in advance",
            "advance" in body.lower() or "tell us" in body.lower(),
            detail="a warning with no action is a disclaimer, not a courtesy")

    return s


if __name__ == "__main__":
    print(run().report())
