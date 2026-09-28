#!/usr/bin/env python3
"""Associate form labels with their controls for screen readers.

Two patterns exist in these templates:

  A. <label class="field-label">Text</label> followed by a control.
     Fixed with for/id, which also makes the label clickable.

  B. A control with only a placeholder and no label at all.
     Fixed with aria-label taken from the placeholder.

The trap: several of these controls sit inside {% for %} loops, so a
literal id would be emitted once per row and duplicate ids are invalid
HTML that break label association *and* getElementById. Inside a loop we
therefore use aria-label, which needs no unique value.
"""
import io
import re
import glob
import os

CONTROL = r'<(input|select|textarea)\b'
SKIP_TYPES = {'hidden', 'submit', 'button', 'image'}


def in_loop(text, pos):
    """True if pos sits inside a {% for %} ... {% endfor %} block."""
    before = text[:pos]
    return before.count('{% for') > before.count('{% endfor %}')


def slug(s):
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'\{\{.*?\}\}', '', s)
    s = re.sub(r'[^a-zA-Z0-9]+', '-', s).strip('-').lower()
    return s[:40]


def attr(tag, name):
    m = re.search(name + r'\s*=\s*"([^"]*)"', tag)
    return m.group(1) if m else None


def fix_file(path):
    # newline='' both ways: whatever endings the file has, it keeps.
    src = io.open(path, encoding='utf-8', newline='').read()
    stem = os.path.basename(path).replace('.html', '')
    out = src
    changes = 0
    # Seeded with the ids already in the file, so a generated one cannot
    # collide with a hand-written one either.
    used = {i: 1 for i in re.findall(r'id="([^"]+)"', src)}

    # ---- Pattern A: label immediately before a control -------------------
    # Allow whitespace, Jinja comments/tags and one wrapping <div> between.
    pat = re.compile(
        r'(<label\b(?![^>]*\bfor=)[^>]*>)(.*?)(</label>)'
        r'((?:\s|<!--.*?-->|\{#.*?#\}|\{%.*?%\}|<div[^>]*>)*?)'
        r'(' + CONTROL + r'[^>]*>)',
        re.S)

    def repl_a(m):
        nonlocal changes
        label_open, label_text, label_close, between, ctrl_tag = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
        if (attr(ctrl_tag, 'type') or '').lower() in SKIP_TYPES:
            return m.group(0)
        if 'aria-label' in ctrl_tag:
            return m.group(0)

        # A LABEL THAT ALREADY WRAPS A CONTROL MUST NOT BE GIVEN for=.
        #
        # The pattern above matches the label's own </label> and then looks
        # for the next control AFTER it -- so for a wrapping label it finds a
        # different field further down the page and points the label at that.
        # It did, twenty-four times. room_form.html came out with
        #
        #   <label class="checkbox-row" for="room_form-start-date">
        #     <input type="checkbox" name="active">
        #     <span>Bookable - guests can choose this room</span>
        #   </label>
        #
        # and when a label carries both for= and a wrapped control, `for` wins.
        # Clicking "Bookable" would have moved focus to the start-date box, and
        # a screen reader would have read that date field out as "Bookable -
        # guests can choose this room". That is worse than the missing label it
        # was fixing: a wrong name is acted on, a missing one is asked about.
        #
        # A wrapping label needs no for= at all -- the association is implicit.
        if re.search(CONTROL, label_text):
            return m.group(0)

        pos = m.start()
        existing_id = attr(ctrl_tag, 'id')

        # A PARTIAL CAN BE INCLUDED TWICE ON ONE PAGE, which is the same trap
        # as a loop reached by another road. _menu_fields.html is pulled in
        # once per course, so a literal id in it is emitted once per course --
        # eleven duplicates, and the per-file registry could not see them
        # because within the file each id appears exactly once. The collision
        # only exists after rendering.
        #
        # aria-label needs no unique value, so a partial gets one of those.
        a_partial = stem.startswith('_')
        if (in_loop(src, pos) or a_partial) and not existing_id:
            # aria-label: safe to repeat across loop iterations
            clean = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', label_text)).strip()
            clean = re.sub(r'\{\{.*?\}\}', '', clean).strip()
            if not clean:
                return m.group(0)
            new_ctrl = re.sub(r'^<(\w+)', r'<\1 aria-label="%s"' % clean.replace('"', "'"),
                              ctrl_tag, count=1)
            changes += 1
            return label_open + label_text + label_close + between + new_ctrl

        cid = existing_id
        if not cid:
            base = attr(ctrl_tag, 'name') or slug(label_text) or 'f'
            cid = '%s-%s' % (stem, slug(base))
            # UNIQUE WITHIN THE FILE. The id is built from the control's name,
            # and one template very often has two controls with the same name
            # -- a "notes" field in two panels, a "status" in a filter bar and
            # again in a row. Both got admin_events-status, which is invalid
            # HTML: a duplicate id breaks the label association this is
            # supposed to be creating, AND getElementById, which silently
            # returns whichever came first. Sixteen of them, caught by
            # test_rendered_markup rather than by anything here.
            if cid in used:
                used[cid] += 1
                cid = '%s-%d' % (cid, used[cid])
            else:
                used[cid] = 1
            new_ctrl = re.sub(r'^<(\w+)', r'<\1 id="%s"' % cid, ctrl_tag, count=1)
        else:
            new_ctrl = ctrl_tag

        new_label = label_open[:-1] + ' for="%s">' % cid
        changes += 1
        return new_label + label_text + label_close + between + new_ctrl

    out = pat.sub(repl_a, out)

    # ---- Pattern B: placeholder-only controls, no label ------------------
    def repl_b(m):
        nonlocal changes
        tag = m.group(0)
        if (attr(tag, 'type') or '').lower() in SKIP_TYPES:
            return tag
        if 'aria-label' in tag or attr(tag, 'id'):
            return tag
        ph = attr(tag, 'placeholder')
        if not ph:
            return tag
        # skip if a label[for] elsewhere already covers it (id absent, so no)
        clean = re.sub(r'\{\{.*?\}\}', '', ph).strip()
        if not clean:
            return tag
        changes += 1
        return re.sub(r'^<(\w+)', r'<\1 aria-label="%s"' % clean.replace('"', "'"),
                      tag, count=1)

    out = re.sub(CONTROL + r'[^>]*>', repl_b, out)

    if out != src:
        io.open(path, 'w', encoding='utf-8', newline='').write(out)
    return changes


total = 0
for p in sorted(glob.glob('templates/*.html')):
    n = fix_file(p)
    if n:
        print('%-40s %d' % (os.path.basename(p), n))
        total += n
print('total changes:', total)
