from __future__ import annotations

import unittest
from email.message import Message
from urllib.error import HTTPError

from tests.skill_imports import add_skill_scripts_to_path

add_skill_scripts_to_path()

from a_share_research.identity_sources import (  # noqa: E402
    TransportError,
    UrlLibTransport,
)


class RateLimitedOpener:
    def open(self, request: object, timeout: int) -> object:
        raise HTTPError(
            "https://source.example.test/private-path",
            429,
            "Too Many Requests: sensitive upstream detail",
            Message(),
            None,
        )


class UrlLibTransportTests(unittest.TestCase):
    def test_http_429_maps_to_a_sanitized_rate_limited_error(self) -> None:
        transport = UrlLibTransport()
        transport._opener = RateLimitedOpener()  # type: ignore[assignment]

        with self.assertRaises(TransportError) as caught:
            transport.get("https://source.example.test/query", {})

        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(
            str(caught.exception),
            "The source rate limit was reached.",
        )
        self.assertNotIn("private-path", str(caught.exception))
        self.assertNotIn("sensitive upstream detail", str(caught.exception))
        caught.exception.__cause__.close()


if __name__ == "__main__":
    unittest.main()
