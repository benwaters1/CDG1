"""Run the whole test suite.

    python tests/run.py            all suites
    python tests/run.py routes     just the ones whose name contains "routes"

Nothing to install: no pytest, no fixtures, no database of its own to set up.
The suite copies the current database to a throwaway file and runs against
that, so it never touches real staff or guest records and leaves nothing
behind. Exits non-zero if anything failed, so CI can use it as-is.
"""
import os
import glob
import sys
import traceback

import _harness  # noqa: F401  — sets GUDANES_DB_PATH before app is imported

SUITES = [
    "test_auth",
    "test_routes",
    "test_staff_today",
    "test_owner_home",
    "test_chat",
    "test_access_levels",
    "test_booking_quote",
    "test_booking_bill",
    "test_booking_lifecycle",
    "test_booking_payment",
    "test_guest_portal",
    "test_house_capacity",
    "test_availability_calendar",
    "test_workshop_money",
    "test_part_payments",
    "test_autocharge",
    "test_workshop_rooms",
    "test_workshop_lifecycle",
    "test_solo_occupancy",
    "test_workflows",
    "test_operations",
    "test_approvals_money",
    "test_closing_loops",
    "test_list_view",
    "test_consequences",
    "test_pos",
    "test_service_day",
    "test_after_midnight",
    "test_pos_journal",
    "test_pos_archive",
    "test_menu_day",
    "test_card_capacity",
    "test_dietary_clashes",
    "test_menu_read",
    "test_formule",
    "test_beverage_pours",
    "test_packages",
    "test_insurance",
    "test_vault",
    "test_contract_deadlines",
    "test_performance_reviews",
    "test_offboarding",
    "test_account_access",
    "test_hr_notes",
    "test_profile_privacy",
    "test_onboarding_kit",
    "test_payroll_blockers",
    "test_pos_floor",
    "test_leave_accrual",
    "test_vat_working",
    "test_ical_sync",
    "test_waitlist",
    "test_stock_ledger",
    "test_waitlist_other",
    "test_house_crud",
    "test_hr_compliance",
    "test_campaign_email",
    "test_email_outbox",
    "test_email_templates",
    "test_newsletter",
    "test_gallery",
    "test_exports",
    "test_destructive",
    "test_money_ahead",
    "test_maintenance",
    "test_backup_alert",
    "test_payments",
    "test_stripe_webhook",
    "test_stripe_price_drift",
    "test_promo_privacy",
    "test_promo_blast",
    "test_terminal",
    "test_arrive",
    "test_timesheet_repair",
    "test_staff_lifecycle",
    "test_hr_records",
    "test_vehicles",
    "test_offline",
    "test_translations",
    "test_staff_language",
    "test_whats_on",
    "test_ateliers",
    "test_navigation",
    "test_nav_reachable",
    "test_front_page",
    "test_confirmations",
    "test_social_schedule",
    "test_design",
    "test_table_overflow",
    "test_links",
    "test_error_pages",
    "test_seo_files",
    "test_rota_clashes",
    "test_cover_gaps",
    "test_rota_vs_clock",
    "test_hr_management_links",
    "test_skills",
    "test_overtime",
    "test_repeat_guests",
    "test_room_economics",
    "test_still_out",
    "test_sick_note",
    "test_insurer_notice",
    "test_home_warnings",
    "test_watch_tasks",
    "test_job_outcomes",
    "test_public_writes",
    "test_connection_hygiene",
    "test_security_headers",
    "test_money_routes",
    "test_wages",
    "test_outlook",
    "test_booking_email",
    "test_booking_form_errors",
    "test_abandoned_checkout",
    "test_room_deposits",
    "test_ics_feeds",
    "test_shift_actions",
    "test_guest_account",
    "test_bulk_confirm",
    "test_task_admin",
    "test_payment_landing",
    "test_room_order",
    "test_guest_amendments",
    "test_room_balance_reminder",
    "test_my_hours",
    "test_pay_reviews",
    "test_money_reports",
    "test_noindex_meta",
    "test_privacy",
    "test_refunds",
    "test_money_out",
    "test_finance_functions",
    "test_outbox_lock",
    "test_form_prefill",
    "test_company_records",
    "test_estate",
    "test_pennylane_send",
    "test_pricing",
    "test_promo_and_ledger",
    "test_public_forms",
    "test_phone_numbers",
    "test_reset_code",
    "test_texting",
    "test_checkin_texts",
    "test_room_feedback",
    "test_digest_and_hub",
    "test_guest_self_service",
    "test_private_urls",
    "test_optout_and_flags",
    "test_till_and_toggles",
    "test_expense_files",
    "test_guest_bill_loop",
    "test_outstanding",
    "test_auto_receipt",
    "test_part_payment",
    "test_walk_in",
    "test_owner_writes",
    "test_shell_cache",
    "test_city_tax",
    "test_event_money",
    "test_event_terms",
    "test_no_email_guest",
    "test_pennylane_revenue",
    "test_booking_race",
    "test_restore_drill",
    "test_backup_resilience",
    "test_comms_and_escalation",
    "test_house_config",
    "test_accessibility",
    "test_supplier_invoices",
    "test_staff_claims",
    "test_reviews",
    "test_vehicle_papers",
    "test_cover_register",
    "test_police_register",
    "test_dead_context",
    "test_extras_cancel",
    "test_newsletter_reach",
    "test_data_requests",
    "test_room_board",
    "test_template_shadowing",
    "test_empty_nights",
    "test_booking_parties",
    "test_guest_record",
    "test_handover_check",
    "test_repair_handover",
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


def _registry_complete():
    """Prove every suite on disk is one this runner actually runs.

    SUITES is a hand-kept list, not a glob. A file that never gets added to it
    is not a suite that fails - it is a suite that silently does not exist,
    while the file sits in the tree looking like coverage and the total at the
    bottom counts only the ones that ran. That is the same failure the positive
    control guards against from the other side: the run looks green because
    nothing asked the question.

    Written as a self-check rather than as a suite so that it reports even when
    the run is filtered down to a single name.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    on_disk = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(here, "test_*.py"))}
    missing = sorted(on_disk - set(SUITES))
    ghosts = sorted(set(SUITES) - on_disk)
    ok = not missing and not ghosts
    print(f"  {'PASS' if ok else 'FAIL'}  every suite file is registered "
          f"({len(on_disk)} on disk)")
    if missing:
        print("        written but never run: " + ", ".join(missing))
    if ghosts:
        print("        registered but the file is gone: " + ", ".join(ghosts))
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
    registry_ok = _registry_complete()

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

    # What the suite actually reached. Printed even when everything passes,
    # because a green run over half the app is exactly the failure this guards
    # against — "we have tests" and "this page is tested" are different claims,
    # and only one of them is checkable.
    if not wanted:
        try:
            hit, miss, by_area = _harness.coverage_report()
            print("\n" + "=" * 64)
            pct = 100 * len(hit) / max(1, len(hit) + len(miss))
            print(f"COVERAGE — {len(hit)} of {len(hit) + len(miss)} pages exercised ({pct:.0f}%)")
            for area in sorted(by_area):
                got, lost = by_area[area]["hit"], by_area[area]["miss"]
                line = f"  {area:<14} {len(got):>3}/{len(got) + len(lost):<3}"
                if lost:
                    line += "  untested: " + ", ".join(lost[:3])
                    if len(lost) > 3:
                        line += f" +{len(lost) - 3} more"
                print(line)
        except Exception as e:                       # pragma: no cover
            print(f"\n(coverage report unavailable: {e})")

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
    if not registry_ok:
        print("\nA suite file was written and never registered — the total"
              " above does not cover it.")
    return 0 if (not all_failed and not crashed and control_ok and registry_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
