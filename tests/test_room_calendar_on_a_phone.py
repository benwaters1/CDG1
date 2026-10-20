"""The room page's calendar, as a guest reads it on a phone.

The owner, 19 October 2026: the calendar was "very hard" to read on a phone.
On a room's booking page the free nights were 13px figures with no box round
them, and the taken ones were grey squares with a line through, so a month
read as a block of grey in which the nights a guest could actually book were
the faintest thing on it.

The answer is a block at the end of gudanes.css: below 640px a free night is
a boxed, full-strength figure big enough to read, and a taken or past one
steps back with no box at all. gudanes.css arrives from the design side as a
whole file, so this block is exactly the kind of thing a handover drops
without anybody noticing -- the page still renders, the calendar still works,
and it is simply hard to read again. This reads the stylesheet for it.
"""
import os
import re

from _harness import Suite
import _harness

CSS = os.path.join(_harness.ROOT, "static", "gudanes.css")


def _phone_block(css):
    """Every rule inside a max-width:640px block, joined."""
    out, i = [], 0
    while True:
        i = css.find("@media (max-width: 640px){", i)
        if i < 0:
            return "".join(out)
        depth, j = 0, css.index("{", i)
        for k in range(j, len(css)):
            depth += css[k] == "{"
            depth -= css[k] == "}"
            if depth == 0:
                out.append(css[j + 1:k])
                i = k
                break


def _rule(block, selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", block)
    return m.group(1).replace(" ", "").replace("\n", "") if m else ""


def run():
    s = Suite("the room calendar on a phone")
    with open(CSS, encoding="utf-8") as fh:
        css = fh.read()
    block = _phone_block(css)

    free = _rule(block, ".g .g-cal .g-dates__day")
    size = re.search(r"font-size:(\d+)px", free)
    s.check("a free night is a figure big enough to read (16px or more)",
            bool(size) and int(size.group(1)) >= 16, detail=free)
    s.check("in a box of its own", "border:1px" in free and "background:" in free, detail=free)
    taken = _rule(block, ".g .g-cal .g-dates__day.is-off")
    s.check("a taken night has no box, so the free ones stand out",
            "background:transparent" in taken and "border-color:transparent" in taken, detail=taken)
    s.check("and is still struck through, so colour is not the only sign",
            "text-decoration:line-through" in taken, detail=taken)
    chosen = _rule(block, ".g .g-cal .g-dates__day.is-sel")
    s.check("the nights chosen are filled in", "background:var(--ink)" in chosen, detail=chosen)
    s.check("the block says it is the house's, so a handover keeps it",
            "HOUSE ADDITION, not from a handover" in css)
    return s


if __name__ == "__main__":
    print(run().report())
