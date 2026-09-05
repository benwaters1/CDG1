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
    "test_workshop_minimum",
    "test_workshop_sheet",
    "test_workshop_materials",
    "test_workshop_alumni",
    "test_workshop_rooming",
    "test_session_capacity",
    "test_solo_occupancy",
    "test_workflows",
    "test_operations",
    "test_areas",
    "test_arrivals_sheet",
    "test_approvals_money",
    "test_closing_loops",
    "test_list_view",
    "test_crawlers_and_proof",
    "test_consequences",
    "test_pos",
    "test_service_day",
    "test_after_midnight",
    "test_pos_journal",
    "test_pos_archive",
    "test_menu_day",
    "test_dinner_covers",
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
    "test_room_waitlist_signup",
    "test_stock_ledger",
    "test_shopping_basket",
    "test_waitlist_other",
    "test_house_crud",
    "test_hr_compliance",
    "test_animals",
    "test_event_run_sheet",
    "test_card_and_rate",
    "test_seven_gaps",
    "test_photo_mirror",
    "test_booking_bar",
    "test_booking_journey",
    "test_funnel_forms",
    "test_pass",
    "test_site_audit",
    "test_house_day",
    "test_house_operations",
    "test_house_reports",
    "test_house_ledger",
    "test_management_reports",
    "test_campaign_email",
    "test_workshop_announce",
    "test_email_outbox",
    "test_email_templates",
    "test_newsletter",
    "test_gallery",
    "test_site_images",
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
    "test_waitlist_offer",
    "test_watch_tasks",
    "test_job_outcomes",
    "test_automation_switches",
    "test_public_writes",
    "test_connection_hygiene",
    "test_security_headers",
    "test_money_routes",
    "test_wages",
    "test_labour_honesty",
    "test_outlook",
    "test_booking_email",
    "test_booking_form_errors",
    "test_abandoned_checkout",
    "test_room_deposits",
    "test_deposit_categories",
    "test_vouchers",
    "test_event_promo",
    "test_city_tax_arrears",
    "test_kitchen_sheet",
    "test_pennylane_split",
    "test_revenue_categories",
    "test_extras_due",
    "test_ics_feeds",
    "test_shift_actions",
    "test_guest_account",
    "test_bulk_confirm",
    "test_bulk_honesty",
    "test_bulk_tasks",
    "test_task_admin",
    "test_payment_landing",
    "test_room_order",
    "test_guest_amendments",
    "test_room_balance_reminder",
    "test_my_hours",
    "test_pay_reviews",
    "test_money_reports",
    "test_budget",
    "test_capital_spend",
    "test_night_cost",
    "test_filling_gaps",
    "test_filings",
    "test_discount_outcomes",
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
    "test_city_tax_charged",
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
    "test_dead_keys",
    "test_extras_cancel",
    "test_newsletter_reach",
    "test_data_requests",
    "test_room_board",
    "test_breakages",
    "test_house_upkeep",
    "test_restaurant_four",
    "test_buying",
    "test_kitchen",
    "test_access",
    "test_rota_templates",
    "test_agreements",
    "test_publish_consent",
    "test_room_checks",
    "test_no_show_rooms",
    "test_orphan_templates",
    "test_guest_page",
    "test_stay_cost",
    "test_next_free",
    "test_share_link",
    "test_travelling_together",
    "test_weather",
    "test_own_record",
    "test_utc_slices",
    "test_read_write_parity",
    "test_no_overbooking",
    "test_add_room",
    "test_cancel_and_reject",
    "test_declines",
    "test_estate_actions",
    "test_api_tokens",
    "test_ical_routes",
    "test_owner_edits",
    "test_provider_off",
    "test_stale_mail",
    "test_awaiting_answer",
    "test_refused_cards",
    "test_voids",
    "test_old_addresses",
    "test_reports_index",
    "test_reopened_bills",
    "test_guest_keys_and_photos",
    "test_recorded_never_read",
    "test_receipt_sequence",
    "test_unasked_questions",
    "test_delivered_and_deposit",
    "test_house_facts_on_pages",
    "test_final_numbers_moved",
    "test_one_off_search_boxes",
    "test_who_decided",
    "test_rate_limit_retention",
    "test_nightly_machinery",
    "test_nightly_jobs",
    "test_who_did_it",
    "test_stated_windows",
    "test_availability_picture",
    "test_empty_form_crashes",
    "test_rendered_markup",
    "test_outbox_retention",
    "test_orphan_pages",
    "test_readiness_on_home",
    "test_deletes",
    "test_payment_returns",
    "test_guests_and_staff",
    "test_template_shadowing",
    "test_empty_nights",
    "test_booking_parties",
    "test_guest_record",
    "test_guest_preferences_apply",
    "test_party_bill",
    "test_split_bill",
    "test_correspondence",
    "test_second_contact",
    "test_record_history",
    "test_clock_change",
    "test_one_search_box",
    "test_review_invitation",
    "test_itinerary",
    "test_price_agreed",
    "test_stay_restamp",
    "test_payment_ledger",
    "test_event_agreement",
    "test_public_calendar",
    "test_event_day",
    "test_event_worth",
    "test_pay_statement",
    "test_calling_it_off",
    "test_guest_record_fields",
    "test_guest_management",
    "test_merge_tags",
    "test_pace",
    "test_booking_source",
    "test_saved_views",
    "test_bookings_list",
    "test_unreachable_code",
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
    # Compared as sets above, which cannot see a name listed TWICE -- and a
    # duplicate runs that suite twice and counts its checks twice, so the
    # total quietly overstates. Found by listing one that was already there.
    seen = set()
    dupes = sorted({n for n in SUITES if n in seen or seen.add(n)})
    ok = not missing and not ghosts and not dupes
    print(f"  {'PASS' if ok else 'FAIL'}  every suite file is registered "
          f"({len(on_disk)} on disk)")
    if missing:
        print("        written but never run: " + ", ".join(missing))
    if ghosts:
        print("        registered but the file is gone: " + ", ".join(ghosts))
    if dupes:
        print("        listed twice, so run and counted twice: "
              + ", ".join(dupes))
    return ok


# Pages the suite reaches and never gets an answer out of. Every one was
# counted as covered until the measure started reading the reply rather than
# the request, and each is here with what it actually replied.
#
# Two shapes, and both mean the same thing -- the working path never runs:
#
#   403  the test proves somebody is REFUSED, which is worth proving and is
#        not a test of what the page does when it is allowed. Eight of the
#        first thirty-eight were deletes.
#   404  the test posts an id that does not exist, so the handler takes its
#        not-found branch and returns. Also worth proving; also not the page.
#
# Thirty-six of the original thirty-eight have come off it, each by having
# the test written. The last two are here because reaching them needs a real
# payment provider, which is a different kind of missing than a missing test.
#
# Three of them were only reachable at all by standing in UNDER the guard
# rather than around it -- app.py imports urlopen by name, so a stand-in in
# its place leaves the real check running and turns any escape into a loud
# failure. The one that reads "Pennylane isn't connected" needed the harness
# to keep the real _pennylane_request for the same reason: the guard lives
# inside the function the harness blocks.
#
# Taking one off this list means writing the test that runs it properly. The
# list is checked in BOTH directions: a fortieth name reds the run, and so
# does a name that starts answering, because an exception list that outlives
# its reason is how the next one gets in unnoticed.
COVERAGE_KNOWN_GAPS = {
    # Two of these need a real payment provider: reaching the branch that matters
    # means a real payment provider, and arranging one in a test is not a
    # thing to do with the château's own Stripe account.
    #
    # refund_restaurant_booking_admin issues money back. Its refusal when
    # Stripe is unconfigured is already held by test_declines, which declines
    # a paid booking and requires the failure to be reported rather than
    # swallowed -- so what is missing here is only the branch where money
    # actually moves.
    #
    # workshop_stripe_success retrieves the checkout session before it does
    # anything, unlike its two siblings, which answer from the database first
    # and are covered (tests/test_payment_returns.py).
    #
    # share_payment_success is workshop_stripe_success again: it retrieves the
    # checkout session as its first act, so with Stripe pinned off there is
    # nothing to answer with. The half that can be tested without a card --
    # what somebody holding a share sees, and when the button disappears --
    # is in tests/test_split_bill.py.
    "refund_restaurant_booking_admin",
    "workshop_stripe_success",
    "share_payment_success",

    # And one where the MEASURE is the awkward part rather than the test.
    # api_draft_reply answers 503 {"error": "not configured"} with no model
    # provider, which is the view running, deciding, and telling the add-in
    # something it can act on -- not a refusal at the door. But a 5xx counts
    # as unanswered here on purpose: loosening that to let this one through
    # would let a genuinely broken page count as covered, which is the whole
    # failure this measure exists to stop. So the judgement sits here, named
    # and reversible, rather than in the rule. tests/test_provider_off.py
    # does exercise it -- the token posture and the 503 both.
    "api_draft_reply",
}


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
    coverage_ok = True
    if not wanted:
        try:
            hit, miss, by_area = _harness.coverage_report()
            print("\n" + "=" * 64)
            # Floored, not rounded. 737 of 740 rounds to 100%, and a
            # headline reading 100% over three outstanding pages is the exact
            # overstatement this measure was changed to stop. It says 100%
            # when it is 100%.
            total_pages = len(hit) + len(miss)
            pct = 100 * len(hit) // max(1, total_pages)
            print(f"COVERAGE — {len(hit)} of {len(hit) + len(miss)} pages answered ({pct}%)")
            for area in sorted(by_area):
                got, lost = by_area[area]["hit"], by_area[area]["miss"]
                line = f"  {area:<14} {len(got):>3}/{len(got) + len(lost):<3}"
                if lost:
                    line += "  untested: " + ", ".join(lost[:3])
                    if len(lost) > 3:
                        line += f" +{len(lost) - 3} more"
                print(line)
            # Reached but never answered. Every one of these was counted as
            # covered until now: the request matched the endpoint and was then
            # turned away at the door, which tests the door and nothing else.
            # Named rather than counted -- a total is not something anybody
            # can act on.
            knocked = _harness.coverage_knocked_only()
            knocked_names = {ep for ep, _seen in knocked}
            fresh = [ep for ep, _seen in knocked
                     if ep not in COVERAGE_KNOWN_GAPS]
            mended = sorted(COVERAGE_KNOWN_GAPS - knocked_names)
            if knocked:
                print(f"\n  REACHED BUT NEVER ANSWERED — {len(knocked)} "
                      f"page(s), {len(fresh)} of them new:")
                for ep, seen in knocked:
                    what = ", ".join(f"{mth} {code}"
                                     + (f" -> {loc}" if loc else "")
                                     for mth, code, loc in seen[:3])
                    mark = "NEW  " if ep not in COVERAGE_KNOWN_GAPS else "     "
                    print(f"    {mark}{ep:<34} {what}")
                print("  A login redirect, a 403, or an id that does not "
                      "exist is not a test of the page.")
            if fresh:
                coverage_ok = False
                print("  ^ the ones marked NEW are not on the known list. "
                      "Write the test, or add the name and say why.")
            if mended:
                coverage_ok = False
                print("\n  ON THE KNOWN LIST AND ANSWERING NOW — take "
                      "these off it:")
                for ep in mended:
                    print(f"    {ep}")
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
    if not coverage_ok:
        print("\nThe list of pages reached but never answered has moved — "
              "see above. Either a page stopped being tested properly, or one "
              "started and the list still says otherwise.")
    if not registry_ok:
        print("\nA suite file was written and never registered — the total"
              " above does not cover it.")
    return 0 if (not all_failed and not crashed and control_ok
                 and registry_ok and coverage_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
