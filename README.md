# Design fixes — four real ones, not two hundred and twenty

You asked for ten per page. I went looking, and **there are not ten faults
per page.** Making up the difference would mean changing things that are
already right, which is the point where a rebuild starts going backwards.

Here is what a full scan actually turned up.

## 1. Doubled hairlines — every divider drawn at 2px

The dossier list on the room page sits inside a `.g-aside`, so it collects
**two** border rules: `.g-aside dl > div` gives every row a bottom border and
`.g-dossier__l div` gives it a top one. Every divider drew at 2px while the
first and last drew at 1px — a list that reads as unevenly ruled without it
being obvious why.

**I wrote three `.g-dossier__l` rules that could never have won**, because
the bottom border comes from a different component's selector entirely. I only
found it by asking the browser which rules matched, instead of reading the
file and guessing. Fixed at the right selector.

Same fault on Home, where `.g-facts` and the section below it both drew an
edge into the same seam.

    doubled rules across 17 pages x 3 widths: 0

## 2. A card that was full width by accident

On The Estate a three-card grid had the last card spanning `1 / -1` — so two
cards were 568px and the third 1168px, with its image stretched to a 5.2:1
letterbox beside two at 2.5:1.

A full-width card is a deliberate device. A full-width card that is simply the
leftover third of a row is not. The span now holds only where it is asked for
with `data-span="full"`, and that case gets a sane 21:9 crop.

    all three cards 568px, all three images 2.53:1

## 3. Stacked buttons ran together

Two buttons stacked on a narrow screen met edge to edge, so the solid one's
border and the quiet one's read as a single 2px line between them.

## What I checked and found nothing wrong with

Widowed headings, buttons of mismatched height in a row, list markers hanging
outside their block, and mixed image ratios anywhere else. **The scan reported
seventeen pages with doubled rules and two with mixed ratios; measured
properly, it was two and one** — the rest were side-by-side elements my check
was reading as stacked.

## Two new audit rules

  - a doubled hairline where two stacked blocks meet
  - mixed image ratios inside one grid

## Testing

242 renders, 11 conditions: 3, all text-measure boundaries within a character.
