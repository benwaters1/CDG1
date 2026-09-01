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
    """The two things the image manager arrives with, both times so far.

    It is a designer page with no route yet -- nothing in app.py renders it --
    so neither fault shows up as a broken page. They show up in the sweeps.

    THE ENDPOINT. It names url_for('admin_image_upload'), and it came wrapped
    in a guard: `if 'admin_image_upload' in url_map`. The guard does not raise
    -- Jinja is content to say a name it has never heard of contains nothing --
    it just always answers no, because url_map is not in the template context.
    So the branch naming the endpoint is dead in every render, and what the
    page actually posts to is the literal path in the else. Which is worse than
    a crash: the day the route moves, the page keeps posting to the old path
    and nothing says so.

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
    bad = ("var ENDPOINT = \"{{ url_for('admin_image_upload')")
    if bad in src:
        start = src.find(bad)
        end = src.find(";", start)
        if end != -1:
            src = src[:start] + 'var ENDPOINT = "/admin/images/upload"' + src[end:]
            fixed += 1
    marker = '<input type="file" accept="image/*" hidden data-slot-input'
    at = src.find(marker)
    if at != -1 and "aria-label" not in src[at:at + 200]:
        close = src.find(">", at)
        if close != -1:
            src = (src[:close] + ' aria-label="Choose a photograph for '
                   '{{ slot.label }}"' + src[close:])
            fixed += 1
    if fixed:
        _write(rel, src)
    return fixed


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


def main():
    steps = [
        ("the robots block in public_base", repair_parent_robots_block),
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
        ("the house's own reviews", repair_featured_reviews),
        ("why a room is unavailable", repair_unavailable_reason),
        ("the under-18 count the return reads", repair_under_18_field),
        ("the promo code box on the event enquiry", repair_event_promo_field),
        ("the image manager that has no route yet", repair_admin_images_page),
        ("guest pages to read before committing", repair_reverted_guest_pages),
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
