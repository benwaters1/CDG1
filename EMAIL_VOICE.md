# The emails are in a different voice from the site

Seventeen admin-editable templates. They are functional and none is broken —
but every one of them uses contractions, exclamation-adjacent warmth and
generic hospitality phrasing, while the site itself is composed, unhedged and
never uses a contraction. A guest who books through the site and then reads
the email is hearing two different houses.

These are the four worth changing. All are editable in **Admin → Email
templates**, so no code deploy is needed.

---

## 1. `room_waitlist_opening` — tells the guest someone else's business

Current:

> A booking was just **cancelled or declined** that overlaps the dates you're
> interested in… book now before it's taken again

Two problems. "Cancelled **or declined**" tells a stranger that someone else
was refused — which is not their business and is faintly alarming. And "before
it's taken again" is pressure of the kind the site deliberately avoids.

Replace with:

> Hi {name},
>
> A room has come free for {desired_arrival} to {desired_departure} — the dates
> you asked about.
>
> Waitlist rooms rarely stay open for long, so if you would still like them:
> {book_url}
>
> — Château de Gudanes

Same urgency, stated rather than pressed, and it does not disclose why the
room is free.

---

## 2. `workshop_balance_reminder` — "a friendly reminder"

Current opens *"A friendly reminder that the balance of €X…"*. Announcing that
a reminder is friendly is what makes it read as not being.

> Hi {guest_name},
>
> The balance of €{balance_amount} for {workshop_title} ({dates}) is due on
> {balance_due_date}.
>
> You can pay it, or change how it is taken, from your registration:
> {manage_url}
>
> — Château de Gudanes

Note the second line: the autocharge opt-out exists and this is the one mail
where a guest is thinking about payment, so it belongs here.

---

## 3. The three `_declined` templates — all open by apologising

All three begin *"We're sorry — we're unable to…"*. A decline is the email most
likely to be read twice, and the house voice does not contract or apologise
before it explains.

Workshop:

> Hi {guest_name},
>
> We are not able to confirm your registration for {workshop_title} ({dates}) —
> the session has filled.
>
> Other dates are open, and we would be glad to have you on one of them. Write
> to us and we will find one that suits.
>
> — Château de Gudanes

Restaurant:

> Hi {guest_name},
>
> We are not able to seat a party of {party_size} on {dinner_date}. {refund_note}
>
> The kitchen cooks for one table a night and it is already committed. Tell us
> which other evenings suit and we will hold one.
>
> — Château de Gudanes

Event:

> Hi {contact_name},
>
> We are not able to host your {event_type} on the date you asked for — the
> château is already committed that week.
>
> We take one event at a time, which is the reason. Tell us how flexible your
> dates are and we will find one.
>
> — Château de Gudanes

Each now gives a **reason**. "We're unable to" with no explanation reads as a
judgement on the guest; "the session has filled" or "we take one event at a
time" reads as a fact about the château.

---

## 4. Contractions throughout

Every template uses *we're, you're, it's, we'd, don't*. The site uses none —
not as a style rule I imposed, but because the composed register you settled
on does not contract. Worth a pass through all seventeen when you are next in
the admin.

---

## What is already right

`workshop_feedback_request`, `workshop_deposit_receipt` and the three
`_confirmed` templates are fine. They state what happened and stop, which is
the correct register for a receipt.
