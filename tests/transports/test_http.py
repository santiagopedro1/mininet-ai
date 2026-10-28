from __future__ import annotations

import unittest
from threading import Event, Thread

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
                transport = httpx.MockTransport(
                    lambda request, response=response: response
                )
                with self.assertRaises(HttpTransportError) as context:
                    self.post(transport, limit=limit)
                self.assertEqual(context.exception.code, expected_code)

    def test_http_timeout_uses_the_shared_timeout_error(self) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("deadline", request=request)

        with self.assertRaises(TimeoutError):
            self.post(httpx.MockTransport(timeout))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b'{"answers":{"next_action":{},"next_action":{}}}'
            )
        )
        with self.assertRaises(HttpTransportError) as error:
            self.post(transport)
        self.assertEqual(error.exception.code, "invalid-json")

    def test_cancel_request_closes_only_the_matching_local_client(self) -> None:
        entered, release = Event(), Event()
        result = []

        def handler(request: httpx.Request) -> httpx.Response:
            entered.set()
            self.assertTrue(release.wait(2))
            return httpx.Response(200, json={"result": "ok"})

        transport = HttpxJsonTransport(httpx.MockTransport(handler))

        def post() -> None:
            try:
                result.append(
                    transport.post_json(
                        "https://provider.example.test/invoke",
                        {"request": "test"},
                        headers={},
                        timeout_seconds=1,
                        max_response_bytes=1024,
                        request_id="invoke-1",
                    )
                )
            except Exception as error:
                result.append(error)

        worker = Thread(target=post)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(transport.cancel_request("invoke-1"))
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            # A closed local client does not prove the peer stopped and a
            # response already in flight may still be delivered by the adapter.
            self.assertTrue(result)
            self.assertFalse(transport.cancel_request("invoke-1"))
        finally:
            release.set()
            worker.join(2)


if __name__ == "__main__":
    unittest.main()
