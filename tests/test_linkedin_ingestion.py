"""Synthetic official-export shapes only. No member content or credentials."""
import copy
import csv
import hashlib
import io
import unittest
from datetime import datetime, timezone

from cloud.linkedin_ingestion import (
    AccountIdentity, ExportSource, LiveUnavailable, exact_event_alias,
    import_member_export, profile_url,
)

ACCOUNT = "https://www.linkedin.com/in/synthetic-owner"
OTHER = "https://www.linkedin.com/in/synthetic-contact"
ACCOUNT_ID = AccountIdentity(ACCOUNT, True, "synthetic:verified-account")
SOURCE = ExportSource("synthetic:archive", "2026-10-12T18:00:00+00:00", "partial", ("messages",))
MESSAGE_HEADER = ["CONVERSATION ID", "CONVERSATION TITLE", "FROM", "SENDER PROFILE URL", "TO",
                  "RECIPIENT PROFILE URLS", "DATE", "SUBJECT", "CONTENT", "FOLDER"]
INVITATION_HEADER = ["From", "To", "Sent At", "Message", "Direction", "inviterProfileUrl", "inviteeProfileUrl"]
CONNECTION_HEADER = ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]


def csv_text(header, rows, *, delimiter=",", preamble=""):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow(header)
    writer.writerows(rows)
    return preamble + output.getvalue()


def message(text="Synthetic hello", *, sender=ACCOUNT, recipient=OTHER, when="2026-10-11 12:30:00 UTC", thread="thread-a"):
    return [thread, "Synthetic conversation", "Owner" if sender == ACCOUNT else "Contact", sender,
            "Contact" if recipient == OTHER else "Owner", recipient, when, "Synthetic subject", text, "INBOX"]


def events(result):
    return [row for row in result["records"] if row["kind"] == "conversation_events"]


class LinkedInIngestionTests(unittest.TestCase):
    def ingest(self, files=None, **kwargs):
        return import_member_export(files or {"Messages.csv": csv_text(MESSAGE_HEADER, [message()])},
                                    account=kwargs.pop("account", ACCOUNT_ID), source=kwargs.pop("source", SOURCE), **kwargs)

    def test_default_live_connector_is_unavailable_and_has_no_outbound_method(self):
        provider = LiveUnavailable()
        self.assertFalse(provider.capabilities()["live_inbox"])
        self.assertFalse(provider.capabilities()["outbound"])
        self.assertFalse(provider.fetch_evidence()["inbox_checked"])
        self.assertFalse(hasattr(provider, "send_message"))
        self.assertFalse(hasattr(provider, "send_invitation"))

    def test_verified_account_and_evidence_are_required(self):
        for account in (AccountIdentity(ACCOUNT, False, "proof"), AccountIdentity(ACCOUNT, True, "")):
            with self.assertRaises(ValueError):
                self.ingest(account=account)

    def test_messages_preserve_exact_text_sent_received_and_timestamps(self):
        text = 'Synthetic quote: "hello", comma\nsecond line  '
        rows = [message(text), message("Synthetic answer", sender=OTHER, recipient=ACCOUNT, when="2026-10-11 12:31:00 UTC")]
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, rows).encode("utf-8-sig")})
        sent, received = [row["payload"] for row in events(result)]
        self.assertEqual(sent["text"], text)
        self.assertEqual(sent["occurred_at"], "2026-10-11 12:30:00 UTC")
        self.assertEqual(sent["timestamp"]["normalized_utc"], "2026-10-11T12:30:00+00:00")
        self.assertEqual((sent["direction"], received["direction"]), ("sent", "received"))
        self.assertTrue(received["direction_verified"])
        self.assertEqual(sent["thread_id"], received["thread_id"])
        thread = next(row["payload"] for row in result["records"] if row["kind"] == "conversation_observations")
        self.assertEqual(thread["last_reply_at"], received["occurred_at"])
        self.assertFalse(thread["history_complete"])

    def test_connections_preamble_bom_semicolon_and_day_precision(self):
        text = csv_text(CONNECTION_HEADER, [["Same", "Name", OTHER + "/?trk=export", "", "Synthetic Co", "Engineer", "11 Oct 2026"]],
                        delimiter=";", preamble='Notes:\n"Email addresses are subject to privacy settings."\n\n')
        result = self.ingest({"Connections.csv": text.encode("utf-8-sig")})
        payload = events(result)[0]["payload"]
        self.assertEqual(payload["timestamp"]["precision"], "date")
        self.assertIsNone(payload["timestamp"]["timezone"])
        self.assertIsNone(payload["timestamp"]["normalized_utc"])
        self.assertEqual(payload["historical_state"], "connected_at_export")
        self.assertFalse(payload["live_state_verified"])
        self.assertEqual(result["source"]["files"][0]["header_line"], 4)

    def test_repeated_import_is_idempotent_and_reexport_ignores_file_row_order(self):
        initial = self.ingest()
        again = self.ingest(existing_records=initial["records"])
        self.assertEqual(again["records"], [])
        self.assertEqual(len(again["duplicates"]), 1)
        self.assertEqual(again["conflicts"], [])
        shifted = csv_text(MESSAGE_HEADER, [message("Different synthetic event", when="2026-10-11 12:32:00 UTC"), message()])
        third = self.ingest({"Messages.csv": shifted}, existing_records=initial["records"])
        self.assertEqual(len(events(third)), 1)
        self.assertEqual(len(third["duplicates"]), 1)

    def test_missing_timezone_is_never_assigned_utc(self):
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(when="2026-10-11 12:30:00")])})
        payload = events(result)[0]["payload"]
        self.assertIsNone(payload["timestamp"]["timezone"])
        self.assertIsNone(payload["timestamp"]["normalized_utc"])
        self.assertIn("timestamp_timezone_unknown", payload["issues"])
        self.assertIsNone(exact_event_alias(payload, ACCOUNT))
        thread = next(row["payload"] for row in result["records"] if row["kind"] == "conversation_observations")
        self.assertIsNone(thread["last_reply_at"])

    def test_offset_precision_and_exact_original_timestamp_are_preserved(self):
        value = "2026-10-11T07:30:00.120-05:00"
        payload = events(self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(when=value)])}))[0]["payload"]
        self.assertEqual(payload["timestamp"]["raw"], value)
        self.assertEqual(payload["timestamp"]["precision"], "fractional_seconds:3")
        self.assertEqual(payload["timestamp"]["normalized_utc"], "2026-10-11T12:30:00.120000+00:00")

    def test_unknown_direction_does_not_guess_from_display_names(self):
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(sender=OTHER, recipient="")])})
        payload = events(result)[0]["payload"]
        self.assertEqual(payload["direction"], "unknown")
        self.assertFalse(payload["direction_verified"])
        self.assertIn("direction_unverified", payload["issues"])
        again = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(sender=OTHER, recipient="")])}, existing_records=result["records"])
        self.assertEqual(again["records"], [])
        self.assertEqual(len(again["duplicates"]), 1)

    def test_explicit_direction_is_preserved_and_contradiction_held(self):
        header = MESSAGE_HEADER + ["Direction"]
        payload = events(self.ingest({"Messages.csv": csv_text(header, [message(sender=OTHER, recipient="") + ["INCOMING"]])}))[0]["payload"]
        self.assertEqual(payload["direction"], "received")
        self.assertTrue(payload["direction_verified"])
        contradiction = events(self.ingest({"Messages.csv": csv_text(header, [message() + ["INCOMING"]])}))[0]
        self.assertEqual(contradiction["payload"]["direction"], "unknown")
        self.assertIn("direction_conflict", contradiction["payload"]["issues"])
        self.assertEqual(contradiction["status"], "needs_reconciliation")

    def test_explicit_outgoing_with_another_sender_is_not_account_ownership(self):
        header = MESSAGE_HEADER + ["Direction"]
        row = message(sender=OTHER, recipient="https://www.linkedin.com/in/synthetic-third") + ["OUTGOING"]
        payload = events(self.ingest({"Messages.csv": csv_text(header, [row])}))[0]["payload"]
        self.assertEqual(payload["direction"], "unknown")
        self.assertFalse(payload["direction_verified"])
        self.assertIn("direction_conflict", payload["issues"])

    def test_old_invitation_pending_column_is_not_current_pending(self):
        header = INVITATION_HEADER + ["Status"]
        row = ["Owner", "Contact", "2025-01-10 09:00:00 UTC", "Synthetic invitation", "OUTGOING", ACCOUNT, OTHER, "PENDING"]
        payload = events(self.ingest({"Invitations.csv": csv_text(header, [row])}))[0]["payload"]
        self.assertEqual(payload["source_status"], "PENDING")
        self.assertEqual(payload["historical_state"], "invitation_recorded")
        self.assertFalse(payload["live_state_verified"])
        self.assertNotIn("confirmation_evidence", payload)

    def test_connection_does_not_infer_exact_invitation_acceptance(self):
        invitation = ["Owner", "Contact", "2025-01-10 09:00:00 UTC", "Synthetic invitation", "OUTGOING", ACCOUNT, OTHER]
        result = self.ingest({"Invitations.csv": csv_text(INVITATION_HEADER, [invitation]),
                              "Connections.csv": csv_text(CONNECTION_HEADER, [["Contact", "One", OTHER, "", "Co", "Engineer", "11 Jan 2025"]])})
        self.assertEqual({row["payload"]["historical_state"] for row in events(result)}, {"invitation_recorded", "connected_at_export"})
        self.assertTrue(all(row["payload"]["historical_only"] for row in events(result)))
        self.assertTrue(all(not row["payload"]["live_state_verified"] for row in events(result)))

    def test_display_name_collision_does_not_merge_contacts(self):
        rows = [["Same", "Name", OTHER, "", "Co", "Engineer", "11 Oct 2026"],
                ["Same", "Name", "https://www.linkedin.com/in/synthetic-other", "", "Co", "Engineer", "11 Oct 2026"]]
        result = self.ingest({"Connections.csv": csv_text(CONNECTION_HEADER, rows)})
        ids = [row["payload"]["contact_id"] for row in events(result)]
        self.assertNotEqual(*ids)

    def test_exact_existing_contact_id_is_reused_and_suppression_retained(self):
        contact = {"id": "legacy-contact-1", "kind": "contacts", "payload": {"profile_url": OTHER + "/?trk=legacy", "opt_out": True, "history_complete": False}}
        payload = events(self.ingest(existing_contacts=[contact]))[0]["payload"]
        self.assertEqual(payload["contact_id"], contact["id"])
        self.assertEqual(payload["suppression"][contact["id"]]["status"], "suppressed")
        self.assertTrue(payload["suppression"][contact["id"]]["sources"][0]["flags"]["opt_out"])

    def test_absence_of_suppression_is_unknown_and_not_clearance(self):
        payload = events(self.ingest())[0]["payload"]
        context = payload["suppression"][payload["contact_id"]]
        self.assertEqual(context["status"], "unknown")
        self.assertFalse(context["outbound_clearance"])

    def test_two_canonical_ids_for_same_profile_are_a_conflict(self):
        contacts = [{"id": value, "payload": {"profile_url": OTHER}} for value in ("one", "two")]
        payload = events(self.ingest(existing_contacts=contacts))[0]["payload"]
        self.assertIn("canonical_contact_conflict", payload["issues"])
        self.assertIsNone(payload["contact_id"])

    def test_exact_legacy_event_keeps_its_original_event_id(self):
        legacy = {"id": "original-sqlite-event", "kind": "outreach_events", "payload": {
            "event_id": "original-sqlite-event", "event_type": "message", "direction": "sent",
            "contact_key": OTHER + "/?trk=legacy", "content": "Synthetic hello", "occurred_at": "2026-10-11 12:30:00 UTC"}}
        result = self.ingest(existing_records=[legacy])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["duplicates"][0]["event_id"], legacy["id"])
        self.assertTrue(all(alias["event_id"] == legacy["id"] for alias in result["aliases"]))

    def test_possible_legacy_match_is_held_without_new_delivery_record(self):
        legacy = {"id": "legacy-date-only", "kind": "outreach_events", "payload": {
            "contact_key": OTHER, "content": "Historical journal entry\nSynthetic hello\nUncertain delivery", "occurred_at": "2026-10-11"}}
        result = self.ingest(existing_records=[legacy])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["conflicts"][0]["reason"], "possible_existing_event_requires_reconciliation")
        self.assertEqual(result["conflicts"][0]["existing_ids"], [legacy["id"]])

    def test_legacy_body_hash_with_missing_direction_or_time_is_held(self):
        legacy = {"id": "legacy-incomplete", "kind": "outreach_events", "payload": {
            "contact_key": OTHER, "content_hash": hashlib.sha256(b"Synthetic hello").hexdigest()}}
        result = self.ingest(existing_records=[legacy])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["conflicts"][0]["reason"], "possible_existing_event_requires_reconciliation")

    def test_unzoned_legacy_timestamp_is_possible_match_not_exact_alias(self):
        legacy = {"id": "legacy-unzoned", "kind": "outreach_events", "payload": {
            "event_type": "message", "contact_key": OTHER, "direction": "sent",
            "text": "Synthetic hello", "occurred_at": "2026-10-11 12:30:00"}}
        self.assertIsNone(exact_event_alias(legacy["payload"], ACCOUNT))
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(when="2026-10-11 12:30:00")])}, existing_records=[legacy])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["conflicts"][0]["reason"], "possible_existing_event_requires_reconciliation")

    def test_same_semantic_alias_with_different_native_threads_is_held(self):
        first = self.ingest()
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(thread="different-native-thread")])},
                             existing_records=first["records"])
        self.assertEqual(result["records"], [])
        self.assertEqual(result["conflicts"][0]["reason"], "exact_alias_conflicting_native_threads")

    def test_alias_collision_between_notion_and_journal_requires_reconciliation(self):
        payload = events(self.ingest())[0]["payload"]
        previous = [{"id": value, "kind": "outreach_events", "payload": copy.deepcopy(payload)} for value in ("journal", "notion")]
        result = self.ingest(existing_records=previous)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["conflicts"][0]["reason"], "exact_alias_multiple_existing_events")

    def test_same_text_at_different_verified_times_is_a_distinct_event(self):
        initial = self.ingest()
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(when="2026-10-12 12:30:00 UTC")])}, existing_records=initial["records"])
        self.assertEqual(len(events(result)), 1)
        self.assertEqual(result["conflicts"], [])

    def test_owner_identity_alone_does_not_match_two_different_recipients(self):
        first = self.ingest()
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(recipient="https://www.linkedin.com/in/synthetic-third", thread="thread-b")])},
                             existing_records=first["records"])
        self.assertEqual(len(events(result)), 1)
        self.assertEqual(result["conflicts"], [])

    def test_partial_import_records_valid_file_and_reports_bad_file(self):
        result = self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message()]),
                              "Invitations.csv": "From,To,Sent At,Message\nOwner,Contact,2026-10-11", "Other.csv": "ignored"})
        self.assertEqual(len(events(result)), 1)
        self.assertEqual(len(result["conflicts"]), 2)
        self.assertEqual(result["source"]["effective_completeness"], "partial")
        self.assertIn("invitations", result["source"]["missing_categories"])
        self.assertFalse(result["source"]["inbox_checked"])

    def test_complete_export_never_becomes_current_live_history(self):
        files = {"Messages.csv": csv_text(MESSAGE_HEADER, [message()]),
                 "Invitations.csv": csv_text(INVITATION_HEADER, []),
                 "Connections.csv": csv_text(CONNECTION_HEADER, [])}
        result = self.ingest(files, source=ExportSource("complete-archive", "2026-10-12T18:00:00+00:00",
                            "complete_for_supplied_categories", ("messages", "invitations", "connections")))
        self.assertEqual(result["source"]["effective_completeness"], "complete_for_supplied_categories")
        self.assertFalse(result["source"]["inbox_checked"])
        self.assertTrue(all(row["payload"]["historical_only"] for row in result["records"]))
        self.assertTrue(all(not row["payload"]["live_state_verified"] for row in result["records"]))
        self.assertTrue(all(not row["payload"]["history_complete"] for row in result["records"]))
        from cloud.agent_index import build_index
        indexed = build_index(result["records"], datetime(2026, 10, 13, 12, tzinfo=timezone.utc), source_complete=True)
        projections = [row["payload"] for row in indexed if row["kind"] == "entity_projections"]
        self.assertTrue(projections)
        self.assertTrue(all(row["state"] not in {"connected", "pending", "reply_received", "message_sent"} for row in projections))
        self.assertTrue(all(row["last_verified_action"] is None for row in projections))

    def test_inherited_suppression_is_visible_to_shared_index(self):
        from cloud.agent_index import build_index
        contact = {"id": "legacy-contact-1", "payload": {"profile_url": OTHER, "opt_out": True}}
        result = self.ingest(existing_contacts=[contact])
        observations = [row for row in result["records"] if row["kind"] == "contact_observations"]
        self.assertTrue(observations[0]["payload"]["suppressed"])
        indexed = build_index(result["records"], datetime(2026, 10, 13, 12, tzinfo=timezone.utc), source_complete=True)
        projection = next(row["payload"] for row in indexed if row["kind"] == "entity_projections" and row["payload"]["entity_id"] == contact["id"])
        self.assertEqual(projection["state"], "suppressed")

    def test_archived_folder_is_preserved_as_historical_evidence(self):
        row = message()
        row[-1] = "ARCHIVED"
        payload = events(self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [row])}))[0]["payload"]
        self.assertEqual(payload["source_folder"], "ARCHIVED")
        self.assertTrue(payload["historical_only"])
        self.assertFalse(payload["live_state_verified"])

    def test_numeric_dates_need_explicit_locale_evidence(self):
        files = {"Connections.csv": csv_text(CONNECTION_HEADER, [["Contact", "One", OTHER, "", "Co", "Engineer", "10/11/2026"]])}
        unknown = events(self.ingest(files))[0]["payload"]["timestamp"]
        self.assertIsNone(unknown["value"])
        explicit = events(self.ingest(files, source=ExportSource("archive", date_order="DMY", date_order_evidence_ref="verified:locale")))[0]["payload"]["timestamp"]
        self.assertEqual(explicit["value"], "2026-11-10")
        with self.assertRaises(ValueError):
            self.ingest(files, source=ExportSource("archive", date_order="MDY"))

    def test_unknown_timestamp_and_two_digit_year_are_preserved_without_century_guess(self):
        value = "10/11/26, 12:30 PM"
        payload = events(self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(when=value)])},
                                     source=ExportSource("archive", date_order="MDY", date_order_evidence_ref="verified:locale")))[0]["payload"]
        self.assertEqual(payload["timestamp"]["raw"], value)
        self.assertIsNone(payload["timestamp"]["value"])

    def test_name_only_recipient_remains_ambiguous_despite_existing_matching_name(self):
        contacts = [{"id": "one", "payload": {"name": "Contact", "profile_url": OTHER}},
                    {"id": "two", "payload": {"name": "Contact", "profile_url": "https://www.linkedin.com/in/synthetic-other"}}]
        payload = events(self.ingest({"Messages.csv": csv_text(MESSAGE_HEADER, [message(recipient="")])}, existing_contacts=contacts))[0]["payload"]
        self.assertIsNone(payload["participants"][1]["contact_id"])
        self.assertIsNone(payload["contact_id"])
        self.assertIn("participant_identity_unverified", payload["issues"])

    def test_profile_canonicalization_does_not_accept_non_profile_urls(self):
        self.assertEqual(profile_url("http://linkedin.com/in/synthetic-contact/?trk=x"), OTHER)
        for value in ("https://www.linkedin.com/jobs/view/1", "https://linkedin.com.evil.test/in/a", "https://user:pass@linkedin.com/in/a"):
            with self.assertRaises(ValueError):
                profile_url(value)

    def test_exact_alias_needs_explicit_direction_and_identity(self):
        payload = events(self.ingest())[0]["payload"]
        self.assertIsNotNone(exact_event_alias(payload, ACCOUNT))
        payload["direction"] = "unknown"
        self.assertIsNone(exact_event_alias(payload, ACCOUNT))


if __name__ == "__main__":
    unittest.main()
