"""Put back what a design handover keeps taking out.

Run this straight after unzipping a `gudanes-final_NN.zip` over the templates,
then run the suite. It repairs the four things that have now been reverted by
more than one handover in a row, and it is idempotent — running it on an
already-repaired tree changes nothing.

This is a WORKAROUND, not a fix. The cause is that the zips are generated from a
snapshot of the tree rather than from current main, so anything shipped after
that snapshot is silently reverted by whichever file touches it. The real fix is
one sentence upstream: regenerate from current main before exporting. Until that
happens, this script and the suite are what stand between a handover and a
regression.

The four:

  1. noindex. 24 guest pages carry `{% block robots %}` overriding the empty
     block in public_base. The handovers strip the block from the parent too,
     which turns every override into dead markup — nothing errors, the pages
     render perfectly, and a guest's booking becomes indexable.
  2. Part-payments and the auto-charge opt-out in workshop_manage.html. Five
     handovers, five deletions.
  3. .table-wrap around the tables in guest_statement.html, without which a
     wide table drags the whole page sideways on a phone.
  4. The footer's Privacy Policy link, which comes back as href="#".

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
    start = head.index("    {# Part-payments and the auto-charge notice.")
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


def repair_privacy_link():
    rel = "templates/public_base.html"
    src = _read(rel)
    dead = "<a href=\"#\">{{ t('Privacy Policy') }}</a>"
    if dead not in src:
        return 0
    _write(rel, src.replace(
        dead, "<a href=\"{{ url_for('privacy_page') }}\">{{ t('Privacy Policy') }}</a>", 1))
    return 1


def main():
    steps = [
        ("the robots block in public_base", repair_parent_robots_block),
        ("noindex on guest pages", repair_child_noindex),
        ("part-payments and auto-charge", repair_workshop_payments),
        ("table wrappers", repair_table_wrappers),
        ("the privacy footer link", repair_privacy_link),
    ]
    total = 0
    for label, fn in steps:
        n = fn()
        total += n
        print(f"  {'restored' if n else 'already fine':<14} {n if n else '':<3} {label}")
    print(f"\n{total} repair(s). Now run: python tests/run.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
