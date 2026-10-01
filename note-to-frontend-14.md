# For whoever hands over the public templates: dates in the browser, and one expiry date

Three public templates had a date wrong, and the fixes are in `public_base.html`,
`book_room.html`, `_availcal.html` and `guest_account.html`. Please keep them
when you next send these files.

**1. The earliest departure on every form was the arrival day itself.** The
site-wide script in `public_base.html` that makes a departure follow its
arrival did this:

    var next = new Date(a.value + 'T00:00:00');   // LOCAL midnight
    next.setDate(next.getDate() + 1);
    var min = next.toISOString().slice(0, 10);     // read back in UTC

Anywhere east of Greenwich, local midnight on the 30th is still the 29th in
UTC. So for anybody booking from France the departure picker allowed a stay of
no nights, all day, every day, and moving the arrival past the departure
snapped the departure onto the arrival day. The server then refused the form.

**2. Every date picker offered yesterday for two hours after midnight.**
`new Date().toISOString().slice(0, 10)` is UTC's day, not the house's. From
midnight to 02:00 in the Ariège it is still yesterday, and the room form refuses
a past arrival on submit.

**3. The calendars took "today" from the guest's own clock**
(`new Date(); today.setHours(0,0,0,0)`), so a guest in New York was offered the
house's yesterday all evening.

**What replaced them:** one helper, `window.houseDay`, in a `<script>` in
`public_base.html`'s `<head>` (in the head so a page's own script can use it
wherever it sits):

- `houseDay.today()` gives the house's day as `YYYY-MM-DD`. It asks the browser
  for the date in the house's time zone (`{{ house_tz }}`, `Europe/Paris`), and
  falls back to the day the server drew the page (`{{ house_today_iso() }}`).
- `houseDay.after(iso)` gives the day after an ISO date, counted on the
  calendar alone. `houseDay.after(iso, n)` gives n days after.
- `houseDay.date()` gives the house's today as a local `Date` at midnight, for
  the calendars.

`book_room.html` sets `arrive.min = houseDay.today()` and
`depart.min = houseDay.after(arrive.value)`. The room page's calendar and
`_availcal.html` use `houseDay.date()` for today.

**4. The account page's expiry date** (`guest_account.html`) was
`{{ expires[:10]|date_short }}`. `expires` is a stored UTC moment, and `[:10]`
cut it to UTC's day before `date_short` could convert it. It is now
`{{ expires|date_short }}`: `date_short` gives the house's day of a moment on its own.

**If a handover replaces these files:** keep the `houseDay` script in the head.
Every picker calls it, and without it they throw and stop working altogether.
Never take a day from `toISOString()`, and never slice a stored moment to ten
characters (`|date_short` or `|house_day` instead). `test_utc_slices` fails on
all of these, and it runs the room page and the event enquiry in headless
Chrome at a frozen 00:30 to read what the pickers allow.
