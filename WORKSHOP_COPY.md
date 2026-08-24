# Workshop programme — repositioned by length

Three lengths, three centres of gravity — but five ateliers in total. The
3/5/7-night trio below is the spine of the programme: each carries the others,
but one leads. Noël and the Immersive Artisan Workshops sit outside that
pattern (both four nights, one seasonal and one organised around making
rather than a subject) and are deliberately left untouched by this SQL.

```sql
-- ============================================================
-- SEVEN NIGHTS — the restoration, led
-- ============================================================
UPDATE workshops SET
  nights_label = '7 nights / 8 days',
  description  = 'A week inside the restoration itself. You will spend real hours beside the artisans lifting eighteenth-century frescoes out from under a century of overpaint — the slowest, most exacting work in the château, and the reason it is taking a decade. Between those mornings there is cooking in the château kitchens, the brocantes of the valley, and dinner by candlelight every evening.',
  sample_day   = 'Coffee in the Renaissance kitchen while the château wakes, then up to the salon with the conservators. An hour of scalpel and solvent under the raking lamp, lifting cream emulsion from a wall unseen since the 1700s. A centimetre is a good morning.

Lunch under the lime trees. The afternoon is yours — the pool, the library, the parkland, or back to the wall if it has hold of you.

Aperitifs at six, dinner by candlelight, and the drawing room until somebody remembers the time.',
  itinerary    = 'FRESCO CONSERVATION — four mornings alongside the restoration team, working on the salon and dining-room walls under the supervision of professional conservators from France, Italy and England
THE ARCHIVE — an afternoon with the château''s own documents, plans and photographs, and what they reveal about what was here before
LIME AND PLASTER — the traditional materials, why Monuments Historiques requires them, and how they are mixed and applied
COOKING IN THE CUISINE — two hands-on classes, one seasonal and one drawn from the château''s eighteenth-century recipe book
THE BROCANTES — a full day among the antique fairs and vide-greniers of the Ariège
GROTTE DE NIAUX — Paleolithic paintings that have survived fourteen thousand years, twenty minutes from the gates
THE HIGH VALLEYS — a guided walk into the Pyrénées, and a picnic where the river turns
WINE OF THE SOUTH-WEST — a tasting in the medieval kitchen with a local producer'
WHERE title LIKE '%Seven Starry Nights%';

-- ============================================================
-- FIVE NIGHTS — the kitchen, led
-- ============================================================
UPDATE workshops SET
  nights_label = '5 nights / 6 days',
  description  = 'Five days in the château kitchens, cooking the way this house has always cooked — from the market that morning, from the garden if it is summer, and from a recipe book written at Gudanes in the 1700s. You will also spend a morning on the frescoes, because it is difficult to eat in a room and not want to know how it was saved.',
  sample_day   = 'Down to the village market early with the chef, before the best of it goes.

The Renaissance kitchen until lunch: knife work, a sauce that cannot be hurried, and the copper that came with the château. You eat what you have made, outside when the weather allows.

The afternoon drifts. Aperitifs at six, and dinner is what you made at noon.',
  itinerary    = 'SEASONAL FRENCH COOKING — four hands-on classes with the château chefs, built around whatever the Ariège is giving that week
THE 1700s COOKBOOK — historic dishes from the château''s own recipe book, including ices set in antique copper moulds
A MORNING AT THE MARKET — Les Cabannes on a Sunday, or Tarascon midweek, with the chef doing the buying
BREAD AND PASTRY — at the boulangerie below the gates, three minutes down the hill, while it is still dark
FRESCO CONSERVATION — one morning with the restoration team, on the walls of the room you have been eating in
SETTING THE FRENCH TABLE — the etiquette, the history, and laying one properly for the last night
WINE OF THE SOUTH-WEST — a tasting with a local producer, matched to what you have been cooking
A PRIVATE CHÂTEAU LUNCHEON — at a neighbouring house, in its own dining room'
WHERE title LIKE '%Cooking in the Cuisine%';

-- ============================================================
-- THREE NIGHTS — the brocantes, led
-- ============================================================
UPDATE workshops SET
  nights_label = '3 nights / 4 days',
  description  = 'A long weekend of hunting. The Ariège is thick with brocantes, vide-greniers and antique fairs, and this is three days of working through them properly — with someone who knows which dealers are worth the detour, what things are worth, and how to get them home. Cooking and long lunches between, and a chef who knows the region as well as the dealers do.',
  sample_day   = 'An early start for the fair at Mirepoix — timbered arcades, four hundred years old, and dealers setting up before light. You arrive ahead of the crowd.

Coffee and pastry, then the dealers who are not advertised: a barn outside the village, and a vide-grenier in a churchyard.

A late lunch on the terrace, the morning''s finds laid out, and an honest appraisal of what you have bought.',
  itinerary    = 'THE BROCANTES — our own dealers and vide-greniers across the valley, including the ones that are not advertised
MIREPOIX — the medieval bastide and its market, among the best preserved in France
THE GRAND ANTIQUE FAIRE AT FOIX — for the late-July sitting, timed to it deliberately
WHAT IS IT WORTH — an hour on marks, makers, period and repair, so you buy well rather than often
LINENS AND MONOGRAMS — the French domestic textiles worth looking for, and how to read them
SEASONAL COOKING — a hands-on class with the château chefs, using the market you walked through that morning
A PRIVATE CHÂTEAU LUNCHEON — at a neighbouring house
GETTING IT HOME — packing, shipping and the paperwork, handled before you leave'
WHERE title LIKE '%Antique%' OR title LIKE '%French Finds%';
```

## Why this shape

Three lengths that mean three different things, rather than three durations of
the same holiday. These three are the spine; Noël and the Artisan workshops
sit alongside them:

- **Three nights** is the one you can take without arranging your life around
  it. It is the brocantes, and it sells on the hunt.
- **Five nights** is the kitchen. Enough time to actually learn a technique
  rather than watch one.
- **Seven nights** is the restoration. It is the only length where a guest can
  do enough hours on a wall to see the thing change under their hands, and it
  is what makes Gudanes different from a cookery school in a nice house.

Each still carries the others. Nobody spends seven days only on frescoes.

The other two are deliberately not on this grid. **Noël** is four nights in
December and happens once a year — it sells on the season, not the subject.
**The Immersive Artisan Workshops** are four nights given to making, taught by
the same craftspeople the restoration depends on. Neither should be forced
into the 3/5/7 logic; that they break it is the point.
