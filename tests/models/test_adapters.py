from __future__ import annotations

import unittest

from mininet_ai.models import (
    DeterministicModelProvider,
    OllamaModelProvider,
    OpenAICompatibleModelProvider,
)
from mininet_ai.sdk import (
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelRole,
)
from mininet_ai.transports import HttpTransportError


class RecordingTransport:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    def post_json(self, url, payload, **options):
        self.calls.append((url, payload, options))
        if self.error is not None:
            raise self.error
        return self.response


def request(**updates) -> ModelRequest:
    values = {
        "provider": "test",
        "model": "small-model",
        "messages": (
            ModelMessage(role=ModelRole.SYSTEM, content="Return a decision."),
            ModelMessage(role=ModelRole.USER, content="Inspect s1", name="operator"),
        ),
        "parameters": {"temperature": 0},
        "timeoutSeconds": 4,
    }
    values.update(updates)
    return ModelRequest.model_validate(values)


class ModelAdapterTests(unittest.TestCase):
    def test_deterministic_provider_returns_configured_structured_output(
        self,
    ) -> None:
        provider = DeterministicModelProvider()

        response = provider.generate(
            request(parameters={"response": {"message": "done"}})
        )

        self.assertEqual(response.structured_output, {"message": "done"})
        with self.assertRaises(ModelProviderError) as missing:
            provider.generate(request(parameters={}))
        self.assertEqual(
            missing.exception.code,
            "model.deterministic.response-missing",
        )

    def test_adapters_satisfy_the_model_provider_protocol(self) -> None:
        self.assertIsInstance(OpenAICompatibleModelProvider(), ModelProvider)
        self.assertIsInstance(OllamaModelProvider(), ModelProvider)

    def test_openai_adapter_normalizes_payload_response_and_usage(self) -> None:
        transport = RecordingTransport(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "healthy"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            }
        )
        provider = OpenAICompatibleModelProvider(
            "https://models.example.test/v1/chat/completions",
            api_key="secret",
            transport=transport,
            max_response_bytes=2048,
        )

        response = provider.generate(request())

        self.assertEqual(response.content, "healthy")
        self.assertEqual(response.provider_request_id, "chatcmpl-1")
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage.total_tokens, 10)
        url, payload, options = transport.calls[0]
        self.assertEqual(url, "https://models.example.test/v1/chat/completions")
        self.assertEqual(payload["model"], "small-model")
        self.assertEqual(payload["temperature"], 0)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][1]["name"], "operator")
        self.assertEqual(options["headers"]["authorization"], "Bearer secret")
        self.assertEqual(options["timeout_seconds"], 4)
        self.assertEqual(options["max_response_bytes"], 2048)

    def test_openai_adapter_requests_and_validates_structured_output(self) -> None:
        transport = RecordingTransport(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"state":"healthy"}',
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        provider = OpenAICompatibleModelProvider(transport=transport)
        schema = {
            "type": "object",
            "properties": {"state": {"type": "string"}},
            "required": ["state"],
            "additionalProperties": False,
        }

        response = provider.generate(request(responseSchema=schema))

        self.assertEqual(response.structured_output, {"state": "healthy"})
        response_format = transport.calls[0][1]["response_format"]
        self.assertEqual(response_format["json_schema"]["schema"], schema)

    def test_ollama_adapter_normalizes_payload_response_and_usage(self) -> None:
        transport = RecordingTransport(
            {
                "message": {"role": "assistant", "content": "ready"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 6,
                "eval_count": 1,
            }
        )
        provider = OllamaModelProvider(transport=transport)

        response = provider.generate(request())

        self.assertEqual(response.content, "ready")
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage.input_tokens, 6)
        self.assertEqual(response.usage.total_tokens, 7)
        url, payload, options = transport.calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["options"], {"temperature": 0})
        self.assertFalse(payload["stream"])
        self.assertEqual(options["timeout_seconds"], 4)

    def test_ollama_structured_output_uses_the_schema_as_format(self) -> None:
        transport = RecordingTransport(
            {
                "message": {"role": "assistant", "content": '{"safe":true}'},
                "done": True,
            }
        )
        provider = OllamaModelProvider(transport=transport)
        schema = {
            "type": "object",
            "properties": {"safe": {"type": "boolean"}},
            "required": ["safe"],
        }

        response = provider.generate(request(responseSchema=schema))

        self.assertEqual(response.structured_output, {"safe": True})
        self.assertEqual(transport.calls[0][1]["format"], schema)

    def test_transport_and_malformed_responses_have_typed_errors(self) -> None:
        cases = (
            (
                OpenAICompatibleModelProvider(
                    transport=RecordingTransport(
                        error=HttpTransportError("offline", code="request-failed")
                    )
                ),
                "model.openai.request-failed",
            ),
            (
                OpenAICompatibleModelProvider(
                    transport=RecordingTransport({"choices": []})
                ),
                "model.response.invalid",
            ),
            (
                OllamaModelProvider(
                    transport=RecordingTransport({"message": {"content": 2}})
                ),
                "model.response.invalid",
            ),
        )

        for provider, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ModelProviderError) as context:
                    provider.generate(request())
                self.assertEqual(context.exception.code, expected_code)

    def test_openai_rejects_inconsistent_token_usage(self) -> None:
        provider = OpenAICompatibleModelProvider(
            transport=RecordingTransport(
                {
                    "choices": [{"message": {"content": "ready"}}],
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                }
            )
        )

        with self.assertRaises(ModelProviderError) as context:
            provider.generate(request())

        self.assertEqual(context.exception.code, "model.response.invalid")

    def test_invalid_json_and_schema_mismatches_are_distinct(self) -> None:
        schema = {"type": "object", "required": ["safe"]}
        cases = (
            ("not-json", "model.response.invalid-json"),
            ('{"unexpected":true}', "model.response.schema-mismatch"),
        )

        for content, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = OllamaModelProvider(
                    transport=RecordingTransport(
                        {"message": {"content": content}, "done": True}
                    )
                )
                with self.assertRaises(ModelProviderError) as context:
                    provider.generate(request(responseSchema=schema))
                self.assertEqual(context.exception.code, expected_code)

    def test_endpoints_reject_credentials_and_fragments(self) -> None:
        invalid = (
            "file:///tmp/model",
            "https://user:secret@example.test/chat",
            "https://example.test/chat#token",
        )

        for endpoint in invalid:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    OllamaModelProvider(endpoint)


if __name__ == "__main__":
    unittest.main()
