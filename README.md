# A drawn approach map — and why drawn

`_approach.html`, on the Contact page, in the site's own gold and blue.

## Three reasons it is drawn rather than a map tile

  · **it owes nothing to a CDN.** You have 254 images hotlinked from a
    Squarespace account; if that lapses the site goes blank. This weighs about
    four kilobytes and cannot break
  · **a map tile shows every road equally.** What someone needs before setting
    off is the one route, the turn that catches people out, and the fact that
    the last stretch is unlit. A tile buries all three
  · it is in the site's own colours, so it belongs

The geometry is schematic — sequence and relative distance, which is what a
person reads at the kitchen table. The Google and Apple pins are for the car.

## What it actually says

Toulouse airport, Foix at 1 h 05, Tarascon at 1 h 25, **left at Les Cabannes**,
then the final four kilometres as a dashed line marked *unlit*. The pin is the
gates, and the caption says so — there is no street number here and an address
search leaves people in the village square.

## Two things I got wrong and fixed

  · **every label was upside down.** I put the sub-label at a smaller `y`
    than the name, and in SVG smaller y is HIGHER — so it read "airport /
    Toulouse" and "1 h 05 / Foix", subtitle above title, at all four stops
  · **the same contrast blind spot as the armorial block.** The ground is a
    gradient, which is a background-IMAGE, so my checker read straight through
    to white and reported a failure on text that is fine. Solid colour under
    the gradient — the checker can read it, and a client that fails to paint
    gradients still gets navy

That is the second time the gradient trap has caught me in two rounds. Worth
knowing if you ever add one: put a `background-color` under it.

## Fits

320, 390, 768 and 1440 — no sideways scroll, buttons at 49px.

## Testing

242 renders, 11 conditions: 3, all text-measure boundaries within a character.
