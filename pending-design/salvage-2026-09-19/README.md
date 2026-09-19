# Salvaged from the third stale export, 2026-09-19

Produced by `python tools/salvage_handover.py gudanes-full.zip --write`, which
was written after doing this by hand three times.

The export was cut from the same tree as the two before it — `base.html` with
0 `url_for` calls and no CSRF meta tag — so none of it was installed. This is
the part that adds something the tree does not have.

## `static__gudanes.css`

Nine selectors across five whole rules: `.g-band`, `.g-book-bar`,
`.g-compare`, `.g-rooms-list`, the `.g-sec` variants, and one global.

**None of it carries the media-query bug.** The export still writes
`@media (max-width: var(--m-read))` — the custom-property fault for the fourth
time — and those fragments were excluded here because they only ever appear
inside runs that also remove the literal `34rem` rules. The tool drops a run it
cannot hand over whole rather than passing along half of one.

**The one worth a decision:**

```css
img{ max-width: 100%; height: auto; }
```

Their comment: *"Every page overflowed on mobile because images from the CMS
have no intrinsic constraint. This is the belt — every image in the page
respects the viewport."*

The claim needs checking before it goes in, because it is a GLOBAL. Measured
on the live site at 375px, the document does not scroll sideways — but there
are four images that deliberately sit wider than the viewport inside a
horizontal scroller. A blanket `max-width: 100%` would pull those in and flatten
the scroller. Worth having, probably scoped rather than global.

## What was not kept

`templates/bnbforms-widget-styled_1.html` was salvaged as new and then
deleted, for the third time. It is a script tag loading `cdn.bnbforms.com` that
draws a "Book Your Stay" button pointing at an external booking service,
imported by nothing in either tree. The tool is right to surface it — it is
genuinely new — and it is still not something to install from a handover.
