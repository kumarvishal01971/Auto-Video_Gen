import unittest
from unittest.mock import patch

import httpx

from pipeline.providers import llm


class LLMRetryTests(unittest.TestCase):
    def test_falls_back_to_groq_after_primary_service_unavailable(self):
        unavailable = httpx.Response(
            503,
            request=httpx.Request("POST", "https://gemini.example/chat/completions"),
        )
        success = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=httpx.Request("POST", "https://groq.example/chat/completions"),
        )
        with (
            patch.object(llm.config, "LLM_PROVIDER", "openai_compat"),
            patch.object(llm.config, "LLM_API_KEY", "gemini-key"),
            patch.object(llm.config, "LLM_BASE_URL", "https://gemini.example"),
            patch.object(llm.config, "GROQ_API_KEY", "groq-key"),
            patch.object(llm.config, "GROQ_BASE_URL", "https://groq.example"),
            patch.object(llm.config, "GROQ_MODEL", "groq-model"),
            patch.object(llm.httpx, "post", side_effect=[unavailable, unavailable, unavailable, success]) as request,
            patch.object(llm.time, "sleep"),
        ):
            result = llm.chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[-1].args[0], "https://groq.example/chat/completions")

    def test_retries_service_unavailable_after_retry_after(self):
        unavailable = httpx.Response(
            503,
            headers={"Retry-After": "12"},
            request=httpx.Request("POST", "https://example.test/chat/completions"),
        )
        success = httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            request=httpx.Request("POST", "https://example.test/chat/completions"),
        )
        with (
            patch.object(llm.config, "LLM_PROVIDER", "openai_compat"),
            patch.object(llm.config, "LLM_API_KEY", "test-key"),
            patch.object(llm.httpx, "post", side_effect=[unavailable, success]) as request,
            patch.object(llm.time, "sleep") as sleep,
        ):
            result = llm.chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(12)

    def test_surfaces_error_after_retry_limit(self):
        unavailable = httpx.Response(
            503,
            request=httpx.Request("POST", "https://example.test/chat/completions"),
        )
        with (
            patch.object(llm.config, "LLM_PROVIDER", "openai_compat"),
            patch.object(llm.config, "LLM_API_KEY", "test-key"),
            patch.object(llm.config, "GROQ_API_KEY", ""),
            patch.object(llm.httpx, "post", return_value=unavailable) as request,
            patch.object(llm.time, "sleep"),
        ):
            with self.assertRaises(llm.ProviderError):
                llm.chat_json("system", "user")

        self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
