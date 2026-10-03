"""Put back what a design handover keeps taking out.

Run this straight after unzipping a `gudanes-final_NN.zip` over the templates,
then run the suite. It repairs the four things that have now been reverted by
more than one handover in a row, and it is idempotent — running it on an
already-repaired tree changes nothing.

Run tools/check_handover.py FIRST. This file puts back eight things that
have each been reverted more than once; that list can only contain
regressions which have already happened, so it was blind to all eleven in
the ninth handover. check_handover.py needs no such list — it asks git
blame which commit each removed line came from, so it finds the ones
nobody has met yet. Read its report, then run this, then run the suite.

tools/export_for_design.py is the fix, and it now exists: it hands the design
side a snapshot of current main and refuses to build one from a tree that is
behind. Nothing needs repairing if nothing was reverted. Everything below is
for zips built before that was in use.

This is a WORKAROUND, not a fix. The cause is that the zips are generated from a
snapshot of the tree rather than from current main, so anything shipped after
that snapshot is silently reverted by whichever file touches it. The real fix is
one sentence upstream: regenerate from current main before exporting. Until that
happens, this script and the suite are what stand between a handover and a
regression.

The eight:

  1. noindex. 24 guest pages carry `{% block robots %}` overriding the empty
     block in public_base. The handovers strip the block from the parent too,
     which turns every override into dead markup — nothing errors, the pages
     render perfectly, and a guest's booking becomes indexable.
  2. Part-payments and the auto-charge opt-out in workshop_manage.html. Five
     handovers, five deletions.
  3. .table-wrap around the tables in guest_statement.html, without which a
     wide table drags the whole page sideways on a phone.
  4. The footer's Privacy Policy link, which comes back as href="#".
  5. A hardcoded market list in whats_on.html, duplicating the editable,
     translated rows in the `whats_on` table.
  6. url_for('manage_booking', token=...) where the route takes manage_token,
     which 500s every room booking confirmation.
  7. The .g-plate__row rule, which the stylesheet arrives without while a
     dozen templates use the class.
  8. The `checked` expression on the booking form's extras. Without it a guest
     who hits a validation error, or backs out of the card page, silently
     loses the airport transfer they had picked.
  9. The `value=` on both date inputs of the event enquiry form. Same fault as
     8 on a different form: somebody proposing a wedding hits a validation
     error and finds the dates they chose have been emptied.

Each is also guarded by a test (test_noindex_meta, test_part_payments,
test_autocharge, test_table_overflow, test_privacy), so a handover that breaks
something this does not know about still goes red. This just saves the manual
repair on the four that recur.
"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROBOTS = '{% block robots %}<meta name="robots" content="noindex, nofollow">{% endblock %}'

# The pages that carry their own noindex. Held as a list because the handover
# strips it, so the tree itself cannot be asked which ones used to have it.
NOINDEX_PAGES = [
    "booking_confirmation.html", "error.html", "event_confirmation.html",
    "event_find.html", "event_manage.html", "find_booking.html",
    "guest_account.html", "guest_account_expired.html", "guest_account_request.html",
    "guest_feedback_form.html", "guest_feedback_submitted.html", "guest_portal.html",
    "guest_statement.html", "manage_booking.html", "newsletter_confirmed.html",
    "restaurant_confirmation.html", "restaurant_find.html", "restaurant_manage.html",
    "unsubscribe.html", "workshop_confirmation.html", "workshop_feedback_form.html",
    "workshop_find.html", "workshop_manage.html", "workshop_register.html",
]


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n")


def _write(rel, text):
    with io.open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


TEMPLATE_DIR = os.path.join(ROOT, "templates")

# The public pages, by what they extend. Used to keep the svg sweep off the
# staff app, whose icons are not a handover's business.
PUBLIC_ROOTS = {"public_base.html"}


def _from_git(rel):
    """The file as main has it, or None."""
    try:
        return subprocess.run(
            ["git", "show", "HEAD:" + rel], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", check=True).stdout.replace("\r\n", "\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _comment_block(text, needle):
    """The lines of the {# ... #} that contains `needle`."""
    lines = text.split("\n")
    i = next((k for k, l in enumerate(lines) if needle in l), None)
    if i is None:
        return None
    start = next((k for k in range(i, -1, -1) if "{#" in lines[k]), None)
    end = next((k for k in range(i, len(lines)) if "#}" in lines[k]), None)
    if start is None or end is None:
        return None
    return lines[start:end + 1]


def _jinja_block(text, opener):
    """The lines of a {% if %}...{% endif %}, tags balanced, with the comment
    above it. Counting rather than matching the first endif, because these
    blocks contain a for-loop and an inner if."""
    lines = text.split("\n")
    i = next((k for k, l in enumerate(lines) if opener in l), None)
    if i is None:
        return None
    start = i
    for k in range(i, -1, -1):
        if lines[k].lstrip().startswith("{#"):
            start = k
            break
        if lines[k].strip() and not lines[k].lstrip().startswith(("{#", "   ")):
            break
    depth = 0
    for k in range(i, len(lines)):
        depth += len(re.findall(r"{%-?\s*(?:if|for)\b", lines[k]))
        depth -= len(re.findall(r"{%-?\s*end(?:if|for)\b", lines[k]))
        if depth == 0:
            return lines[start:k + 1]
    return None


def repair_confirmation_map_pin():
    """A default latitude is the failure the pin was put there to prevent.

    The confirmation letter offers its map from COORDINATES rather than the
    address, because the chateau has no street number and an address search
    sends people to the village square — in the dark, at the end of a drive
    from Toulouse, a mile below where they are expected.

    A hardcoded default is that same failure wearing the fix's clothes. The
    coordinates are then never absent, so the branch that says "telephone us
    and we will talk you up the last few miles" can never run, and every
    guest is sent instead to a point somebody typed once and nobody has
    checked since. Wrong quietly, for everybody, forever.

    Two handovers have reverted it identically — defaults back, guard and
    else-branch gone — because the design side's tree still carries the older
    macro. The prose either side of the guard is unchanged, so this is
    structure and not intent: the block is lifted back off the commit rather
    than retyped, and only when the hardcoded default is actually there. Edit
    the coordinates into something new and this stops firing, which is the
    behaviour you want the day somebody genuinely moves the pin.
    """
    rel = "templates/_email.html"
    src = _read(rel)
    if "settings.get('lat', '42.7847')" not in src:
        return 0
    head = _from_git(rel)
    if head is None:
        return 0

    want = head.split("\n")
    a = next((k for k, l in enumerate(want)
              if "{% set lat = settings.get('lat') %}" in l), None)
    if a is None:
        return 0
    b = next((k for k in range(a, len(want))
              if want[k].strip() == "{% endif %}"), None)
    if b is None:
        return 0

    lines = src.split("\n")
    c = next((k for k, l in enumerate(lines)
              if "settings.get('lat', '42.7847')" in l), None)
    d = next((k for k in range(c, len(lines))
              if "'Open in Apple Maps'" in lines[k]), None)
    if d is None:
        return 0

    _write(rel, "\n".join(lines[:c] + want[a:b + 1] + lines[d + 1:]))
    return 1


# The anchors the public buttons scroll to, and the element each belongs on.
# Kept as data because the failure is always the same shape: the button
# survives the export and the id it points at does not.
SCROLL_ANCHORS = [
    ("templates/book_rooms.html", "book", '<section class="g-book-bar">'),
    ("templates/contact.html", "write",
     '<section class="g-sec"><div class="g-two">'),
]


def repair_scroll_anchors():
    """Buttons that scroll nowhere: an href with no id to answer it.

    This is the quietest of the four ways a link dies. A missing page is a
    404 and a broken image leaves a hole, but href="#book" with nothing
    carrying that id is not an error at any level — the browser is asked to
    scroll to nothing and obliges. No console line, no exception, no server
    log. The guest presses "Check dates" on the Stay page, the page sits
    perfectly still, and they conclude the site is broken rather than that
    an attribute is missing. Three buttons on that page and one on Contact.

    The id is put back only where the file still HAS the href, so this reads
    the need out of the template rather than asserting it: take the button
    away and the repair stops firing, which is the correct behaviour the day
    somebody genuinely redesigns the section.
    """
    done = 0
    for rel, anchor, marker in SCROLL_ANCHORS:
        src = _read(rel)
        if 'href="#%s"' % anchor not in src:
            continue            # nothing points there any more; leave it
        if 'id="%s"' % anchor in src:
            continue            # already answered
        if src.count(marker) != 1:
            print("  ! %s: cannot place id=\"%s\" — the section it belongs "
                  "on has moved" % (rel, anchor))
            continue
        _write(rel, src.replace(
            marker, marker.replace(">", ' id="%s">' % anchor, 1), 1))
        done += 1
    return done


# The ten. Each is (file, what is there, what it should be, why).
#
# Two shapes. Where the photograph belonged in its slot and the CAPTION had
# drifted, the caption is corrected. Where the caption was right and the
# wrong picture was in the slot, the picture is swapped -- and always for one
# already in use on this site under exactly that description, so nothing new
# is hotlinked and nothing is invented.
_CDN = "https://images.squarespace-cdn.com/content/53c3b576e4b02bad423517b8/"

CAPTION_FIXES = [
    ("templates/book_rooms.html",
     'alt="Restoration work in progress"',
     'alt="Chambre Bleue, one of the five finished rooms"',
     "a finished blue bedroom, beside prose about the beds and the linen"),

    ("templates/restoration.html",
     'March+Kitchen+Shots-3.jpg?format=1500w" alt="Work in progress"',
     'March+Kitchen+Shots-3.jpg?format=1500w" alt="Copper pans in the kitchen"',
     "copper pans, in a section headed Staying Here"),

    ("templates/book_rooms.html",
     _CDN + 'cc7f6cb2-243c-47a9-ab7e-5200a66afca1/bedroom+3.jpg?format=1000w'
     '" alt="The kitchen table in winter"',
     _CDN + '1698035463433-V8UTXJPKVH28T0HM15XV/Winter+kitchen+table-15.jpg'
     '?format=1000w" alt="The kitchen table in winter"',
     "fourth in a band of kitchen, pans and dining room"),

    ("templates/events_info.html",
     _CDN + "1785412379425-1YELJ7RXRF6AHGQEHCF6/Chambre+Bleue+4.png",
     _CDN + "1728027560330-AOHMYTLQV9BVVSYW5FS3/Gardens+-+Formal+Gardens+2.jpg",
     "captioned The formal gardens"),

    ("templates/events_info.html",
     _CDN + "1731560785352-PVDXL7V3QVW8X7C83ZEI/Chambre+du+Parc+1.jpg",
     _CDN + "e8a9fd2f-c0cf-427b-b438-473be3217d51/Chateau+At+Night_.jpg",
     "captioned The chateau at night, on a site that HAS that photograph"),

    ("templates/restaurant_info.html",
     _CDN + "296960eb-e33e-48c6-8f8d-dddb0ec56854/Kitchen+1.JPG",
     _CDN + "1583218908946-B7KNZLCLRCS5QH1EYFG3/Dining+Room.jpg",
     "captioned The dining room laid for dinner"),

    ("templates/restoration.html",
     _CDN + "1152eda0-220a-4d22-8fce-f9f82879739e/Chambre+des+Fleurs+1.jpg"
     "?format=1500w",
     _CDN + "1582799762807-88XQQSIAXWOEHIA2UVSO/restoration+1.jpg?format=1500w",
     "the hero of the restoration page, captioned Restoration work in progress"),

    ("templates/workshops_public.html",
     _CDN + "aafc2bf8-ecca-4961-a8b4-3aa0ea6235f5/Classic+Double.jpg",
     _CDN + "296960eb-e33e-48c6-8f8d-dddb0ec56854/Kitchen+1.JPG",
     "first in a band captioned kitchen, pans, dining room, kitchen table"),

    # Neither half was right on these two, and the reason is worth keeping:
    # the site has no photograph of the pool or of the tennis court, both of
    # which it sells. The grounds are at least the same subject, and the
    # caption then says what is actually in the frame.
    ("templates/facilities.html",
     _CDN + "1152eda0-220a-4d22-8fce-f9f82879739e/Chambre+des+Fleurs+1.jpg"
     '?format=1000w" alt="The grounds at dusk"',
     _CDN + "8d003a05-1829-44b5-ac00-2ed7e8d24395/_DSC8827.jpg"
     '?format=1000w" alt="The grounds, with the court at the far edge"',
     "the card for Le Court, which had a bedroom on it"),

    ("templates/book_rooms.html",
     _CDN + "aafc2bf8-ecca-4961-a8b4-3aa0ea6235f5/Classic+Double.jpg"
     '?format=1000w" alt="The pool"',
     _CDN + "1785430040227-II7R17Q9KKPD2Q39TWUP/IMG_3493.jpg"
     '?format=1000w" alt="The grounds below the ch\u00e2teau"',
     "the Outdoors card, which had a bedroom captioned The pool"),
]


def repair_photograph_captions():
    """Captions naming one thing over a photograph of another.

    Ten of them, on seven public pages: a bedroom captioned "The pool" on the
    page where somebody decides whether to book; a bedroom captioned "The
    chateau at night" on a site that already carries a photograph of the
    chateau at night. Nothing errors -- both halves are individually valid,
    the file loads and the alt is present and non-empty -- so it survived
    every sweep until a check compared the two.

    The cause is arithmetic, not carelessness: thirty-five photographs cover
    the whole public site, reused six to twelve times each, so when a slot
    needs a picture and the shelf holds five bedrooms, a bedroom goes in and
    the caption stays whatever the slot was for.

    Corrected once and reverted whole by the very next export, identically,
    because the design side's tree does not have the corrections in it.
    """
    done = 0
    for rel, find, repl, why in CAPTION_FIXES:
        src = _read(rel)
        # Keyed on what is WRONG, never on whether the right photograph
        # appears somewhere in the file. Three of these were skipped on the
        # first run for being "already right": their substitute is used
        # elsewhere on the same page -- Kitchen+1.JPG is in the band two
        # sections down -- and "is the correct picture present anywhere" is
        # not the same question as "is it in this slot".
        n = src.count(find)
        if n == 0:
            continue          # corrected already, or the section is rebuilt
        if n > 1:
            print("  ! %s: this matches in more than one place, so it cannot "
                  "be placed safely (%s)" % (rel, why))
            continue
        _write(rel, src.replace(find, repl, 1))
        done += 1
    return done


def repair_media_query_variables():
    """Forty-two blocks of phone layout the browser has never once applied.

    A custom property cannot be used in a media query CONDITION: they resolve
    at computed-value time, long after the query has been evaluated. So
    @media (max-width: var(--m-read)) is discarded silently and everything
    inside it never runs — the navigation label, the back-to-top position,
    full-width buttons on the confirmation page.

    Put back twice by handovers, because the design side works from a tree
    that still has the variables in and every export carries them. Found by
    hand both times, which is why it kept coming back.

    Substituted inside @media only. Everywhere else the variable is correct
    and useful, and that is exactly what makes this hard to see.
    """
    import re
    rel = "static/gudanes.css"
    src = _read(rel)
    values = {"--m-tight": "26rem", "--m-read": "34rem"}
    media = re.compile(r"@media[^{]*\{")
    out, n, last = [], 0, 0
    for m in media.finditer(src):
        cond = fixed = m.group(0)
        for name, value in values.items():
            fixed = fixed.replace("var(%s)" % name, value)
        if fixed != cond:
            n += 1
        out.append(src[last:m.start()])
        out.append(fixed)
        last = m.end()
    out.append(src[last:])
    if n:
        _write(rel, "".join(out))
    return n


def repair_staging_noindex():
    """Without this the app is indexable wherever it is deployed.

    chateaugudanes.com is still Squarespace and this app sits on a public
    Railway URL, so an indexable copy competes with the house for the house's
    own name, using the house's own words and photographs. SITE_IS_LIVE
    decides; the wrapper in public_base.html is how a page asks.

    Reverted twice. The handover of 6 September took the code, having been
    generated from a snapshot predating the switch. The one of 7 September
    left the code alone and deleted only the paragraph above it explaining
    why the conditional is there — so this restores either half. Neither
    time did anything error.
    """
    rel = "templates/public_base.html"
    src = _read(rel)
    if "site_is_live" in src:
        # The code is here. Is the reason for it? A guard whose explanation
        # has been deleted reads as a stray conditional somebody left in,
        # which is exactly the thing the next person tidies away — and
        # tidying this one away makes a staging copy of the house indexable
        # under the house's own name, with nothing red anywhere to say so.
        if "competes with the house" in src:
            return 0
        head = _from_git(rel)
        if head is None or "competes with the house" not in head:
            return 0
        want = _comment_block(head, "competes with the house")
        here = _comment_block(src, "Each such page overrides this")
        if not want or not here:
            return 0
        _write(rel, src.replace("\n".join(here), "\n".join(want), 1))
        print("     (the guard was there; its reason was not)")
        return 1
    plain = "{% block robots %}{% endblock %}"
    if plain not in src:
        print("  ! public_base.html: no robots block to wrap")
        return 0
    _write(rel, src.replace(
        plain,
        "{% if site_is_live %}{% block robots %}{% endblock %}\n"
        '{%- else %}<meta name="robots" content="noindex, nofollow">{% endif %}',
        1))
    # And the reason, in the same run. Returning here meant a
    # handover that took both halves needed the tool run twice, and
    # nothing said so -- which is the worst version of that bug,
    # because the first run looks like it finished.
    return 1 + repair_staging_noindex()


def repair_parent_robots_block():
    """Without this the 24 child overrides below are dead markup."""
    rel = "templates/public_base.html"
    src = _read(rel)
    if "block robots" in src:
        return 0
    anchor = '<meta name="description" content='
    if anchor not in src:
        print("  ! public_base.html: no description meta to anchor to")
        return 0
    cut = src.index("\n", src.index(anchor)) + 1
    _write(rel, src[:cut] + "{% block robots %}{% endblock %}\n" + src[cut:])
    return 1


def repair_child_noindex():
    fixed = 0
    for name in NOINDEX_PAGES:
        rel = f"templates/{name}"
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        src = _read(rel)
        if ROBOTS in src:
            continue
        lines = src.split("\n")
        at = 1
        for i, line in enumerate(lines[:6]):
            if "{% block title %}" in line:
                at = i + 1
                break
        lines.insert(at, ROBOTS)
        _write(rel, "\n".join(lines))
        fixed += 1
    return fixed


def repair_workshop_payments():
    """The part-payment form and the auto-charge opt-out, from git."""
    rel = "templates/workshop_manage.html"
    src = _read(rel)
    if "part_amount" in src:
        return 0
    try:
        head = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", check=True).stdout.replace("\r\n", "\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  ! could not read the previous workshop_manage.html from git")
        return 0
    if "part_amount" not in head:
        print("  ! git's copy has no part-payment block either — repair by hand")
        return 0
    # Anchored on the markup, not on the comment above it. The comment was
    # dropped upstream and this line raised ValueError, which stopped the whole
    # script on repair 2 of 8 -- so the six after it silently never ran. A
    # repair tool that dies partway is worse than one that skips a step,
    # because the report still says what it managed before it fell over.
    start = head.rindex("\n    <form", 0, head.index('id="part_amount"')) + 1
    end = head.index("    {% endif %}", head.index('name="autocharge_opt_out"'))
    block = head[start:end + len("    {% endif %}\n")]
    pay = [l for l in src.split("\n") if "workshop_pay_balance" in l and "g-btn" in l]
    if len(pay) != 1:
        print(f"  ! {len(pay)} Pay balance buttons — cannot place the block safely")
        return 0
    at = src.index(pay[0]) + len(pay[0]) + 1
    _write(rel, src[:at] + "\n" + block + src[at:])
    return 1


def repair_table_wrappers():
    """A wide table has to scroll in its own box, not drag the page."""
    fixed = 0
    for name in ("guest_statement.html", "book_rooms.html"):
        rel = f"templates/{name}"
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        src = _read(rel)
        out, wrapped = [], 0
        for line in src.split("\n"):
            stripped = line.strip()
            indent = line[:len(line) - len(line.lstrip())]
            if stripped.startswith("<table"):
                # Already wrapped if the line above opened one.
                if out and 'class="table-wrap"' in out[-1]:
                    out.append(line)
                    continue
                out.append(f'{indent}<div class="table-wrap">')
                out.append("  " + line)
                wrapped += 1
            elif stripped == "</table>" and wrapped:
                out.append("  " + line)
                out.append(indent + "</div>")
            else:
                out.append(line)
        if wrapped:
            _write(rel, "\n".join(out))
            fixed += wrapped
    return fixed


def repair_hardcoded_markets():
    """Remove the hardcoded market list from whats_on.html.

    Three handovers have now shipped a "Which Market, Which Day" block in
    static HTML. The markets are already in the `whats_on` table, where the
    owner edits them and where t() translates them, so the page showed Les
    Cabannes and Tarascon twice — once in French, once not — and editing them on
    the admin page changed only one of the two.

    The block also names four markets the table does not carry: Foix, St Girons,
    Mirepoix and the farm shop at Les Cabannes. Those are worth having and are
    NOT invented here — a market day is a fact about the valley, not something
    this script should assert. They belong in the table with the other four,
    entered once by somebody who knows, after which this removal costs nothing.
    """
    rel = "templates/whats_on.html"
    src = _read(rel)
    marker = '<h2 class="g-place">Which Market, Which Day</h2>'
    if marker not in src:
        return 0
    # Cut the whole <section> the heading sits in, not just the heading.
    start = src.rindex("<section", 0, src.index(marker))
    end = src.index("</section>", start) + len("</section>")
    tail = src[end:]
    if tail.startswith(chr(10)):
        tail = tail[1:]
    _write(rel, src[:start] + tail)
    return 1


def repair_manage_booking_parameter():
    """url_for('manage_booking', token=...) — the route parameter is manage_token.

    A BuildError, not a bad link: the template raises when it renders, so EVERY
    room booking confirmation returns 500. That is the page a guest reaches
    immediately after paying, carrying their reference code and their manage
    link, so the failure lands on the one visitor least able to shrug it off.

    Shipped in final_25 on booking_confirmation.html line 100, fixed, and
    shipped again in final_27 on the same line. tests/test_links.py catches it
    either way; this saves the manual edit.
    """
    fixed = 0
    for name in os.listdir(os.path.join(ROOT, "templates")):
        if not name.endswith(".html"):
            continue
        rel = f"templates/{name}"
        src = _read(rel)
        wrong = "url_for('manage_booking', token="
        if wrong not in src:
            continue
        _write(rel, src.replace(wrong, "url_for('manage_booking', manage_token="))
        fixed += 1
    return fixed


def repair_plate_row_rule():
    """The .g-plate__row rule, which the stylesheet keeps arriving without.

    The markup has gained this wrapper in three separate handovers and the CSS
    has never come with it, so a dozen public pages lay out against a class
    with nothing behind it. It has one job: sit above .g-plate::before's inset
    rule, which is why position: relative is the whole of it.
    """
    rel = "static/gudanes.css"
    src = _read(rel)
    if ".g-plate__row{" in src:
        return 0
    anchor = ".g-plate__l{ display: grid;"
    if anchor not in src:
        print("  ! gudanes.css: no .g-plate__l rule to anchor to")
        return 0
    # Taken from git rather than written out here. The literal below said
    # `position: relative`, which was right when it was written and has since
    # been replaced on main by `min-width: 0` for a reason the comment beside
    # it gives. A repair that restores the older of two fixes is still a
    # revert, and this one had already put the stale rule back once.
    rule = None
    try:
        head = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", check=True).stdout.replace("\r\n", "\n")
        at = head.index(".g-plate__row{")
        start = head.rindex("/*", 0, at) if "/*" in head[max(0, at - 400):at] else at
        rule = head[start:head.index("\n", at) + 1]
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    if not rule:
        print("  ! could not read the current .g-plate__row rule from git")
        return 0
    _write(rel, src.replace(anchor, rule + anchor, 1))
    return 1


def repair_privacy_link():
    rel = "templates/public_base.html"
    src = _read(rel)
    dead = "<a href=\"#\">{{ t('Privacy Policy') }}</a>"
    if dead not in src:
        return 0
    _write(rel, src.replace(
        dead, "<a href=\"{{ url_for('privacy_page') }}\">{{ t('Privacy Policy') }}</a>", 1))
    return 1


def repair_extra_prefill():
    """Put the ticked extras back on the room booking form.

    The eighth of these. book_room.html renders each extra as a checkbox and
    the handover arrives with the `checked` expression stripped off the input,
    so a guest who picks the airport transfer, hits a validation error or backs
    out of the card page, loses it silently and pays for a taxi instead. Guarded
    by test_booking_form_errors and test_abandoned_checkout, both of which went
    red on final_28 — and both of which cover work the handover reverted from
    the same author who wrote it.
    """
    rel = "templates/book_room.html"
    src = _read(rel)
    opening = "<input type=\"checkbox\" id=\"extra_{{ e['id'] }}\" name=\"extras\" value=\"{{ e['id'] }}\""
    stripped = opening + ">"
    if stripped not in src:
        return 0
    checked = "{{ 'checked' if e['id'] in (prefill_extras | default([])) }}"
    _write(rel, src.replace(stripped, opening + "\n" + " " * 25 + checked + ">", 1))
    return 1


def repair_event_date_prefill():
    """Put the typed dates back on the event enquiry form.

    Same fault as repair_extra_prefill, on a different form, and the tenth
    handover brought it: `value="{{ prefill_preferred_date | default('') }}"`
    is stripped from both date inputs, so somebody proposing a wedding gets a
    validation error and finds the two dates they chose have been emptied.
    Nothing errors; the page just quietly forgets.

    Guarded by test_form_prefill, which is what went red on final_31 —
    check_handover.py named the commit ("Keep what the guest typed") before the
    suite ran, which is the order that saves the manual pass.
    """
    rel = "templates/events_info.html"
    src = _read(rel)
    fixed = 0
    for field in ("preferred_date", "alternate_date"):
        stripped = f'<input type="date" id="{field}" name="{field}">'
        if stripped not in src:
            continue
        src = src.replace(
            stripped,
            f'<input type="date" id="{field}" name="{field}" '
            f"value=\"{{{{ prefill_{field} | default('') }}}}\">", 1)
        fixed += 1
    if fixed:
        _write(rel, src)
    return fixed


# ---------------------------------------------------------------------------
# Added after four handovers of repairing each of these by hand. The rule this
# file works to is that a thing goes in once it has been reverted more than
# once; every one below is on its second to fourth time.
# ---------------------------------------------------------------------------

# Endpoint names the design side keeps guessing. Each is a BuildError on a
# public page, which is a 500 rather than a wrong link.
# The line ending the templates in this tree use.
NEWLINE = "\r\n"

WRONG_ENDPOINTS = {
    # The newsletter route has always been newsletter_subscribe. `subscribe`
    # arrives on the journal form and on the new waiting-list form.
    "url_for('subscribe')": "url_for('newsletter_subscribe')",
    # Four handovers running.
    "url_for('contact')": "url_for('contact_page')",
    # The public gallery is gallery_page; admin_gallery is the other one. Arrived
    # on the photoshoots page and on social.html, and 500s the page rather than
    # merely mislinking it.
    "url_for('gallery')": "url_for('gallery_page')",
    # guest_portal takes a token, and there is none to give at the point in
    # the page this link sits. find_booking is where somebody holding only an
    # email address can actually reach their stays.
    "url_for('guest_portal')": "url_for('find_booking')",

    # The valley page is facilities_page; `facilities` is the TEMPLATE's
    # name, not the route's. Unguarded, so it is a 500 on the arrival card
    # rather than merely a wrong link.
    "url_for('facilities')": "url_for('facilities_page')",
}


def repair_endpoint_names():
    fixed = 0
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if not name.endswith(".html"):
            continue
        rel = "templates/" + name
        src = _read(rel)
        new = src
        for wrong, right in WRONG_ENDPOINTS.items():
            new = new.replace(wrong, right)
        if new != src:
            _write(rel, new)
            fixed += 1
    return fixed


def repair_decorative_svgs():
    """An svg with no aria-hidden is read out to a screen reader.

    The drawn marks and icons arrive without it every time, because the
    keyboard pass that added it is newer than the working copy they are cut
    from. Adding the attribute rather than dropping the mark: the marks are
    the design work and they are worth keeping.
    """
    fixed = 0
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if not name.endswith(".html"):
            continue
        rel = "templates/" + name
        src = _read(rel)
        # Public templates only, by what they extend — the same definition
        # test_accessibility uses, so the tool and the check that grades it
        # cannot disagree about which pages these are. base.html mentions
        # public_base.html without being it, and the staff app's icons are not
        # a handover's business.
        if not ('extends "public_base.html"' in src or name in PUBLIC_ROOTS):
            continue
        def _hide(mo):
            tag = mo.group(0)
            if re.search(r'aria-hidden|aria-label|role\s*=\s*"img"', tag, re.I):
                return tag
            return tag[:-1].rstrip() + ' aria-hidden="true" focusable="false">'
        new = re.sub(r"<svg\b[^>]*>", _hide, src, flags=re.I)
        if new != src:
            _write(rel, new)
            fixed += 1
    return fixed


def repair_plan_col_rule():
    """.g-plan__col, dropped from the stylesheet while events_info uses it."""
    rel = "static/gudanes.css"
    src = _read(rel)
    if ".g-plan__col{" in src:
        return 0
    head = _from_git(rel)
    if not head:
        return 0
    lines = head.split("\n")
    i = next((k for k, l in enumerate(lines) if l.startswith(".g-plan__col{")), None)
    if i is None:
        return 0
    block = lines[i - 3:i + 1] if lines[i - 3].lstrip().startswith("/*") else [lines[i]]
    out = src.split("\n")
    j = next((k for k, l in enumerate(out) if l.startswith(".g-plan__cols{")), None)
    if j is None:
        return 0
    out[j + 1:j + 1] = block
    _write(rel, "\n".join(out))
    return 1


def repair_media_query_variables():
    """Forty-two @media rules whose condition is a custom property.

    A custom property CANNOT be used in a media query condition: they are
    resolved at computed-value time, long after the query has been evaluated,
    so the browser discards the whole rule. Forty-two blocks of phone layout —
    the navigation label, the back-to-top position, full width buttons on the
    confirmation page — never once applied, and a discarded @media reports
    nothing: no error, no console warning, and the page renders perfectly at
    the desktop size anyone checks it at.

    THIS IS HERE BECAUSE ASKING HAS FAILED TWICE. It came back in the handover
    of 2026-09-05 and again on 2026-09-07. The design side works from a tree
    that still has the variables in, so every zip carries them back, and a
    note in the CSS is not read by whatever generates the zip. Repairing it is
    cheaper than the fourth conversation about it.

    Only the CONDITION is touched — everything between @media and the opening
    brace. The variables are correct, and wanted, in ordinary declarations.
    """
    rel = "static/gudanes.css"
    src = _read(rel)
    values = {}
    for name in ("--m-tight", "--m-read", "--m-wide"):
        found = re.search(re.escape(name) + r":\s*([0-9.]+[a-z%]+)\s*;", src)
        if found:
            values[name] = found.group(1)
    if not values:
        return 0

    def one(match):
        cond = match.group(0)
        for name, literal in values.items():
            cond = cond.replace("var(%s)" % name, literal)
        return cond

    out, _n = re.subn(r"@media[^{]*", one, src)
    if out == src:
        return 0
    _write(rel, out)
    return 1


def repair_panel_heading_rule():
    """.g-panel__h is used on the manage pages and defined nowhere.

    The promotion from h3 to h2 is a real fix — h1 straight to h3 is a heading
    skip — so the class is given the rule its h3 already had rather than a new
    look invented here. The page reads identically; only the outline changes.
    """
    rel = "static/gudanes.css"
    src = _read(rel)
    if ".g-panel__h" in src:
        return 0
    old = (".g-mb-card h3{\n"
           "  font-family: var(--display); font-weight: 400; font-size: 22px;\n"
           "  color: var(--blue-deep); margin: 0 0 var(--s4);\n}")
    if old not in src:
        return 0
    _write(rel, src.replace(old, ".g-mb-card h3,\n.g-mb-card .g-panel__h{\n"
                            "  font-family: var(--display); font-weight: 400; font-size: 22px;\n"
                            "  color: var(--blue-deep); margin: 0 0 var(--s4);\n}"))
    return 1


def repair_featured_reviews():
    """The house's own reviews, on both pages that show them.

    The booking and atelier pages are handed these on every load. Without the
    block they render none of them, so "Feature on booking page" is a button
    with no effect — which is how it sat for months before anybody noticed.
    """
    fixed = 0
    for rel, anchor in (("templates/book_rooms.html", "What Guests Say"),
                        ("templates/workshops_public.html", None)):
        src = _read(rel)
        if "featured_reviews" in src:
            continue
        head = _from_git(rel)
        if not head or "featured_reviews" not in head:
            continue
        block = _jinja_block(head, "{% if featured_reviews %}")
        if not block:
            continue
        lines = src.split("\n")
        if anchor:
            i = next((k for k, l in enumerate(lines) if anchor in l), None)
            at = None if i is None else i + 1
        else:
            i = next((k for k, l in enumerate(lines)
                      if "g-close" in l and "<section" in l), None)
            at = i
        if at is None:
            continue
        lines[at:at] = [""] + block
        _write(rel, "\n".join(lines))
        fixed += 1
    return fixed


def repair_unavailable_reason():
    """WHY a room cannot be booked, which was worked out and thrown away.

    "Requires a 3-night minimum" is a booking a guest can still make.
    "Not available" reads as sold out and loses it.
    """
    rel = "templates/book_rooms.html"
    src = _read(rel)
    flat = '<span class="g-btn g-btn--off g-card__cta">Not available these dates</span>'
    if flat not in src:
        return 0
    _write(rel, src.replace(flat,
        '<span class="g-btn g-btn--off g-card__cta">\n'
        "              {{ unavailable_reason.get(room['id'], 'Not available these dates') }}\n"
        "            </span>"))
    return 1


def repair_under_18_field():
    """The booking form's second guest count, by the name the return reads.

    Under-18s are exempt from the taxe de sejour and the figure is declared to
    the commune. The handover calls the field `children`, which nothing on the
    app side has ever read, so the count silently becomes zero for every
    booking and the return understates the exemption.
    """
    rel = "templates/book_room.html"
    src = _read(rel)
    if 'name="children"' not in src and "taxe de s" in src.lower():
        return 0                          # named right AND the reason is given
    # Whatever the field is called this round, the value has to come from the
    # name the route actually passes, or the number is lost on every
    # validation error without anything erroring.
    new = (src.replace("{{ prefill_guests_under_18 or prefill_children or 0 }}",
                       "{{ prefill_under_18 or 0 }}")
              .replace("{{ prefill_children or 0 }}", "{{ prefill_under_18 or 0 }}")
              .replace('name="children"', 'name="guests_under_18"')
              .replace("value=\"{{ prefill_children or 0 }}\">",
                       "value=\"{{ prefill_under_18 or 0 }}\">")
              .replace('<label for="br_children">Children</label>',
                       '<label for="br_children">Children (under 18)</label>'))
    if "taxe de s" not in new.lower():
        new = new.replace(
            "adults and children together.",
            "adults and children together. Under-18s are exempt from the "
            "taxe de sejour, so the count is what keeps a family from being "
            "overcharged and the commune's return right.")
    _write(rel, new)
    return 1


# The four guest pages the handovers ship an older copy of every single time,
# because the zip carries their working copy of every file it has ever
# touched rather than only what changed since the last one. Four rounds, four
# identical reverts each.
#
# NOT generalised to "any template that adds nothing", tempting as that is.
# A file the design side deliberately trimmed — a duplicate paragraph removed,
# say — looks exactly like a revert to that rule, and undoing real editing is
# a worse failure than repeating a repair. check_handover.py reports the
# general case; this puts back the four that have earned it.
REVERTED_PAGES = (
    "templates/guest_account.html",      # the whole bill loop
    "templates/guest_statement.html",    # row headers, and the print actions
    "templates/manage_booking.html",     # the bill row with its remove control
    "templates/workshop_manage.html",    # the labelled session select
)


def repair_event_promo_field():
    """The promo code box on the event enquiry form.

    submit_event_inquiry reads request.form["promo_code"] and stores it so the
    owner can honour a code when they quote. Without the field the route reads a
    parameter no form sends -- the read-and-never-written shape one level up --
    and it fails silently, because an enquiry with no code is a perfectly
    ordinary enquiry.

    The suite did not catch it either when a handover dropped it: the test posts
    to the route rather than checking the page carries the field. That check
    exists now, and so does this.
    """
    rel = "templates/events_info.html"
    src = _read(rel)
    if 'name="promo_code"' in src:
        return 0
    marker = '<textarea id="message" name="message"'
    at = src.find(marker)
    if at == -1:
        return 0
    # The end of the paragraph the textarea sits in, which is where the new
    # field goes. Found rather than reconstructed: the surrounding markup is the
    # designer's and changes shape between handovers.
    close = src.find("</p>", at)
    if close == -1:
        return 0
    close += len("</p>")
    nl = "\r\n" if "\r\n" in src else "\n"
    block = nl.join([
        "",
        "        {# Captured, not validated. There is no price on an enquiry yet",
        "           to take a discount off, and refusing an enquiry because a",
        "           code was mistyped would lose a wedding over a typo. The",
        "           house sees what was claimed and applies it when it quotes. #}",
        '        <p class="g-field">',
        '          <label for="promo_code">Promo code (optional)</label>',
        '          <input type="text" id="promo_code" name="promo_code"',
        '                 autocomplete="off"',
        "                 value=\"{{ prefill_promo_code | default('') }}\">",
        '          <span class="g-hint">If you have been given one, we will',
        "          apply it to your quote.</span>",
        "        </p>",
    ])
    _write(rel, src[:close] + block + src[close:])
    return 1


def repair_admin_images_page():
    """The two things the image manager arrives with, both rounds so far.

    THE ENDPOINT. It names url_for('admin_image_upload') inside a guard --
    `if 'admin_image_upload' in url_map` -- and the guard does not raise. Jinja
    is content to say a name it has never heard of contains nothing, so it
    always answers no, url_map not being anything a template is given. The
    branch naming the endpoint is therefore dead in every single render, and
    the page posts to the hardcoded path in the else instead.

    That is worse than a crash. The route exists now, and the day it moves the
    page would go on posting to the old path with nothing at all to say so. So
    the repair is to name the endpoint and let Flask resolve it.

    THE FILE INPUT. Hidden, clicked by the slot around it, and with no name of
    its own. The slot IS labelled, so nobody using the page is stuck -- but the
    accessibility sweep reads the input, not the intent, and it is right to:
    the day somebody drops the `hidden`, an unlabelled file field goes live.
    """
    rel = "templates/admin_images.html"
    if not os.path.exists(os.path.join(ROOT, rel)):
        return 0
    src = _read(rel)
    fixed = 0

    start = src.find("var ENDPOINT =")
    end = src.find(";", start) if start != -1 else -1
    # Only where the dead guard actually is. Matching on the endpoint name
    # alone would keep rewriting the correct line, and a repair that reports
    # work against a clean tree is one nobody can read.
    if start != -1 and end != -1 and "url_map" in src[start:end]:
        good = 'var ENDPOINT = "{{ url_for(' + "'admin_image_upload'" + ') }}"'
        src = src[:start] + good + src[end:]
        fixed += 1

    marker = '<input type="file" accept="image/*" hidden data-slot-input'
    at = src.find(marker)
    if at != -1 and "aria-label" not in src[at:at + 200]:
        close = src.find(">", at)
        if close != -1:
            src = (src[:close] + ' aria-label="Choose a picture for '
                   '{{ slot.label }}"' + src[close:])
            fixed += 1

    if fixed:
        _write(rel, src)
    return fixed


def repair_url_map_guards():
    """The guard that never fires, and the pages nobody could reach.

    The sketches wrap a link in `{% if 'X' in url_map %}` to mean "only if that
    page is built yet". It cannot work. url_map is not in the template context,
    so Jinja reports that a name it has never heard of contains nothing, the
    test is false in EVERY render, and the link takes its fallback forever.

    Nothing errors and every page looks right, which is why it survived nine
    rounds. Weddings, Private, Photoshoots and Press are all real, dedicated
    pages, and not one link in the public nav or footer pointed at any of them.

    BOTH halves are repaired, and the second half is the one that took a second
    round to learn:

      the page EXISTS  -> name the endpoint, which is what the guard reached for
      the page does NOT -> collapse to the fallback it has always silently
                           taken, because a guard that cannot fire is not a
                           guard, it is a note claiming this might light up one
                           day when it never will
    """
    import re as _re
    app_src = _read("app.py")
    real = set(_re.findall(r"^def (\w+)\(", app_src, _re.M))
    fixed = 0
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if not name.endswith(".html"):
            continue
        rel = "templates/" + name
        src = _read(rel)
        if "in url_map" not in src:
            continue
        before = src

        def pick(mo):
            ep, fallback = mo.group(1), mo.group(2)
            return ("{{ url_for('%s') }}" % ep) if ep in real else fallback

        # {% if 'X' in url_map %}{{ url_for('X') }}{% else %}FALLBACK{% endif %}
        src = _re.sub(
            r"\{%\s*if\s*'(\w+)'\s*in url_map\s*%\}\{\{\s*url_for\('\1'\)\s*\}\}"
            r"\{%\s*else\s*%\}(.*?)\{%\s*endif\s*%\}", pick, src)
        # {{ url_for('X') if 'X' in url_map else FALLBACK }}
        src = _re.sub(
            r"\{\{\s*url_for\('(\w+)'\)\s*if\s*'\1'\s*in url_map\s*else\s*([^}]*)\}\}",
            lambda mo: pick(mo) if mo.group(1) in real
            else "{{ " + mo.group(2).strip() + " }}", src)

        if src != before:
            _write(rel, src)
            fixed += 1
    return fixed


def _rename_cols(text, renames):
    """Subscript renames, only where the variable is a booking or guest row."""
    for wrong, right in renames:
        for var in ("b", "g", "booking", "stay"):
            text = text.replace(var + wrong, var + right)
    return text


def repair_invented_columns():
    """Column names the sketches use that the database does not have.

    b['reference'], b['arrival'], b['departure'] and g['reference'] appear
    across the guest partials. None exists. A missing key on a sqlite3.Row
    RAISES rather than returning empty, so each one is a 500 on the page a
    guest reads after paying -- not a blank line.

    Renames only. b['balance_due'] is the same class of fault and is NOT here,
    because the right answer is an expression rather than another column name,
    and a repair script guessing at arithmetic is worse than a red test.

    NAMED FILES, not every template, and this is the whole care of it. The
    first version of this swept templates/ and renamed b['reference'] on
    management_cash_banking.html -- where b is a cash_bankings row, which HAS a
    reference column and has no reference_code. That is the same 500 this
    function exists to prevent, introduced by the function. The suite stayed
    green because the fixture banks nothing, so the row markup never rendered.

    It also rewrote the comments that document the wrong names, which turned
    "the sketch used b['arrival']" into a sentence about the correct name and
    threw away the reason anybody wrote it down.
    """
    import re as _re
    RENAMES = (
        ("['reference']", "['reference_code']"),
        ("['arrival']", "['arrival_date']"),
        ("['departure']", "['departure_date']"),
    )
    GUEST_PARTIALS = ("_guest_timeline.html", "_print_stay.html",
                      "_linked_bookings.html", "_sharelink.html")
    fixed = 0
    for name in GUEST_PARTIALS:
        rel = "templates/" + name
        if not os.path.exists(os.path.join(ROOT, rel)):
            continue
        src = _read(rel)
        before = src
        out, i = [], 0
        while i < len(src):
            # Leave Jinja comments alone: they are where the wrong names are
            # written down on purpose.
            start = src.find("{#", i)
            if start == -1:
                out.append(_rename_cols(src[i:], RENAMES)); break
            close = src.find("#}", start)
            close = len(src) if close == -1 else close + 2
            out.append(_rename_cols(src[i:start], RENAMES))
            out.append(src[start:close])
            i = close
        src = "".join(out)
        if src != before:
            _write(rel, src)
            fixed += 1
    return fixed


def repair_room_name_map():
    """The French room names, which are NOT landed, and arrive every round.

    room_name() maps 'Suite with Mountain View' to 'Chambre Emeraude' and so on
    for all five. It is careful work and may well be what the house wants. But a
    room's name is DATA, and renaming it in a template renames it in only some
    of the places a guest sees it.

    Concretely: confirm_booking_by_id builds the confirmation email from
    room['name'] straight out of the database. Land the map alone and a guest
    books Chambre Emeraude, then gets an email about a King Room with Mountain
    View -- and so do their bill, their statement and their arrival card.

    Renaming the rooms properly is one UPDATE on the rooms table, after which
    every page and every email agrees without a map anywhere. Until somebody
    does that, this passes the database name through and keeps the intended
    names in a comment so the work is not lost.
    """
    rel = "templates/_room_copy.html"
    if not os.path.exists(os.path.join(ROOT, rel)):
        return 0
    src = _read(rel)
    start = src.find("{% macro room_name(db_name) -%}")
    if start == -1:
        return 0
    end = src.find("{%- endmacro %}", start)
    if end == -1:
        return 0
    end += len("{%- endmacro %}")
    body = src[start:end]
    if "Chambre" not in body:
        return 0                      # already passing the database name through

    keep = []
    for line in body.split("\n"):
        stripped = line.strip()
        if "':" in stripped and "'" in stripped:
            # 'Suite with Mountain View': 'Chambre Emeraude',
            left, _, right = stripped.partition("':")
            frm = left.lstrip("'")
            to = right.strip().strip(",").strip("'")
            if frm and to:
                keep.append("    %-34s -> %s" % (frm, to))

    NEW = [
        "{# The house's French room names, NOT LANDED, and deliberately so.",
        "",
        "   A room's name is DATA, not presentation. confirm_booking_by_id builds",
        "   the confirmation email from room['name'] out of the database, so",
        "   landing this map alone means a guest books one name and is written to",
        "   about another -- and so are their bill, statement and arrival card.",
        "",
        "   Renaming the rooms properly is one UPDATE on the rooms table, after",
        "   which every page and every email agrees without a map anywhere. #}",
        "{% macro room_name(db_name) -%}",
        "{#- The intended names, for whoever lands them:",
    ] + keep + [
        "  -#}",
        "{{- db_name -}}",
        "{%- endmacro %}",
    ]
    _write(rel, src[:start] + "\n".join(NEW) + src[end:])
    return 1


def repair_reverted_guest_pages():
    """Report the four pages the handovers keep shipping an older copy of.

    It does NOT edit them, and the reason is worth writing down. What these
    arrive with is not new markup — it is the older version of a line main has
    since improved: guest_account's bill figure without the balance beside it,
    guest_statement's total as a bold cell rather than a row header,
    manage_booking's bill row without its remove control, workshop_manage's
    select without the label. To a rule those read as additions, because they
    are lines main does not have. To a person they read as what they are.

    Every other repair in this file is mechanical: a name is wrong, a rule is
    missing, an attribute is absent. This one is a judgement about intent, and
    check_handover.py's docstring says where that belongs. So it names them and
    stops — four `git checkout` commands with a person deciding, rather than a
    script quietly discarding design work the day they genuinely edit one.
    """
    stale = []
    for rel in REVERTED_PAGES:
        head = _from_git(rel)
        if head is None:
            continue
        here = [l.strip() for l in _read(rel).split("\n") if l.strip()]
        there = [l.strip() for l in head.split("\n") if l.strip()]
        if here != there:
            stale.append(rel)
    if stale:
        print("  ! these four arrive as an older copy every time. Read them, then:")
        for rel in stale:
            print("        git checkout HEAD -- %s" % rel)
    return 0


# ---------------------------------------------------------------------------
# The seven put back after the 1 October handovers (g, k), and again on 2 Oct.
# Every zip this week is built on 8114e1c, before any of these existed, so each
# one arrives without them. Each is keyed on the FAULT being present, and puts
# back the exact text main carries, so a file the design side did not touch
# comes out of install-and-repair byte-identical to main.
# ---------------------------------------------------------------------------

REVEAL_IMPORT = (
    "{# Put back after the 1 October handovers (g, k): the_reveal is still called below, "
    "and its import went out with the explorer's. #}\n"
    "{% from '_interactive.html' import the_reveal %}\n")


def repair_reveal_import():
    """/restoration answered 500: the slider is called, and its import was cut
    beside the explorer's (which the design did mean to cut)."""
    rel = "templates/restoration.html"
    src = _read(rel)
    if "the_reveal(" not in src or "import the_reveal" in src:
        return 0
    anchor = "{% from '_floorplan.html' import floorplan %}\n"
    if anchor not in src:
        raise ValueError("no floorplan import to put the_reveal's beside")
    _write(rel, src.replace(anchor, anchor + REVEAL_IMPORT, 1))
    return 1


WORKSHOP_TERMS_KEYS = '    {# Put back after the 1 October handovers (g, k), which cut the facts list these lived in.\n       Both are the code\'s, not copy: the rooms line is what the registration page says\n       (two to a room; no extra beds, per the owner\'s facts of 1 October), and the deposit\n       and balance are read from the workshops and from WORKSHOP_BALANCE_DAYS by the route.\n       Inside the balance window the WHOLE amount is due at booking, and a page that says\n       "10% to reserve" three weeks out has told a guest one figure and charged another.\n       Reverted by handovers three times. #}\n    <div class="g-key__i">\n      <p class="g-key__k">Rooms</p>\n      <p class="g-key__v">Arranged for two. Coming alone, you share with another guest travelling solo, or take a room to yourself with a single supplement</p>\n    </div>\n    <div class="g-key__i">\n      <p class="g-key__k">To reserve</p>\n      <p class="g-key__v">{% if deposit_pct %}{{ deposit_pct }}% to reserve; the balance {{ balance_days }} days before.{% else %}It depends on the workshop &mdash; the figure is on the session you choose.{% endif %}{% if balance_days %} Booking inside {{ balance_days }} days? The whole amount is due then, not a deposit.{% endif %}</p>\n    </div>\n'


def repair_workshop_terms_keys():
    """The Workshops page's deposit, 30-day balance and whole-amount-inside-
    the-window line, and how rooms are arranged. Cut with the facts list they
    lived in; read from the route, so they are the code's, not copy. Put back
    beside whichever grid the zip carries: the old included grid, or the Key
    Information grid that replaced it on 3 October."""
    rel = "templates/workshops_public.html"
    src = _read(rel)
    if "deposit_pct" in src:
        return 0
    anchor = ('      <p class="g-key__v">Included, except on the workshops spent at the house</p>\n'
              '    </div>\n')
    if anchor in src:
        _write(rel, src.replace(anchor, anchor + WORKSHOP_TERMS_KEYS, 1))
        return 1
    # 3 October (2oct-k): the included grid is gone, and a "Key Information"
    # grid carries the design's own Rooms line. Only the money goes back, right
    # after that line: a second Rooms item would say the same thing twice.
    head = src.find('<h2 class="g-place">Key Information</h2>')
    rooms = src.find('<p class="g-key__k">Rooms</p>', head) if head >= 0 else -1
    end = src.find("    </div>\n", rooms) if rooms >= 0 else -1
    if end < 0:
        raise ValueError("neither the included grid nor Key Information's Rooms item "
                         "is there to carry the deposit line")
    end += len("    </div>\n")
    _write(rel, src[:end] + TO_RESERVE_KEY + src[end:])
    return 1


TO_RESERVE_KEY = (
    "    {# Put back after every handover since 1 October. The deposit and the balance are read\n"
    "       from the workshops and from WORKSHOP_BALANCE_DAYS by the route, and inside the balance\n"
    "       window the WHOLE amount is due at booking: a page that says \"10% to reserve\" three\n"
    "       weeks out has told a guest one figure and charged another. #}\n"
    "    <div class=\"g-key__i\">\n"
    "      <p class=\"g-key__k\">To reserve</p>\n"
    "      <p class=\"g-key__v\">{% if deposit_pct %}{{ deposit_pct }}% to reserve; the balance "
    "{{ balance_days }} days before.{% else %}It depends on the workshop &mdash; the figure is on "
    "the session you choose.{% endif %}{% if balance_days %} Booking inside {{ balance_days }} days? "
    "The whole amount is due then, not a deposit.{% endif %}</p>\n"
    "    </div>\n")


BATHROOM_UNGUARDED = ("          {% if room['amenities'] %}\n"
                      "            {% for tag in room['amenities'].split(',') %}"
                      "<li>{{ tag.strip() }}</li>{% endfor %}\n")
BATHROOM_GUARDED = '          {% if room[\'amenities\'] %}\n            {#- Put back after the 1 October handovers (g, k). Free-text amenities may override the\n                derived list, but never the bathroom: shared-or-private is the one claim on this\n                card that costs an apology when it is wrong, and imported amenities on two rooms\n                still said "Shared bathroom" after every room became private. The column decides. -#}\n            {% for tag in room[\'amenities\'].split(\',\') if \'bathroom\' not in tag|lower %}<li>{{ tag.strip() }}</li>{% endfor %}\n            {% if room[\'bathroom\'] %}<li>{{ room[\'bathroom\']|capitalize }} bathroom</li>{% endif %}\n'


def repair_bathroom_follows_column():
    """Free-text amenities may replace the derived tags, never the bathroom:
    shared-or-private is the claim that costs an apology when it is wrong."""
    rel = "templates/book_rooms.html"
    src = _read(rel)
    if BATHROOM_UNGUARDED not in src:
        return 0
    _write(rel, src.replace(BATHROOM_UNGUARDED, BATHROOM_GUARDED, 1))
    return 1


ARRIVAL_CARD_NOTE = (
    "{# with context: the card reads house_maps from the context processor, and imported without it the\n"
    "   'Open in maps' link never rendered on any confirmation (found after the 1 October (g) handover). #}\n")


def repair_arrival_card_context():
    """The card reads house_maps; imported without context it never drew the
    'Open in maps' link on any confirmation."""
    rel = "templates/booking_confirmation.html"
    src = _read(rel)
    bare = "{% from '_arrival_card.html' import arrival_card %}"
    if bare not in src:
        return 0
    _write(rel, src.replace(
        bare, ARRIVAL_CARD_NOTE + "{% from '_arrival_card.html' import arrival_card with context %}", 1))
    return 1


AFTER_DARK = ("The last four kilometres climb and are unlit: if you are arriving after dark, "
              "telephone when you leave Les Cabannes and someone will meet you at the gates")


def repair_after_dark_line():
    """Contact's only rendered instruction for arriving after dark went with
    its map. The design's own sentence for it, in the By car line."""
    rel = "templates/contact.html"
    src = _read(rel)
    if "telephone when you leave Les Cabannes" in src:
        return 0
    old = "The last stretch climbs; take it slowly after dark</p>"
    if old in src:
        _write(rel, src.replace(old, AFTER_DARK + "</p>", 1))
        return 1
    m = re.search(r'(<p class="g-key__k">By car</p>\s*<p class="g-key__v">)(.*?)(</p>)', src, re.S)
    if not m:
        raise ValueError("no By car line to carry the after-dark instruction")
    value = m.group(2).rstrip(". ") + ". " + AFTER_DARK[0].upper() + AFTER_DARK[1:]
    _write(rel, src[:m.start(2)] + value + src[m.end(2):])
    return 1


LEGAL_HOST_OLD = ('    <div><dt>Host</dt><dd>Railway Corp., San Francisco, California, United States \u00b7 '
                  '<a href="https://railway.com" rel="noopener">railway.com</a></dd></div>')
LEGAL_HOST_NEW = (
    "    {# Railway's own wording, from railway.com/legal/privacy (1 Oct 2026). "
    "LCEN asks for the host's name, address and telephone. #}\n"
    '    <div><dt>Host</dt><dd>Railway Corporation, 548 Market St PMB 68956, San Francisco, California 94104, '
    'United States \u00b7 +1 415 707 7675 \u00b7 <a href="https://railway.com" rel="noopener">railway.com</a></dd></div>')
LEGAL_RCS = (
    '    {# LCEN 6-III also asks for the trade-register entry: "RCS Foix" and the SIREN, '
    "which is the SIRET's first nine digits. #}\n"
    "    {% if company and company['registration_office'] %}\n"
    "    {% set _siren = (company['registration_number'] or '')|replace(' ', '') %}\n"
    "    <div><dt>Trade register</dt><dd>{{ company['registration_office'] }}{% if _siren|length >= 9 %} "
    "{{ _siren[:3] }} {{ _siren[3:6] }} {{ _siren[6:9] }}{% endif %}</dd></div>\n"
    "    {% endif %}\n")


def repair_legal_notice():
    """The host in full (name, address, telephone) and the RCS line, which
    LCEN 6-III asks for and the design's page leaves out."""
    rel = "templates/legal.html"
    src = _read(rel)
    n = 0
    if "548 Market St" not in src and LEGAL_HOST_OLD in src:
        src = src.replace(LEGAL_HOST_OLD, LEGAL_HOST_NEW, 1)
        n += 1
    if "Trade register" not in src:
        vat = "    {% if company and company['vat_number'] %}"
        if vat not in src:
            raise ValueError("no VAT row to put the RCS line above")
        src = src.replace(vat, LEGAL_RCS + vat, 1)
        n += 1
    if n:
        _write(rel, src)
    return n


PRIVACY_A = "<a href=\"{{ url_for('privacy_page') }}\">{{ t('Privacy Policy') }}</a>"
LEGAL_A = "<a href=\"{{ url_for('legal_page') }}\">{{ t('Legal Notice') }}</a>"


def repair_legal_footer_links():
    """The Legal Notice, linked beside every privacy link the base carries --
    the footer's Help list and the drawer's legal row. Keyed on the privacy
    link itself rather than on the markup around it, so a redrawn footer
    still gets one, in the same shape as its neighbour: a list item beside a
    list item, a bare link on the next line beside a bare link."""
    rel = "templates/public_base.html"
    src = _read(rel)
    if "url_for('legal_page')" in src:
        return 0
    out, n = [], 0
    for line in src.split("\n"):
        out.append(line)
        if PRIVACY_A not in line:
            continue
        indent = line[:len(line) - len(line.lstrip())]
        if line.strip() == "<li>" + PRIVACY_A + "</li>":
            out.append(indent + "<li>" + LEGAL_A + "</li>")
        elif line.strip() == PRIVACY_A:
            out.append(indent + LEGAL_A)
        else:
            out[-1] = line.replace(PRIVACY_A, PRIVACY_A + " " + LEGAL_A, 1)
        n += 1
    if not n:
        raise ValueError("no privacy link to put the legal link beside")
    _write(rel, "\n".join(out))
    return n


# Claims about the house the owner has corrected, in the design side's copy.
# The design side writes from its own fact ledger, which can lag the owner, so
# a correction made here would arrive reverted in the next whole-file zip.
# Each is a whole word, fixed only outside comments: a design note that
# mentions the word is a record of what was said, and rewriting it would
# make the record false.
COPY_CORRECTIONS = [
    # 2 October: no padel court. The tennis court is lined for pickleball.
    ("padel", "pickleball"),
]
_COMMENT_SPLIT = re.compile(r"(\{#.*?#\}|<!--.*?-->)", re.S)


def repair_corrected_claims():
    """Owner-corrected claims, rewritten in the copy wherever a zip puts
    them back. test_confirmed_facts names any that get past this."""
    n = 0
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if not name.endswith(".html"):
            continue
        rel = "templates/" + name
        src = _read(rel)
        parts = _COMMENT_SPLIT.split(src)
        changed = 0
        for i in range(0, len(parts), 2):
            for wrong, right in COPY_CORRECTIONS:
                def fix(m, right=right):
                    word = m.group(0)
                    return right.capitalize() if word[0].isupper() else right
                parts[i], k = re.subn(r"\b%s\b" % re.escape(wrong), fix, parts[i],
                                      flags=re.I)
                changed += k
        if changed:
            _write(rel, "".join(parts))
            n += changed
    return n


def main():
    steps = [
        ("the robots block in public_base", repair_parent_robots_block),
        # Must follow the block repair: it wraps what that one puts back.
        ("the staging noindex switch", repair_staging_noindex),
        ("media queries the browser can evaluate",
         repair_media_query_variables),
        ("the confirmation letter's hardcoded map pin",
         repair_confirmation_map_pin),
        ("buttons that scroll nowhere", repair_scroll_anchors),
        ("captions over the wrong photograph",
         repair_photograph_captions),
        ("noindex on guest pages", repair_child_noindex),
        ("part-payments and auto-charge", repair_workshop_payments),
        ("table wrappers", repair_table_wrappers),
        ("the hardcoded market list", repair_hardcoded_markets),
        ("the manage_booking link parameter", repair_manage_booking_parameter),
        ("the .g-plate__row rule", repair_plate_row_rule),
        ("the privacy footer link", repair_privacy_link),
        ("the ticked extras on the booking form", repair_extra_prefill),
        ("the typed dates on the event enquiry", repair_event_date_prefill),
        ("endpoint names that do not exist", repair_endpoint_names),
        ("aria-hidden on decorative svgs", repair_decorative_svgs),
        ("the .g-plan__col rule", repair_plan_col_rule),
        ("the .g-panel__h rule", repair_panel_heading_rule),
        ("variables in media query conditions", repair_media_query_variables),
        ("the house's own reviews", repair_featured_reviews),
        ("why a room is unavailable", repair_unavailable_reason),
        ("the under-18 count the return reads", repair_under_18_field),
        ("the promo code box on the event enquiry", repair_event_promo_field),
        ("the image manager posting to a hardcoded path", repair_admin_images_page),
        ("the url_map guard that never fires", repair_url_map_guards),
        ("column names the database does not have", repair_invented_columns),
        ("the French room names, which are not landed", repair_room_name_map),
        ("guest pages to read before committing", repair_reverted_guest_pages),
        ("the slider's import on Restoration", repair_reveal_import),
        ("the deposit and rooms lines on Workshops", repair_workshop_terms_keys),
        ("the bathroom read from its column", repair_bathroom_follows_column),
        ("the arrival card's context", repair_arrival_card_context),
        ("the after-dark instruction on Contact", repair_after_dark_line),
        ("the Legal Notice's host and RCS line", repair_legal_notice),
        ("the Legal Notice footer links", repair_legal_footer_links),
        ("claims the owner has corrected", repair_corrected_claims),
    ]
    total, failed = 0, []
    for label, fn in steps:
        # Each repair is isolated. This script died on step 2 of 10 once —
        # an anchor it searched for was no longer in the file, .index() raised,
        # and the eight steps after it never ran. Nothing said so: the report
        # listed what it had managed before falling over, which reads exactly
        # like a run that found nothing left to do. A step that cannot run is
        # a thing to say out loud, not a reason to stop.
        try:
            n = fn()
        except FileNotFoundError:
            # The file this step is about is not in this tree at all, so there
            # is nothing here to put back. Said out loud, but not counted as a
            # failure — otherwise a partial checkout could never exit 0.
            print(f"  {'not in tree':<14} {'':<3} {label}")
            continue
        except Exception as e:
            failed.append((label, f"{type(e).__name__}: {e}"))
            print(f"  {'COULD NOT RUN':<14} {'':<3} {label}")
            continue
        total += n
        print(f"  {'restored' if n else 'already fine':<14} {n if n else '':<3} {label}")

    if failed:
        print(f"\n{total} repair(s), and {len(failed)} that could not run:")
        for label, err in failed:
            print(f"  - {label}: {err}")
        print("\nPut those back by hand — the suite will tell you what they were "
              "for. Then run: python tests/run.py")
        return 1
    print(f"\n{total} repair(s). Now run: python tests/run.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
