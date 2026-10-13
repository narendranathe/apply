"""A connection-budget fixture enforces Notion's documented 180/minute limit."""
import unittest
from unittest.mock import patch

from cloud.notion import AmbiguousWriteError, NotionAPIError, NotionStore


class VirtualClock:
    def __init__(self):
        self.now, self.waits = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class ConnectionBudget:
    def __init__(self, clock):
        self.clock, self.starts, self.rejections = clock, [], 0

    def __call__(self, *args):
        now = self.clock()
        recent = [start for start in self.starts if now - start < 60]
        if len(recent) >= 180:
            self.rejections += 1
            raise NotionAPIError(429, 'rate_limited', 60 - (now - recent[0]))
        self.starts.append(now)
        return {'results': [], 'has_more': False, 'next_cursor': None}


class RequestPacingTests(unittest.TestCase):
    def test_long_read_scan_stays_within_connection_budget(self):
        clock = VirtualClock()
        api = ConnectionBudget(clock)
        with patch('cloud.notion.time.monotonic', new=clock):
            store = NotionStore('fixture-token', transport=api, sleeper=clock.sleep)
            for _ in range(200):
                store._request('GET', '/blocks/fixture-id/children')
        self.assertEqual(len(api.starts), 200)
        self.assertEqual(api.rejections, 0)
        self.assertTrue(all(later - earlier >= 0.4 - 1e-9
                            for earlier, later in zip(api.starts, api.starts[1:])))

    def test_rate_limit_backoff_and_terminal_restrictions_remain_bounded(self):
        cases = [(NotionAPIError(429, 'rate_limited', 1), False, 2),
                 (NotionAPIError(429, 'rate_limited', 120), True, 1),
                 (NotionAPIError(429, 'rate_limited', 1, 'public_api_request_blocked'), True, 1)]
        for error, terminal, expected_calls in cases:
            with self.subTest(terminal=terminal, expected_calls=expected_calls):
                clock, starts = VirtualClock(), []

                def transport(*args):
                    starts.append(clock())
                    if len(starts) == 1:
                        raise error
                    return {'results': [], 'has_more': False}

                with patch('cloud.notion.time.monotonic', new=clock), patch('cloud.notion.random.uniform', return_value=0):
                    store = NotionStore('fixture-token', transport=transport, sleeper=clock.sleep)
                    if terminal:
                        with self.assertRaises(NotionAPIError):
                            store._request('GET', '/blocks/fixture-id/children')
                        self.assertEqual(clock.waits, [])
                    else:
                        store._request('GET', '/blocks/fixture-id/children')
                        self.assertEqual(starts, [0, 1])
                self.assertEqual(len(starts), expected_calls)

    def test_pacing_never_replays_an_uncertain_write(self):
        clock, starts = VirtualClock(), []

        def transport(*args):
            starts.append(clock())
            raise NotionAPIError(503, 'service_unavailable')

        with patch('cloud.notion.time.monotonic', new=clock):
            store = NotionStore('fixture-token', transport=transport, sleeper=clock.sleep)
            with self.assertRaises(AmbiguousWriteError):
                store._request('POST', '/pages', {'fixture': True}, write=True)
        self.assertEqual(starts, [0])
        self.assertEqual(clock.waits, [])


if __name__ == '__main__':
    unittest.main()
