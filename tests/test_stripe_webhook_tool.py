"""tools/stripe_webhook.py: the live webhook made at the app's API version.

The account's default API version is 2014-07-22 and the dashboard offers only
that or the newest; the app's library pins stripe.api_version. The tool makes the
endpoint at that version, asks for every event the webhook acts on, and never
prints the key or the signing secret. Stripe is never called here.
"""
import contextlib
import importlib.util
import io
import os
import re
import tempfile

from _harness import Suite, ROOT
import _harness

m = _harness.m


def _tool():
    spec = importlib.util.spec_from_file_location(
        "stripe_webhook_tool", os.path.join(ROOT, "tools", "stripe_webhook.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Obj:
    """Like a Stripe object: fields by [] only, and no .get, as stripe 15 has."""
    def __init__(self, **fields):
        self._f = fields

    def __getitem__(self, name):
        return self._f[name]


class _Page(list):
    def auto_paging_iter(self):
        return iter(self)


def run():
    s = Suite("The Stripe webhook tool")
    tool = _tool()
    stripe = tool.stripe

    s.section("It asks for every event the webhook acts on")
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    start = src.index('@app.route("/webhooks/stripe"')
    handler = src[start:src.index("\n@app.route(", start + 10)]
    named = set(re.findall(r'"((?:checkout|charge|refund|payment_intent)\.[a-z_.]+[a-z_])"',
                           handler))
    missing = sorted(e for e in named if e not in tool.EVENTS)
    s.check("every event the handler names is in the list", not missing and named,
            detail=f"{missing} — Stripe would never send it, and the booking it "
                   "confirms would never be confirmed")
    s.check("and the disputes it handles by prefix",
            'startswith("charge.dispute.")' in handler
            and {"charge.dispute.created", "charge.dispute.closed"} <= set(tool.EVENTS))

    s.section("It makes the endpoint at the app's version, and keeps the secret out of sight")
    calls = {}
    fake_key, fake_secret = "sk_test_zzfake" + "k" * 20, "whsec_zzfake" + "s" * 20
    saved = (stripe.WebhookEndpoint.list, stripe.WebhookEndpoint.create, stripe.api_key,
             os.environ.get("STRIPE_SECRET_KEY"))
    try:
        stripe.WebhookEndpoint.list = staticmethod(lambda **kw: _Page())
        stripe.WebhookEndpoint.create = staticmethod(lambda **kw: calls.update(kw) or _Obj(
            id="we_zz", secret=fake_secret, api_version=kw.get("api_version")))
        os.environ["STRIPE_SECRET_KEY"] = fake_key
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            code = tool.main(["--create"], out_dir=tmp)
            written = open(os.path.join(tmp, ".env.stripe-webhook-test"), encoding="utf-8").read()
        said = out.getvalue()
    finally:
        stripe.WebhookEndpoint.list, stripe.WebhookEndpoint.create, stripe.api_key = saved[:3]
        if saved[3] is None:
            os.environ.pop("STRIPE_SECRET_KEY", None)
        else:
            os.environ["STRIPE_SECRET_KEY"] = saved[3]
    s.check("it created the endpoint", code == 0, detail=said[-300:])
    s.check("pinned to the version the app's library speaks",
            calls.get("api_version") == stripe.api_version, detail=str(calls.get("api_version")))
    s.check("for the site's webhook address", calls.get("url", "").endswith("/webhooks/stripe"))
    s.check("asking for every event in the list", calls.get("enabled_events") == tool.EVENTS)
    s.check("the secret went to the file", fake_secret in written)
    s.check("and neither the key nor the secret was printed",
            fake_key not in said and fake_secret not in said, detail=said[-300:])
    s.check("the file it writes is kept out of git",
            re.search(r"(?m)^\.env\.\*\s*$", open(os.path.join(ROOT, ".gitignore"),
                                                   encoding="utf-8").read()) is not None)

    s.section("It refuses rather than guesses")
    os.environ.pop("STRIPE_SECRET_KEY", None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = tool.main(["--create"])
    if saved[3] is not None:
        os.environ["STRIPE_SECRET_KEY"] = saved[3]
    s.check("with no key set it does nothing", code == 2 and "Nothing was done" in out.getvalue())
    s.check("a restricted live key reads as live", tool.key_mode("rk_live_x") == "live")
    s.check("and the app agrees", m.stripe_key_mode("rk_live_x") == "live")
    return s


if __name__ == "__main__":
    print(run().report())
