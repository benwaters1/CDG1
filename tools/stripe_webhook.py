"""The site's Stripe webhook, created at the API version the app speaks.

    python tools/stripe_webhook.py --list      # every endpoint on the account
    python tools/stripe_webhook.py --create    # the site's own, if it is not there

Run it yourself, with the key set in this terminal only:

    PowerShell:  $env:STRIPE_SECRET_KEY = "<the key>";  python tools/stripe_webhook.py --create
    Git Bash:    STRIPE_SECRET_KEY="<the key>" python tools/stripe_webhook.py --create

WHY NOT THE DASHBOARD (Stripe brief, 15 October 2026). The account's default API
version is 2014-07-22, its original, and the dashboard's "Add destination" offers
only that or the newest major versions. The app's library (stripe==15.4.0) pins
2026-07-29.dahlia, and an endpoint on another version delivers events in another
shape. Created here, the endpoint is pinned to stripe.api_version, the version the
app was written and tested against.

WHAT IT NEVER DOES. It never prints the key or the endpoint's signing secret. The
secret is written to .env.stripe-webhook-<test|live> (gitignored, as every .env.*
is), for you to paste into Railway as STRIPE_WEBHOOK_SECRET and then delete. It
reads nothing from .env: the key is the one you set for this command, so it is
always the key you meant. With a restricted key (rk_live_...), that key needs
"Webhook Endpoints: Write" for --create; take the permission off again afterwards.
"""
import argparse
import os
import sys

import stripe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://cdg1-production-9186.up.railway.app/webhooks/stripe"

# Every event the webhook in app.py acts on. test_stripe_webhook_tool reads the
# handler and fails if it names one this list does not ask Stripe to send.
EVENTS = [
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "charge.refunded",
    "charge.refund.updated",
    "refund.created",
    "refund.updated",
    "refund.failed",
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
    "payment_intent.succeeded",
]


def field(obj, name):
    """A field of a Stripe object, or None. The library's objects are not dicts
    (since stripe 15 they have no .get), so the confirmation that printed with
    .get raised after the endpoint was made -- 15 October, the live run."""
    try:
        return obj[name]
    except (KeyError, TypeError, AttributeError):
        return None


def key_mode(key):
    for prefix, mode in (("sk_test_", "test"), ("rk_test_", "test"),
                         ("sk_live_", "live"), ("rk_live_", "live")):
        if key.startswith(prefix):
            return mode
    return None


def main(argv=None, out_dir=ROOT):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", action="store_true", help="show every endpoint on the account")
    ap.add_argument("--create", action="store_true", help="create the site's endpoint")
    ap.add_argument("--url", default=URL, help="where Stripe should send events")
    args = ap.parse_args(argv)
    if not (args.list or args.create):
        ap.print_help()
        return 2

    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    mode = key_mode(key)
    if not mode:
        print("Set STRIPE_SECRET_KEY in this terminal to a secret (sk_) or restricted (rk_) "
              "key first. Nothing was done.")
        return 2
    stripe.api_key = key
    print(f"Using a {mode} key. The app's API version: {stripe.api_version}.")

    existing = list(stripe.WebhookEndpoint.list(limit=100).auto_paging_iter())
    if args.list:
        if not existing:
            print("No endpoints on this account.")
        for e in existing:
            print(f"  {e['id']}  {e['status']:8s}  version {field(e, 'api_version') or 'account default'}"
                  f"  {len(field(e, 'enabled_events') or [])} event(s)  {e['url']}")
    if not args.create:
        return 0

    same = [e for e in existing if e["url"] == args.url]
    if same:
        print(f"An endpoint for {args.url} already exists ({same[0]['id']}, version "
              f"{field(same[0], 'api_version') or 'account default'}). Nothing was created: "
              "delete it in the dashboard first if it is on the wrong version.")
        return 1

    created = stripe.WebhookEndpoint.create(
        url=args.url, api_version=stripe.api_version, enabled_events=EVENTS,
        description="Chateau de Gudanes site (app.py /webhooks/stripe)")
    path = os.path.join(out_dir, f".env.stripe-webhook-{mode}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"STRIPE_WEBHOOK_SECRET={created['secret']}\n")
    print(f"Created {created['id']} at version {field(created, 'api_version')}, "
          f"{len(EVENTS)} events, for {args.url}.")
    print(f"Its signing secret is in {path}. Paste the value into Railway as "
          "STRIPE_WEBHOOK_SECRET, then delete that file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
