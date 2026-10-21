import io
import json
import unittest
import urllib.error

from worldfork.provider import (
    ENDPOINT, MAX_RESPONSE_BYTES, OpenAIInterpreter, ProviderError,
    ProviderUnavailable, _NoRedirect,
)


SCENARIO = {
    "name": "Peak demand", "demand_multiplier": 1.5, "disruption": 0.4,
    "reserve_units": 70, "objective": "balanced", "seed": 42, "worlds": 128,
}


def completed(data=None):
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{
            "type": "output_text",
            "text": json.dumps(data if data is not None else {
                "scenario": SCENARIO, "interpretation": "Assumes a synthetic peak-demand period.",
            }),
        }]}],
    }


class StubOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request, self.timeout = request, timeout
        if isinstance(self.response, Exception):
            raise self.response
        data = self.response if isinstance(self.response, bytes) else json.dumps(self.response).encode()
        return io.BytesIO(data)


class ProviderTests(unittest.TestCase):
    def make(self, response=None, validator=lambda data: dict(data)):
        opener = StubOpener(completed() if response is None else response)
        provider = OpenAIInterpreter("test-secret-never-display", opener=opener, validator=validator)
        return provider, opener

    def test_structured_output_and_fixed_destination(self):
        provider, opener = self.make()
        result = provider.interpret("Increase demand by half")
        self.assertEqual(result["scenario"], SCENARIO)
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(opener.request.full_url, ENDPOINT)
        self.assertEqual(opener.request.method, "POST")
        payload = json.loads(opener.request.data)
        self.assertFalse(payload["store"])
        self.assertNotIn("tools", payload)
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(opener.timeout, 20)
        self.assertNotIn("test-secret", json.dumps(result))

    def test_absent_or_invalid_key_is_unconfigured(self):
        for key in (None, "", "contains\nnewline", "é"):
            with self.subTest(key=repr(key)):
                provider = OpenAIInterpreter(key)
                self.assertFalse(provider.configured)
                with self.assertRaises(ProviderUnavailable):
                    provider.interpret("A scenario")

    def test_prompt_bounds_prevent_network_call(self):
        provider, opener = self.make()
        for prompt in (None, 12, "", "  ", "x" * 2001):
            with self.assertRaises(ValueError):
                provider.interpret(prompt)
        self.assertIsNone(opener.request)

    def test_lone_surrogates_in_prompt_are_rejected_before_network(self):
        provider, opener = self.make()
        for prompt in ("\ud800", "\udfff", "Peak \ud800 demand"):
            with self.assertRaisesRegex(ValueError, "valid Unicode"):
                provider.interpret(prompt)
        self.assertIsNone(opener.request)

    def test_lone_surrogates_in_interpretation_are_invalid_model_data(self):
        for explanation in ("\ud800", "\udfff", "Assumes \ud800 demand"):
            provider, _ = self.make(completed({"scenario": SCENARIO, "interpretation": explanation}))
            with self.assertRaisesRegex(ProviderError, "invalid scenario data"):
                provider.interpret("A scenario")

    def test_valid_unicode_prompt_and_interpretation_are_preserved(self):
        explanation = "Synthetic demand experiment 🧪"
        provider, opener = self.make(completed({"scenario": SCENARIO, "interpretation": explanation}))
        result = provider.interpret("Peak demand 🧪")
        self.assertEqual(result["interpretation"], explanation)
        self.assertEqual(json.loads(opener.request.data)["input"], "Peak demand 🧪")

    def test_refusal_and_incomplete_are_not_scenarios(self):
        for response in (
            {"status": "incomplete", "output": []},
            {"status": "completed", "output": [{"content": [{"type": "refusal", "refusal": "private text"}]}]},
        ):
            provider, _ = self.make(response)
            with self.assertRaises(ProviderError) as caught:
                provider.interpret("A scenario")
            self.assertNotIn("private text", str(caught.exception))

    def test_malformed_and_oversized_responses_are_sanitized(self):
        for response in (
            b"private upstream non-JSON detail",
            b"x" * (MAX_RESPONSE_BYTES + 1),
            {"status": "completed", "output": "bad"},
            completed({"scenario": SCENARIO, "interpretation": "OK", "execute": "danger"}),
            completed({"scenario": {}, "interpretation": "OK"}),
            completed({"scenario": SCENARIO, "interpretation": "x" * 501}),
        ):
            with self.subTest(response=str(response)[:60]):
                provider, _ = self.make(response)
                with self.assertRaises(ProviderError) as caught:
                    provider.interpret("A scenario")
                self.assertNotIn("private upstream", str(caught.exception))

    def test_model_cannot_bypass_engine_validation(self):
        def reject(data):
            raise ValueError("Internal detail should not leak")
        provider, _ = self.make(validator=reject)
        with self.assertRaises(ProviderError) as caught:
            provider.interpret("A scenario")
        self.assertNotIn("Internal detail", str(caught.exception))

    def test_nonfinite_and_duplicate_json_are_rejected(self):
        response = completed()
        for text in (
            '{"scenario":{},"scenario":{},"interpretation":"OK"}',
            '{"scenario":{"value":NaN},"interpretation":"OK"}',
        ):
            response["output"][0]["content"][0]["text"] = text
            provider, _ = self.make(response)
            with self.assertRaises(ProviderError):
                provider.interpret("A scenario")

    def test_transport_errors_do_not_expose_credentials_or_body(self):
        errors = [
            urllib.error.URLError("test-secret-never-display"),
            urllib.error.HTTPError(ENDPOINT, 401, "private message", {}, io.BytesIO(b"secret body")),
            urllib.error.HTTPError(ENDPOINT, 429, "private message", {}, io.BytesIO(b"secret body")),
            urllib.error.HTTPError(ENDPOINT, 500, "private message", {}, io.BytesIO(b"secret body")),
        ]
        for error in errors:
            provider, _ = self.make(error)
            with self.assertRaises(ProviderError) as caught:
                provider.interpret("A scenario")
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertNotIn("secret body", str(caught.exception))

    def test_redirect_is_refused(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_normalized_output_text_fallback(self):
        response = {"status": "completed", "output_text": completed()["output"][0]["content"][0]["text"]}
        provider, _ = self.make(response)
        self.assertEqual(provider.interpret("A scenario")["scenario"], SCENARIO)


if __name__ == "__main__":
    unittest.main()
