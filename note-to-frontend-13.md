# For whoever hands over the public templates: the workshop guest page's balance block

**What changed, in `templates/workshop_manage.html`:** the part that tells a
workshop guest what happens to their balance. There were two blocks saying it,
and between them they promised "the balance is taken from the card on file" to
every guest, whether or not a card had been kept. Guests whose deposit kept no
card were being told not to worry about paying.

There is now ONE block, the card titled "The balance", after the payment card.
It says what is actually true, from what is on file:

- opted out: "We will not take it from your card. Please pay it by {date}."
- a card kept (`card_on_file`): "Whatever is left on {date} will be taken from
  the card you paid the deposit with. You can pay it yourself before then."
- deposit not paid yet: "The card you pay the deposit with is kept for the
  balance, and whatever is left on {date} is taken from it."
- deposit paid, no card: "Please pay it by {date}."

The "I would rather pay it myself" checkbox (`name="autocharge_opt_out"`)
appears whenever there is, or will be, a card to take it from.

**What the page is sent:** `card_on_file` (new). `autocharge_enabled` is no
longer sent: the owner now takes balances from the card by hand as well as by
the automatic job, so whether that job is switched on does not decide what the
guest is told.

**If a handover replaces this file:** keep one block, keep the four wordings
keyed on `booking['autocharge_opt_out']`, `card_on_file` and
`booking['deposit_paid_at']`, and keep the opt-out checkbox. `test_autocharge`
and `test_payments_counted_once` fail if the checkbox goes, if the block stops
reading `card_on_file`, or if a guest with no card on file is told their card
will be charged.
