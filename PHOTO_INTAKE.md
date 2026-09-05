# Photo intake — what the backend needs

The page is `admin_photo_intake.html` and it is finished. It needs one column,
one route and one helper. Everything below matches the patterns already in
`app.py` rather than introducing new ones.

---

## 1. The migration

`social_posts` has a caption but no picture, which is why a post is currently
a sentence with nothing attached. Two columns, in the style of the existing
list around line 3577:

```python
("social_posts_image", "ALTER TABLE social_posts ADD COLUMN image_filename TEXT"),
("social_posts_alt",   "ALTER TABLE social_posts ADD COLUMN alt_text TEXT"),
```

`alt_text` is stored on the post rather than the file because the same
photograph can be used twice and mean something different each time.

---

## 2. Allowed file types

`ALLOWED_EXTENSIONS` (line 178) has no `webp` or `heic`. Every iPhone shoots
HEIC by default, so a photograph transferred from a camera to a phone and
uploaded from there will be rejected today.

```python
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "heic", "heif"}
```

Keep it separate from `ALLOWED_EXTENSIONS` — a photo upload should not accept
`.docx`, and the document upload should not accept `.heic`.

**HEIC will not display in most browsers.** Either convert on upload with
Pillow + `pillow-heif`, or reject it with a message saying so. Silently
storing a file nobody can see is the worse option.

---

## 3. The route

```python
@app.route("/admin/photos", methods=["GET", "POST"])
@login_required            # whatever decorator the other admin routes use
def photo_intake():
    if request.method == "POST":
        photo = request.files.get("photo")
        if not (photo and photo.filename and allowed_image(photo.filename)):
            flash("That was not a photograph we can store.", "error")
            return redirect(url_for("photo_intake"))

        safe = secure_filename(photo.filename)
        name = f"photo_{secrets.token_hex(6)}_{safe}"
        photo.save(os.path.join(UPLOAD_DIR, name))

        conn = get_db()
        plan_id = request.form.get("plan_id") or None
        when    = request.form.get("scheduled_date") or None

        # If a plan was chosen and no date given, take the next free slot.
        if plan_id and not when:
            when = next_free_slot(conn, plan_id)

        conn.execute(
            """INSERT INTO social_posts
               (platform, caption, image_filename, alt_text, plan_id,
                scheduled_date, status, created_by_user_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (platform_for(conn, plan_id),
             request.form.get("caption", "").strip(),
             name,
             request.form.get("alt_text", "").strip() or None,
             plan_id, when,
             "drafted" if request.form.get("caption", "").strip() else "idea",
             current_user_id(), now_iso()))
        conn.commit()
        flash("Photograph saved." + (f" Slotted for {when}." if when else ""), "ok")
        return redirect(url_for("photo_intake"))

    conn = get_db()
    return render_template(
        "admin_photo_intake.html",
        social_plans=conn.execute(
            "SELECT * FROM social_plans WHERE active = 1 ORDER BY name").fetchall(),
        rooms=conn.execute(
            "SELECT id, name FROM rooms WHERE is_active = 1 ORDER BY id").fetchall(),
        recent=conn.execute(
            """SELECT image_filename, alt_text, scheduled_date, status
               FROM social_posts
               WHERE image_filename IS NOT NULL
               ORDER BY id DESC LIMIT 12""").fetchall())
```

**Status is `idea` when the caption is blank and `drafted` when it is not** —
so dropping a photo in without writing anything leaves an honest to-do rather
than a post that looks finished.

---

## 4. `next_free_slot(conn, plan_id)`

The plan already carries `cadence`, `weekday`, `day_of_month` and `post_time`.
Walk forward from today to the next date matching the cadence that has no
`social_posts` row against that plan, and return it as ISO.

This is the whole point of the page: a photograph lands on a real date without
anyone opening a calendar.

---

## 5. Two things the page needs that may not exist

  - **`uploaded_file`** — the recent strip uses `url_for('uploaded_file',
    filename=...)`. If your serving route has another name, change the two
    references in the template.
  - **`social_plans.active`** — the query above assumes it. If the column is
    not there, drop the `WHERE`.

---

## What the page deliberately does not do

**It does not generate the caption.** It reads the file size, the date, the
dimensions and whether the frame is portrait or landscape, shows the plan's
standing brief beside the box, and then stops.

A generated caption about a French château would read like every other
generated caption, and the writing is the reason the site works. What the page
removes is the drudgery — the date, the slot, the alt text nobody fills in —
not the voice.

**It does not publish.** Nothing here posts to Instagram or Facebook. It
creates a draft in the plan you already have. Publishing means Meta's Graph
API, a business account, app review and tokens that expire — a separate piece
of work, and worth doing only once you find the drafts are piling up.

## What it does check, in the browser, before anything uploads

  - a preview, so you can see you picked the right frame
  - size, capture date, pixel dimensions, and portrait / landscape / square
  - **RAW detection** — a `.CR2` or `.CR3` off a Canon cannot be previewed or
    stored, and it says so at the drop zone rather than after a failed upload

---

# The darkroom, and the caption

## What the browser does — real, tested, no service involved

Exposure, contrast, warmth and colour, applied in a canvas before upload, so
what you see is what gets stored. Plus crops for square, 4:5 and 16:9.

**Auto is a histogram stretch.** It measures the 0.4th and 99.6th percentile
of luminance in the *original*, and pulls those to black and white. Tested on
a deliberately flat frame: it set contrast to 50 and exposure to +12, which is
correct for that image. It reads the original each time, so pressing it twice
does not compound.

It is not AI and I have not called it AI in the interface. It is arithmetic,
it is predictable, it runs on the device, and nothing is sent anywhere.

**Working size is capped at 2048px.** A 6000px Canon frame does not need
editing at full resolution in a browser and Instagram will not use it.

## The route needs one more branch

The edited picture arrives as a data URL in `edited`, and the file input is
cleared when an edit is made, so you get one or the other, never both:

```python
edited = request.form.get("edited", "")
if edited.startswith("data:image/jpeg;base64,"):
    raw = base64.b64decode(edited.split(",", 1)[1])
    if len(raw) > 12 * 1024 * 1024:          # a cap, as with any upload
        flash("That photograph is too large.", "error")
        return redirect(url_for("photo_intake"))
    name = f"photo_{secrets.token_hex(6)}.jpg"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
        fh.write(raw)
else:
    ... the file-upload branch already specified above ...
```

**Validate it is actually a JPEG** before writing — decode the header with
Pillow rather than trusting the prefix. A base64 field is a file upload
wearing a different hat and deserves the same suspicion.

---

## The Instagram caption

You have asked twice, so here is how to build it properly rather than me
declining again.

**Do not send the photograph to a vision model.** It costs more, it is slower,
and it will describe a stone room as "a rustic interior with vintage charm" —
which is the voice you have spent this whole rebuild removing from the site.

**Send the facts you already hold.** The room name, the plan's theme, the
plan's standing brief, the month, and the alt text you just typed. That is
enough for a first draft and it stays in your register because the brief is in
your words.

```python
@app.post("/admin/photos/caption")
def photo_caption():
    plan = plan_row(request.form.get("plan_id"))
    prompt = (
        "Write one Instagram caption for Château de Gudanes, a Class I "
        "historic monument under restoration in the Ariège.\n\n"
        f"The photograph shows: {request.form['alt_text']}\n"
        f"Where: {request.form.get('room') or 'the château'}\n"
        f"Month: {date.today():%B}\n"
        f"This slot is for: {plan['theme']}\n"
        f"The standing brief: {plan['brief']}\n\n"
        "Two sentences at most. Plain, specific, no adjectives doing work a "
        "noun could do. Never 'nestled', 'stunning', 'hidden gem', 'timeless' "
        "or 'step back in time'. State a fact about the building or the day. "
        "No hashtags, no emoji, no exclamation marks."
    )
```

**Put it beside the box, not in it.** A "Suggest" button that fills an empty
textarea you then rewrite. If it ever silently replaces something you have
typed, you will stop trusting the page.

**The honest expectation:** it will save you the blank page, and you will
rewrite most of it. That is still worth having. What it will not do is sound
like you, and if it starts to, the site loses the thing that makes it read as
written by a person who lives there.
