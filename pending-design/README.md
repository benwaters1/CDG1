# Two screens waiting on a decision, not on work

Both arrived complete in the handover of 2026-09-05 and both are held here
rather than in `templates/` for one reason: a template nothing renders reads
as live code. Somebody corrects a word in it, changes nothing anybody can see,
and goes away believing they fixed it. `test_orphan_templates` refuses that on
purpose and has no "pending" list, which is the right call.

Their specs are committed beside this folder as `PASS.md` and
`PHOTO_INTAKE.md`. Nothing here is lost; it is parked.

## pass.html — the screen the restaurant team lives on

A fixed iPad by the pass. Who is eating tonight, what they cannot eat, who has
been here before and what they ate last time, and the store with anything at
or below its reorder level flagged.

Needs from this side: a route, one query joining guests / bookings /
restaurant bookings / stock, and two write-backs — `pass_guest_note` and
`pass_stock_ask`.

The design side's own note says most of it exists already: `guests` carries
`dietary_notes`, `preferences`, `vip` and `notes`; `guest_notes` is the running
record; `repeat_guests()` is written.

**The question is not whether it can be built.** It is that this screen puts a
guest's dietary and medical notes on a shared iPad in a kitchen, left on a
charger, in Guided Access. The privacy notice is a set of testable claims
about this code, and it says those notes are deleted once the event is over.
Putting them on an always-on wall display is a change to who can see them and
for how long, and the notice has to move in the same commit.

The design side also flagged a `messages` table and advised against it:
"the kitchen has WhatsApp and it works". Agreed — and if it is ever built,
urgent has to email or text, because a flag nobody sees is not a flag.

## admin_photo_intake.html — taking photographs in

Needs `photo_intake` and `uploaded_file`.

Check `/admin/images` first. There is already a named-slot system where each
place on the site is named in plain language and the owner uploads what should
be there, with `site_image()` reading it back. This may be a second way to do
the same job, which is the thing that has already been declined five times in
another form.
