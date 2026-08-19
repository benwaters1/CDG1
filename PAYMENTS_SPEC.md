# Part-payments and auto-charge on the due date

What you described: a guest can pay the balance, or **part of it**, any time
they like; on the due date whatever is left is **charged automatically** to the
card they registered with.

Most of this already exists. Below is only what is missing.

## What is already built — do not rebuild

- `workshop_transactions` — a proper ledger of charge / discount / payment / refund
- `workshop_balance_due(conn, booking_id)` → `(balance_due, total_charged, total_paid)`
- `add_workshop_transaction(...)` for writing to it
- `balance_due_date` on the booking, set to start date − 30 days
- A job runner with daily jobs registered in the same table as `daily_digest`
- A balance reminder email 7 days before

So partial payments are **already representable** — the ledger sums them
correctly. What is missing is a way for a guest to *make* one, and a way to
charge the remainder without them.

---

## Part 1 — let a guest pay part of the balance

### 1a. Accept an amount on the pay-balance route

`workshop_pay_balance` currently charges the whole balance. Let it take an
optional amount.

```python
@app.route("/workshops/pay-balance/<manage_token>", methods=["GET", "POST"])
def workshop_pay_balance(manage_token):
    conn = get_db()
    booking = conn.execute(
        "SELECT id FROM workshop_bookings WHERE manage_token = ?", (manage_token,)
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)

    # An optional part-payment. Absent or junk means "the whole balance",
    # which keeps every existing link working unchanged.
    part = parse_money(request.form.get("amount")) if request.method == "POST" else None
    if part is not None:
        due, _, _ = workshop_balance_due(conn, booking["id"])
        # Never take more than is owed, and never take a token amount that
        # costs more in Stripe fees than it settles.
        if part < 20 or part > due:
            conn.close()
            flash(f"Enter an amount between €20 and €{due:.2f}.", "error")
            return redirect(url_for("workshop_manage", manage_token=manage_token))

    checkout_url = start_workshop_stripe_payment(conn, booking["id"], "balance", amount_override=part)
    conn.close()
    if not checkout_url:
        flash("This balance can't be paid online right now — contact the château directly.", "error")
        return redirect(url_for("workshop_manage", manage_token=manage_token))
    return redirect(checkout_url, code=303)
```

### 1b. Honour the override in the Stripe helper

In `start_workshop_stripe_payment`, add the parameter and use it:

```python
def start_workshop_stripe_payment(conn, booking_id, kind, amount_override=None):
    ...
    else:  # the existing balance branch
        amount, _, _ = workshop_balance_due(conn, booking_id)
        if amount_override is not None:
            amount = amount_override
        blocked = not booking["deposit_paid_at"]
        label = "Part payment" if amount_override is not None else "Balance"
```

The ledger already handles the rest: line 20492 writes a `payment` row of
whatever was actually charged, so two €500 payments against a €1,000 balance
leave €0 due without any further change.

### 1c. The form on the manage page

In `workshop_manage.html`, beside the existing pay-balance button:

```html
{% if balance_due > 0 %}
<form method="post" action="{{ url_for('workshop_pay_balance', manage_token=booking['manage_token']) }}" class="g-partpay">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <label for="amount">Pay part of the balance</label>
  <div class="g-promo">
    <input type="number" id="amount" name="amount" min="20" max="{{ '%.2f'|format(balance_due) }}"
           step="0.01" placeholder="{{ '%.2f'|format(balance_due) }}">
    <button type="submit">Pay this amount</button>
  </div>
  <p class="g-hint">€{{ '%.2f'|format(balance_due) }} outstanding. Pay any part of it now, or leave it —
    the remainder is charged automatically on {{ booking['balance_due_date'] }}.</p>
</form>
{% endif %}
```

---

## Part 2 — auto-charge the remainder on the due date

This is the part that does not exist at all. Stripe Checkout currently runs in
one-off mode, so **no card is kept** and nothing can be charged later.

### 2a. Save the card at deposit time

In `start_workshop_stripe_payment`, on the **deposit** branch only:

```python
checkout_session = stripe.checkout.Session.create(
    mode="payment",
    payment_method_types=["card"],
    # Keep the card so the balance can be taken on the due date without the
    # guest coming back. Only on the deposit — later payments reuse it.
    payment_intent_data={
        "setup_future_usage": "off_session",
    } if kind == "deposit" else {},
    customer_email=booking["guest_email"],
    ...
)
```

### 2b. Store the customer and payment method

Two new columns:

```sql
ALTER TABLE workshop_bookings ADD COLUMN stripe_customer_id TEXT;
ALTER TABLE workshop_bookings ADD COLUMN stripe_payment_method_id TEXT;
ALTER TABLE workshop_bookings ADD COLUMN autocharge_failed_at TEXT;
ALTER TABLE workshop_bookings ADD COLUMN autocharge_opt_out INTEGER NOT NULL DEFAULT 0;
```

In the Stripe success handler where the deposit payment is recorded, retrieve
the PaymentIntent and keep both IDs:

```python
pi = stripe.PaymentIntent.retrieve(session.payment_intent)
conn.execute(
    "UPDATE workshop_bookings SET stripe_customer_id = ?, stripe_payment_method_id = ? WHERE id = ?",
    (pi.customer, pi.payment_method, booking_id),
)
```

### 2c. The daily job

Register alongside `daily_digest` in the jobs table:

```python
def run_workshop_autocharge_job(conn):
    """Charges whatever is still owed on any booking whose balance falls due
    today. Off-session, so it can fail for reasons the guest must fix — a
    failure is recorded and the château is told, never retried blindly."""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute(
        """SELECT id, manage_token, guest_name, guest_email, reference_code,
                  stripe_customer_id, stripe_payment_method_id
             FROM workshop_bookings
            WHERE balance_due_date IS NOT NULL
              AND balance_due_date <= ?
              AND status = 'confirmed'
              AND autocharge_opt_out = 0
              AND autocharge_failed_at IS NULL
              AND stripe_payment_method_id IS NOT NULL""",
        (today,),
    ).fetchall()

    charged, failed = 0, 0
    for r in rows:
        due, _, _ = workshop_balance_due(conn, r["id"])
        if due <= 0:
            continue                      # already settled, in part or in full
        try:
            pi = stripe.PaymentIntent.create(
                amount=int(round(due * 100)),
                currency="eur",
                customer=r["stripe_customer_id"],
                payment_method=r["stripe_payment_method_id"],
                off_session=True,
                confirm=True,
                description=f"Balance — {r['reference_code']}",
                idempotency_key=f"wsbal-{r['id']}-{today}",   # never double-charge
            )
            add_workshop_transaction(conn, r["id"], "payment",
                                     "Balance — automatic", due, method="stripe")
            charged += 1
        except stripe.error.CardError as e:
            # Declined, or SCA needed. Both need the guest, so stop and ask.
            conn.execute("UPDATE workshop_bookings SET autocharge_failed_at = ? WHERE id = ?",
                         (datetime.now(timezone.utc).isoformat(), r["id"]))
            send_balance_payment_failed_email(conn, r, str(e.user_message or e))
            failed += 1
    conn.commit()
    return {"charged": charged, "failed": failed}
```

Register it:

```python
("workshop_autocharge", "automation_workshop_autocharge_enabled", None,
 24 * 3600, run_workshop_autocharge_job),
```

---

## The failure paths — these matter more than the happy one

**`idempotency_key` is not optional.** If the job runs twice — a retry, a
restart, two workers — Stripe returns the first charge instead of making a
second. Without it you can double-charge €4,800.

**`autocharge_failed_at` stops the loop.** A declined card must not be retried
every day. One attempt, then the guest is emailed a link to pay manually, and
the château sees it in the daily digest.

**SCA will happen.** European cards can require the guest to authenticate,
which an off-session charge cannot do. Stripe raises `CardError` with
`authentication_required`; the guest gets the same "please pay manually" mail.

**Partial payments shrink the auto-charge automatically.** The job reads
`workshop_balance_due()` at the moment it runs, so somebody who paid €500 of
€1,000 last week gets charged €500, not €1,000.

**Tell the guest before it happens.** The existing 7-day reminder should say
plainly that the card will be charged, and give the opt-out link — being
charged unexpectedly is worse than being asked.

**An opt-out is worth having.** `autocharge_opt_out` lets a guest who would
rather pay by transfer say so, without you editing the database.

---

## Order of work

1. Part 1 alone (1a–1c) — small, self-contained, no Stripe mode change,
   and useful on its own.
2. Then 2a–2b, and confirm the IDs are landing on real bookings.
3. Then the job, with the email and opt-out in place **before** it is enabled.

Do not enable the job until a test booking has been charged end to end in
Stripe test mode, including a deliberately declined card.
