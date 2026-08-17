"""Run the whole test suite.

    python tests/run.py            all suites
    python tests/run.py routes     just the ones whose name contains "routes"

Nothing to install: no pytest, no fixtures, no database of its own to set up.
The suite copies the current database to a throwaway file and runs against
that, so it never touches real staff or guest records and leaves nothing
behind. Exits non-zero if anything failed, so CI can use it as-is.
"""
import sys
import traceback

import _harness  # noqa: F401  — sets GUDANES_DB_PATH before app is imported

SUITES = [
    "test_routes",
    "test_hr_compliance",
    "test_campaign_email",
    "test_email_outbox",
    "test_design",
]


def _positive_control():
    """Prove the harness reports a failure when there is one.

    A suite that can only print PASS is worthless, and a broken assertion
    helper is invisible precisely because everything looks green.
    """
    probe = _harness.Suite("control")
    probe.check("a deliberately false check", False)
    ok = probe.failed == ["a deliberately false check"] and probe.passed == 0
    print(f"  {'PASS' if ok else 'FAIL'}  harness reports failures "
          f"(the FAIL line above is expected)")
    return ok


def main(argv):
    wanted = argv[1:]
    names = [n for n in SUITES if not wanted or any(w in n for w in wanted)]
    if not names:
        print(f"No suite matches {wanted}. Available: {', '.join(SUITES)}")
        return 2

    print(f"Database: {_harness.SCRATCH_DB}")
    print("(a throwaway copy — the real one is untouched)")
    print("\n== Self-check")
    control_ok = _positive_control()

    total_passed, all_failed, crashed = 0, [], []
    for name in names:
        print(f"\n== {name}")
        try:
            suite = __import__(name).run()
        except Exception:
            crashed.append(name)
            print(f"    CRASH  {name}")
            traceback.print_exc()
            continue
        total_passed += suite.passed
        all_failed += [f"{suite.name}: {f}" for f in suite.failed]

    print("\n" + "=" * 64)
    total = total_passed + len(all_failed)
    print(f"{total_passed}/{total} checks passed across {len(names)} suite(s)")
    if all_failed:
        print("\nFAILED:")
        for f in all_failed:
            print(f"  - {f}")
    if crashed:
        print("\nCRASHED:", ", ".join(crashed))
    if not control_ok:
        print("\nThe self-check did not behave — treat the run above as unproven.")
    return 0 if (not all_failed and not crashed and control_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
