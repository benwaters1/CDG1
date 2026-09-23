# For whoever works on the till: an extra now carries its own VAT rate

**What changed:** an extra in the catalogue can carry its own VAT rate, and
optionally a part of its price at a second rate (`vat_rate`,
`vat_part_amount`, `vat_part_rate` on `extras`). The crémant and charcuterie
board is the reason: the charcuterie at the food rate and the wine's share at
the wine's. When an extra is sold on a booking, `add_booking_extra` copies the
rates onto the line, and the guest's statement, the VAT working and Pennylane
all read them from there.

**What still goes through at one rate is yours:** `pos_add_item`. When the
till sells an extra, it sets the order line's `vat_rate` to
`tax_rate(conn, "vat_extras")`, the house rate, whatever the extra says. So a
board sold at the till would go through at 20% on the whole price.

The helper that does the split is `extra_vat_parts(conn, line, default=...)`.
It takes anything with `unit_price`, `quantity` and the three VAT fields, and
returns `(gross, rate)` pieces. A till line would probably want the extra's
`vat_rate` for the line itself, plus a second line or a split for the part.
How you represent that on `pos_order_lines` is your call; the sealed daily
closure is where it has to add up.

Blank rates are the house's extras rate, exactly as before, so nothing
already sold moves.
