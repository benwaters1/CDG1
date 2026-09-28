# The mobile round is the best work yet. Four things to carry back.

The phone numbers changed the whole picture and you were right to lead with it.
Verified at 375px on our side: room cards scroll instead of stacking, the
comparison table scrolls with the name pinned, eleven sections fold, and the
document still doesn't scroll sideways. Desktop is untouched. All of it in.

`syncfixture.py` reading `ROOM_IDENTITY` out of `app.py` and **failing when a
template reads a column the fixture doesn't set** is the most valuable thing in
this handover. It found seven at once. That's the tool that prevents the next
"the picker is broken" when the picker was never broken.

The redirects are live — 19 of the 24. Details below.

---

## 1. The bar nagged people to go where they already were

`public_base.html` excluded `book_room` — a *single room's* page — but not
`book_rooms`, which is `/book` itself.

So on the one page where a guest is already choosing a room, the bar sat there
offering **"Check dates"** as a link to that page.

Fixed by adding `book_rooms` to the tuple. `/book/4`, `/book/manage` and the
confirmation were all correctly excluded already.

There's now a test that renders every page and checks the bar is present where
it should be, absent where it shouldn't, and — specifically — never links to
the page it's drawn on.

**A note on how we checked this:** the bar initially appeared not to work at
all in our test browser. It does. Our preview pane doesn't composite frames, so
the CSS transition freezes mid-flight and the transform never lands. Your code
is correct. Mentioning it in case you hit the same false alarm.

## 2. `social.html`'s og:image points at nothing

New this round:

```jinja
{{ site_image('chateau.facade') or url_for('static', filename='img/og-default.jpg') }}
```

Both halves are wrong. `chateau.facade` isn't one of the fifteen slots, and
`img/og-default.jpg` has never existed. So the tag resolves to a **404**, and
every share of that page shows a broken preview.

Nothing reports this, because that URL is only ever fetched by somebody else's
server.

Changed to `site_image('home.hero')` with `img/chateau_facade_formal.jpg` as
the fallback — both of which exist. The real slot list is:

```
home.hero          home.restoration   home.table
press.hero         restaurant.hero    restaurant.chef
workshops.hero     facilities.hero    restoration.hero
gallery.salon_before   gallery.salon_after
```

## 3 & 4. Same two as last time, and now I know why

- **`restoration.html`** — doubled site id, **fourth round**
- **`workshops_public.html`** — "we do not overbook", **third round**

Last time both files were byte-identical to the previous zip, so I said they
were stale carry-forwards. **This round they were genuinely re-cut** — different
byte counts, real changes inside. So that explanation is gone, and your note
tells me the actual cause:

> "No more whole-file takes. I refreshed only the 319 templates I have not
> edited."

**The templates you have edited are exactly the templates our fixes live in.**
Your refresh policy excludes precisely the files where the two sides diverge.
That's why these two come back every single round while everything else is now
clean.

You were right that taking main's copy wholesale reverted your reordering
twice. The answer isn't "take ours" or "keep yours" — it's a merge on those few
files. Realistically there are only two of them, and both are one-line fixes:

```
restoration.html:  content/53c3b576e4b02bad423517b8/v1/53c3b576e4b02bad423517b8/
        should be: content/v1/53c3b576e4b02bad423517b8/          (×2, Squarespace 400s the doubled one)

workshops_public.html:  keep the sentence "we do not overbook a house this size"
                        anywhere on the page — it's a promise the code keeps
                        with a write lock, and there's a test file holding the
                        software to it
```

Apply those two to your working copy once and they stop recurring.

## The three partials, fourth round

`_devices.html`, `_guest_extras.html`, `_prearrival.html` — byte-identical
again, removed again.

**The crest offer stands and I'd like to take it.** Send the laurel-wreath
drawing as a replacement for the `monogram` macro *inside* `_marks.html` and
it's in that day. What's refused is two files defining the same macro, not the
drawing — which is better than the one in use.

## The redirects are live

19 of the 24 answer **301** now. Five are skipped because they already resolve
to the page your list wants: `/book`, `/workshops`, `/restaurant`, `/gallery`,
`/events`. Adding redirects for those would put a second rule on a live route,
and two routes claiming one address is a fault this app has had before.

`REDIRECTS.txt` is read at boot rather than copied into the code, so **you can
add lines to it and they take effect without a code change**. A malformed line
costs that one redirect and nothing else.

## Still yours to answer

Unchanged from last time, plus one:

- **The review figures** — built and switched off for five rounds now. This one
  is on the owner, not you.
- **Restoration: what is it *for*?** You're right that 29 distinct sections
  can't be cut further without deciding that.
- **The Stay page** — "Things to Do With the Days", 4.3 screens, duplicates
  What's On.
- **A dark logo file**, so the invert filter can go.

## Flagged, not claimed — thank you

Listing the step indicator, the end-to-end booking test and the French pass on
~40 strings as *not done* rather than quietly leaving them is worth more than
it might seem. It's the difference between a status report and a guess.
