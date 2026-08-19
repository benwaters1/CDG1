# Workshops — copy fields and the six ateliers

Two parts: three optional columns, then the real 2026–27 programme as seed SQL.
**The page works without any of this** — it falls back to the description and the
session dates. This just fills it properly.

## 1. Columns

```sql
ALTER TABLE workshops ADD COLUMN nights_label TEXT;   -- "4 nights / 5 days"
ALTER TABLE workshops ADD COLUMN sample_day   TEXT;   -- the "A Sample Day" copy
ALTER TABLE workshops ADD COLUMN itinerary    TEXT;   -- read by workshop_detail.html
```

`itinerary` matters: `workshop_detail.html` already reads it and currently
renders nothing, because the column has never existed.

## 2. The five ateliers

Dates and prices taken from the live product pages on chateaugudanes.com.
Descriptions rewritten in the .net voice — plainer, less flowery — while
keeping what the .com actually promises.

```sql
INSERT INTO workshops (title, description, price_per_person, default_capacity, active, sort_order, created_at)
VALUES
('Antique & French Finds',
 'A treasure hunt through the villages and valleys of the Pyrénées. Village markets, hidden antique shops and vide-greniers in search of vintage linens, porcelain, furniture and curiosities — then back to the château for long meals and the gardens. The late-July sitting is timed around the Grand Antique Faire at Foix, when the whole village fills with dealers.',
 2800, 10, 1, 1, datetime('now')),

('Immersive Artisan Workshops',
 'Gather, create and restore amidst the golden landscapes of the French Pyrénées. Hands-on workshops led by master artisans — embroidery, calligraphy, screen and block printing, feather crafts and cooking — taught in a house being restored by the same old methods. For first-timers and accomplished makers alike.',
 2600, 10, 1, 2, datetime('now')),

('Noël at Gudanes',
 'The château as a sanctuary of gentle anticipation, and the first festive atelier held here. Four nights of making heirlooms by hand beneath candlelight, long festive meals, and traditional crafts taught by local artisans — with mulled wine or French hot chocolate never far from reach.',
 3200, 10, 1, 3, datetime('now')),

('Cooking in the Cuisine',
 'Hands-on classes and live demonstrations in the château''s own kitchens, market mornings for seasonal produce, and evenings spent eating what you have made — from eighteenth-century recipes to modern French classics.',
 3800, 10, 1, 4, datetime('now')),

('Seven Starry Nights',
 'Our fullest Workshop: help restore the château''s eighteenth-century frescoes, join cooking classes in between, hike and canoe the Ariège, and explore medieval towns, caves and neighbouring châteaux.',
 4800, 10, 1, 5, datetime('now'));
```

## 3. Lengths and sample days

```sql
UPDATE workshops SET nights_label = '3 nights / 4 days',
  sample_day = 'A morning at one of the region''s celebrated farmers'' markets, set in a preserved medieval village, where producers, artisans and antique treasures share the same square.

An afternoon among our favourite hidden brocantes and dealers, then a private luncheon at a neighbouring château in its historic dining room.

Dinner en plein air on the terrace beneath the stars, followed by an open-air French film with hot chocolate and chantilly.',
  itinerary = 'The Art of Antiquing — our favourite hidden brocantes, dealers and vide-greniers, coordinated with the Grand Antique Faire at Foix
Seasonal French Cooking Classes — cooking with the summer harvest alongside the château chefs
Candlelit Château Dinners — thoughtful tablescapes and seasonal menus
Local Aperitifs Tasting — medieval aperitifs still made by time-honoured methods, tasted in the original medieval kitchen
A Morning at the Market — a celebrated farmers'' market in a preserved medieval village
A Private Château Luncheon — in a neighbouring château''s historic dining room
A Journey Through History — the ruins of a medieval château, then afternoon tea in a village café
Dinner & French Cinema Beneath the Stars'
WHERE title = 'Antique & French Finds';
UPDATE workshops SET nights_label = '4 nights / 5 days',
  itinerary = 'Embroidery with a master artisan
Calligraphy — the hand behind the documents the house still holds
Screen and block printing
Feather crafts
Traditional French cooking
All guided by expert artisans and tutors, for first-timers and accomplished makers alike'
WHERE title = 'Immersive Artisan Workshops';
UPDATE workshops SET nights_label = '4 nights / 5 days',
  itinerary = 'Embroidery — a Christmas ornament drawn from the details of the eighteenth-century Salon de Musique
Candle Making — using the château''s own original fragrance recipe from the 1700s
Illuminated Christmas Cards — the ancient art of the illuminated manuscript, inspired by the château chapel
Historic Cooking Class — champagne jellies set in antique copper moulds
Christmas Cooking Classes — gingerbread, traditional fruit mince pies and tarte tatin'
WHERE title = 'Noël at Gudanes';

UPDATE workshops SET nights_label = '5 nights / 6 days',
  itinerary = 'Seasonal French Cooking Classes — the summer harvest, from the orchards, gardens and markets of the Pyrénées
Historic French Cooking Classes — recipes once eaten within these walls in the 1700s, including historic ices in antique moulds
Regional Wine Tasting — hosted at the château by a local producer
Setting the French Table — the etiquette and history, then styling a table together for a candlelit dinner
Floral Artistry — seasonal arrangements with the château florist
Chocolate Making — tempering and traditional technique with the château chefs
A Morning at the Market
The Art of Antiquing — hidden brocantes and dealers
A Private Château Luncheon — in a neighbouring château''s eighteenth-century chapel
Mountain Picnic & Gentle Hike
French Cinema Beneath the Stars — with hot chocolate and chantilly',
  sample_day = 'A stroll to the local market for fresh produce and crafts, then sweet and savoury cooking classes inspired by the eighteenth-century Château Gudanes cookbook, with a picnic lunch.

Aperitifs with a talk on French table-setting and dining history, followed by an informal dinner of the day''s creations.'
WHERE title = 'Cooking in the Cuisine';

UPDATE workshops SET nights_label = '7 nights / 8 days',
  itinerary = 'Hands-on restoration of the château''s eighteenth-century frescoes, alongside the team
Cooking classes rooted in eighteenth-century tradition
Guided hikes into the high valleys of the Pyrénées
Canoeing the Ariège
Vide-greniers and medieval towns
The Grotte de Niaux — cave paintings that have survived some fourteen thousand years
Vineyards and artisan villages',
  sample_day = 'Crêpe-making and a mimosa breakfast, a riverside picnic at the Pont du Diable, and an afternoon exploring the 17,000-year-old Niaux Cave and its Paleolithic paintings.

A wine-tasting aperitif in the drawing room, followed by dinner and an evening of poetry and prose.'
WHERE title = 'Seven Starry Nights';
```

## 4. Sessions — the real 2026–27 dates

```sql
-- 2026

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2026-06-12', '2026-06-17', 10, datetime('now') FROM workshops WHERE title='Cooking in the Cuisine';
INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2026-07-10', '2026-07-15', 10, datetime('now') FROM workshops WHERE title='Cooking in the Cuisine';

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2026-06-20', '2026-06-27', 10, datetime('now') FROM workshops WHERE title='Seven Starry Nights';

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2026-10-23', '2026-10-27', 10, datetime('now') FROM workshops WHERE title='Immersive Artisan Workshops';

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2026-12-05', '2026-12-09', 10, datetime('now') FROM workshops WHERE title='Noël at Gudanes';

-- 2027
INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2027-07-03', '2027-07-06', 10, datetime('now') FROM workshops WHERE title='Antique & French Finds';

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2027-06-25', '2027-06-30', 10, datetime('now') FROM workshops WHERE title='Cooking in the Cuisine';

INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
SELECT id, '2027-07-10', '2027-07-17', 10, datetime('now') FROM workshops WHERE title='Seven Starry Nights';
```

**Check the column names against your own `workshop_sessions` table first** —
if it has `notes`, `price_override` or similar NOT NULL columns, add them to
the inserts.

## Two things worth knowing

**The Long Weekender is not on the .com** — it existed only on the .net.
Following the .com, it is not seeded here. If you still run it, add it back
at €2,400 with its June/July/August 2026 dates.

**A second French Finds sitting** is listed for late July 2027 with dates
"to be confirmed" — not seeded, since there is no date to sell yet.

**Prices are the .com figures throughout** — these are the current ones and
supersede the older .net listings (which had Cooking at €3,900 and Seven
Starry Nights at €4,500).
