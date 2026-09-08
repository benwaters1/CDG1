# Green and burgundy — and prose running two characters a line

## The colours

Small accents, one job each, which is the discipline that fixed the gold:

  **Burgundy `#5E1F2D`** — the voice. Pull-quotes, what guests said.
  **Royal green `#17352A`** — the outdoors. *Les Jardins* and *La Piscine*
  headings on The Estate.

**These two shades and not lighter ones because I measured first.** As text on
the parchment: green 11.71:1, burgundy 10.81:1. Both comfortably AA at any
size — which is exactly what the gold does NOT have, and why gold reads brown
as text and had to be split into ornament and text.

I first built them as full section grounds. That was too big a gesture for
what you asked; the backgrounds came back out, the tokens stayed, because the
colours were right and the scale was wrong.

**A section may carry only one ground — the audit now enforces it.**

## And what the colour work uncovered

Adding burgundy to a pull-quote made me look at one properly for the first
time. On the homepage, a **416px quote floated right inside a 544px wrap left
the paragraph beside it 128 pixels — the prose came out at two characters a
line, running vertically down the page.**

Live. And **my own audit could never have caught it**, because the
text-measure check exempts anything near a float — I wrote that exemption
myself to stop false positives, and it hid a real one.

**Prose beside it: 2 characters → 61.**

## Four goes at it, and the first three were the wrong thing

I chased the float's **width** three times — a 50% cap, then a 45% cap, then
absolute floors — and it was never the width. A pull-quote set at **42px needs
about 520px to hold twenty-four characters**, so at 416px it read twenty-one
however wide I let it be.

**The type size was the fault.** The quote now scales with its own container:
large when it runs full width, smaller when it floats into a 416px column.

Same lesson as the card grid two rounds ago — **on this site the wrap is the
measure that matters, not the viewport.** A .g-wrap is often 544px inside a
1440px window, which is precisely how this hid.

**Four failures across 12 pages × 6 widths → one, at 23 characters on a 320px
phone. One character off the floor.**

## New audit rules

  - prose crushed beside a float (float-aware, which the measure check is not)
  - a pull-quote too narrow for its own words
  - two grounds on one section

## Testing

242 renders, 11 conditions: 3.
