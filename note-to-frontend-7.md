# The container reset took your tree back, and the export carried it

**Nothing from `gudanes-full.zip` went in except four parked partials,
`RESTORATION.md` and `tools/fullreview.py`.** This note is the only thing that
matters in this round, because the next export is either fixed by it or wasted
the same way.

## What happened

`tools/fullreview.py` explains it in its own first paragraph, and you were
right to write it down:

> *Rebuilt after the container reset wiped the working directory. The templates
> and CSS came back from the last zip.*

**The last zip was not the last commit.** Restoring a working directory from a
zip restores it to whenever that zip was cut, and everything committed since is
simply absent — not conflicting, not flagged, just missing. Every file then
edited from that copy carries the gap forward, so installing it is a revert of
everything that happened to that file in between, whether or not you touched
those lines.

`tools/check_handover.py` measured it before a line was read:

```
Checked 58 modified file(s); 57 remove something.
This tree would UNDO work from 107 commit(s), 4097 line(s) in all.
```

The worst of it was `templates/base.html`, which came back as the **initial
commit** — 161 `url_for` calls down to zero, the `csrf-token` meta tag gone, the
mobile drawer and the ops-calendar link with it. `_insurance_section.html` lost
all four of its CSRF tokens. That is the staff application off the air and its
forms unprotected, from a zip that looked like ordinary design work.

None of this is a criticism of the work in it. The reveal slider is good, and
refusing to render without real pairs rather than reaching for a placeholder is
exactly right. The problem is only ever the copy it was made from.

## What it cost your own findings

Two of the four partials are held because they describe a site that no longer
exists. `what_it_comes_to` opens by saying the confirmation page shows
*"nothing about money at all. No room name, no rate, no total, no balance."*
`booking_confirmation.html` has printed the total on line 40 for months. The
observation was true of your copy and false of the site, and there was no way
for you to know that from inside a stale tree.

`pending-design/README.md` has the specifics on all four, including the field
names — `what_it_comes_to` reads ten fields off the booking and the database
has four of them under different names.

## The fix, and it is one command

```
python tools/export_for_design.py
```

It writes a zip of the design surface **as it is on main right now**, and
refuses to write one from a tree that is behind, ahead or dirty — which is the
same fault caught at the only moment it is cheap, before the copy is made
rather than after it comes back.

Start every round from a fresh run of it. Not from the last zip, not from a
restored directory, not from anything that was on disk before the reset. If the
container is wiped again, that command is the recovery step — it is faster than
unzipping an old export and it cannot leave you behind.

The round before this one reverted **nothing**, because it was built that way.
That is the whole difference.

## One file that was not kept

`templates/bnbforms-widget-styled_1.html` — a script tag loading
`cdn.bnbforms.com` that draws a "Book Your Stay" button pointing at an external
booking service. Nothing imports it in your tree or ours, so it reads as a file
that happened to be in the folder rather than something sent on purpose.

It was not installed. A third-party widget on the public pages routes guests
away from the booking system this app is, and puts someone else's script on
every page — a decision for Ben to make deliberately if he wants it, not one to
arrive inside a design handover.

## Still outstanding from your side

- **The reveal pairs.** Your own note has the brief. There is no
  `static/reveal/` yet, so `the_reveal` renders nothing, correctly.
- **`atelier_glance` wants `w.duration_label`.** The column is `nights_label`.
  If they are the same idea, say so and it goes live in one line.
- **`restoration_explorer` and `the_ninety_four` are imported by nothing**, in
  your tree or ours. Say which page each belongs on.
