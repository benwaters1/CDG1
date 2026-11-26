"""Production entry point. Gunicorn serves `app` from here.

Running `python app.py` is the development path, and everything inside that
file's `if __name__ == "__main__":` block — creating and migrating the
database, starting the automation loop — never runs under a WSGI server. So a
deployment pointed straight at `app:app` would boot with no tables at all and
500 on every page, and would never send a balance reminder, waitlist opening
or daily digest. This module is the difference.

Deliberately kept separate from app.py rather than doing this work at import:
importing the application should never start a thread that emails people, and
the test suite imports it.
"""
import os

from app import app, init_db, start_background_work, secret_key_problem

# NO SESSION KEY, NO START (second sweep, 26 Nov 2026, B2.3). Without
# FLASK_SECRET_KEY app.py makes a random key, which is right for `python app.py`
# and the tests, and wrong here: every deploy would sign out every member of
# staff and lose every guest's part-filled booking, and nothing would say why.
# So production stops before it serves anything, with the reason in the deploy
# log. Checked before init_db, so a refused start has touched nothing. DEPLOY.md says how to make a key.
_key_problem = secret_key_problem(os.environ.get("FLASK_SECRET_KEY") or "")
if _key_problem:
    raise SystemExit("Refusing to start: the session key FLASK_SECRET_KEY is " + _key_problem[0].lower()
                     + _key_problem[1:] + " Set it in Railway's variables (DEPLOY.md, step 4) and deploy again.")

# Idempotent: creates the schema on a fresh volume and applies any pending
# migrations on an existing one. Must finish before the first request.
init_db()

# One automation loop per process. With more than one gunicorn worker every
# worker would run its own and guests would get each automated email once per
# worker, so the Procfile pins --workers 1 and scales with threads instead.
# GUDANES_NO_AUTOMATION=1 disables it — useful if a second instance is ever
# run purely to serve traffic.
if os.environ.get("GUDANES_NO_AUTOMATION", "0") != "1":
    start_background_work()
