from __future__ import annotations

import unittest

import httpx

from mininet_ai.transports import HttpTransportError, HttpxJsonTransport


class HttpxJsonTransportTests(unittest.TestCase):
    @staticmethod
    def post(transport: httpx.BaseTransport, *, limit: int = 1024):
        return HttpxJsonTransport(transport).post_json(
            "https://provider.example.test/invoke",
            {"request": "test"},
            headers={},
            timeout_seconds=1,
            max_response_bytes=limit,
        )

    def test_returns_json_without_following_redirects(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            return httpx.Response(200, json={"result": "ok"})

        result = self.post(httpx.MockTransport(handler))

        self.assertEqual(result, {"result": "ok"})

    def test_status_size_and_json_failures_are_typed(self) -> None:
        cases = (
            (
                httpx.Response(503, text="unavailable"),
                1024,
                "http-status",
            ),
            (
                httpx.Response(200, content=b"x" * 100),
                10,
                "response-too-large",
            ),
            (
                httpx.Response(200, content=b"not-json"),
                1024,
                "invalid-json",
            ),
        )

        for response, limit, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                transport = httpx.MockTransport(lambda request: response)
                with self.assertRaises(HttpTransportError) as context:
                    self.post(transport, limit=limit)
                self.assertEqual(context.exception.code, expected_code)

    def test_http_timeout_uses_the_shared_timeout_error(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("deadline", request=request)

        with self.assertRaises(TimeoutError):
            self.post(httpx.MockTransport(timeout))


if __name__ == "__main__":
    unittest.main()
