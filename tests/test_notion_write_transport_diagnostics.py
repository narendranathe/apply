"""Uncertain write diagnostics retain only a classified operation; no effects."""
import contextlib
import io
import json
import runpy
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from cloud.notion import AmbiguousWriteError, NotionAPIError, NotionError, NotionStore


PRIVATE = "synthetic-private-token-contact-content"
EXPECTED = {"http_status": None, "error_code": "unknown", "operation": "create_page"}


class NotionWriteTransportDiagnosticsTests(unittest.TestCase):
    def assert_private_data_absent(self, error):
        encoded = json.dumps(error.diagnostics())
        self.assertNotIn(PRIVATE, encoded)
        self.assertNotIn(PRIVATE, str(error))
        self.assertEqual(set(error.diagnostics()), {"http_status", "error_code", "operation"})

    def test_every_uncertain_write_transport_exception_keeps_create_operation_once(self):
        for failure in (URLError(PRIVATE), TimeoutError(PRIVATE), OSError(PRIVATE),
                        ValueError(PRIVATE), UnicodeError(PRIVATE)):
            with self.subTest(error_type=type(failure).__name__):
                transport, sleep = Mock(side_effect=failure), Mock()
                store = NotionStore(PRIVATE, transport=transport, sleeper=sleep, request_interval=0)
                with self.assertRaises(AmbiguousWriteError) as caught:
                    store._request("POST", "/pages", {"private": PRIVATE}, write=True)
                self.assertEqual(caught.exception.diagnostics(), EXPECTED)
                self.assert_private_data_absent(caught.exception)
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()

    def test_non_object_write_response_keeps_create_operation_without_replay(self):
        for response in (None, [], PRIVATE, 0):
            with self.subTest(response_type=type(response).__name__):
                transport = Mock(return_value=response)
                store = NotionStore(PRIVATE, transport=transport, request_interval=0)
                with self.assertRaises(AmbiguousWriteError) as caught:
                    store._request("POST", "/pages", {"private": PRIVATE}, write=True)
                self.assertEqual(caught.exception.diagnostics(), EXPECTED)
                self.assert_private_data_absent(caught.exception)
                self.assertEqual(transport.call_count, 1)

    def test_real_transport_json_and_unicode_decode_failures_do_not_expose_body(self):
        for raw in (PRIVATE.encode(), b"\xff" + PRIVATE.encode()):
            with self.subTest(unicode_failure=raw.startswith(b"\xff")):
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.read.return_value = raw
                with patch("cloud.notion.build_opener") as opener:
                    opener.return_value.open.return_value = response
                    store = NotionStore(PRIVATE, request_interval=0)
                    with self.assertRaises(AmbiguousWriteError) as caught:
                        store._request("POST", "/pages", {"private": PRIVATE}, write=True)
                    self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(caught.exception.diagnostics(), EXPECTED)
                self.assert_private_data_absent(caught.exception)

    def test_unknown_route_keeps_safe_fallback_without_exposing_path(self):
        store = NotionStore(PRIVATE, transport=Mock(side_effect=TimeoutError(PRIVATE)), request_interval=0)
        with self.assertRaises(AmbiguousWriteError) as caught:
            store._request("POST", "/" + PRIVATE + "?token=" + PRIVATE, write=True)
        self.assertEqual(caught.exception.diagnostics(), {**EXPECTED, "operation": "notion_request"})
        self.assert_private_data_absent(caught.exception)

    def test_read_retry_behavior_is_unchanged(self):
        transport, sleep = Mock(side_effect=TimeoutError(PRIVATE)), Mock()
        store = NotionStore(PRIVATE, transport=transport, sleeper=sleep,
                            max_attempts=3, request_interval=0)
        with self.assertRaises(NotionError) as caught:
            store._request("GET", "/data_sources/private-id")
        self.assertNotIsInstance(caught.exception, AmbiguousWriteError)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_known_http_write_error_still_retains_verified_status_and_code(self):
        transport = Mock(side_effect=NotionAPIError(503, "service_unavailable"))
        store = NotionStore(PRIVATE, transport=transport, request_interval=0)
        with self.assertRaises(AmbiguousWriteError) as caught:
            store._request("POST", "/pages", write=True)
        self.assertEqual(caught.exception.diagnostics(), {
            "http_status": 503, "error_code": "service_unavailable", "operation": "create_page"})
        self.assertEqual(transport.call_count, 1)
        self.assert_private_data_absent(caught.exception)

    def test_runner_output_contains_only_safe_uncertain_write_fields(self):
        output, errors = io.StringIO(), io.StringIO()

        def write_once(store, *_args, **_kwargs):
            return store._request("POST", "/pages", {"private": PRIVATE}, write=True)

        with patch.dict("os.environ", {"NOTION_TOKEN": PRIVATE, "CLOUD_ENABLED": "false"}), \
                patch("sys.argv", ["cloud.orchestrator", "--mode", "dry-run"]), \
                patch("cloud.baseline.restore_baseline", return_value=({"records": []}, "a" * 64)), \
                patch.object(NotionStore, "_transport", side_effect=URLError(PRIVATE)) as transport, \
                patch.object(NotionStore, "query", new=write_once), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            # Route the real entry point through one simulated write failure.
            with self.assertRaises(SystemExit):
                runpy.run_module("cloud.orchestrator", run_name="__main__")
        result = json.loads(output.getvalue())
        self.assertEqual(result["error_type"], "AmbiguousWriteError")
        self.assertEqual(result["notion"], EXPECTED)
        self.assertEqual(transport.call_count, 1)
        self.assertNotIn(PRIVATE, output.getvalue() + errors.getvalue())
        self.assertNotIn("Authorization", output.getvalue() + errors.getvalue())


if __name__ == "__main__":
    unittest.main()
