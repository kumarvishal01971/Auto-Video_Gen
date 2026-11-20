import unittest
from unittest.mock import Mock, patch

import config
from pipeline.providers import image, llm


class LLMFallbackTests(unittest.TestCase):
    def test_uses_groq_when_primary_provider_fails(self):
        with (
            patch.object(config, "LLM_PROVIDER", "openai_compat"),
            patch.object(config, "LLM_BASE_URL", "https://gemini.example"),
            patch.object(config, "LLM_MODEL", "gemini-model"),
            patch.object(config, "LLM_API_KEY", "gemini-key"),
            patch.object(config, "GROQ_BASE_URL", "https://groq.example"),
            patch.object(config, "GROQ_MODEL", "groq-model"),
            patch.object(config, "GROQ_API_KEY", "groq-key"),
            patch.object(
                llm,
                "_chat_json_with_provider",
                side_effect=[llm.ProviderError("primary unavailable"), {"ok": True}],
            ) as request,
        ):
            result = llm.chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[:4], (
            "Groq fallback", "https://groq.example", "groq-model", "groq-key",
        ))

    def test_does_not_attempt_groq_without_its_key(self):
        with (
            patch.object(config, "LLM_PROVIDER", "openai_compat"),
            patch.object(config, "LLM_API_KEY", "gemini-key"),
            patch.object(config, "GROQ_API_KEY", ""),
            patch.object(
                llm, "_chat_json_with_provider",
                side_effect=llm.ProviderError("primary unavailable"),
            ) as request,
        ):
            with self.assertRaisesRegex(llm.ProviderError, "primary unavailable"):
                llm.chat_json("system", "user")

        request.assert_called_once()


class ImageFallbackTests(unittest.TestCase):
    def test_tries_image_fallbacks_in_configured_order(self):
        pollinations = Mock(side_effect=http_error())
        cloudflare = Mock(side_effect=http_error())
        huggingface = Mock(return_value=b"image")
        with (
            patch.object(config, "IMAGE_PROVIDER", "pollinations"),
            patch.object(config, "IMAGE_FALLBACK", "cloudflare,huggingface"),
            patch.dict(image._PROVIDERS, {
                "pollinations": pollinations,
                "cloudflare": cloudflare,
                "huggingface": huggingface,
            }),
        ):
            result = image.generate("prompt", 10, 20, 1)

        self.assertEqual(result, b"image")
        pollinations.assert_called_once_with("prompt", 10, 20, 1, "")
        cloudflare.assert_called_once_with("prompt", 10, 20, 1, "")
        huggingface.assert_called_once_with("prompt", 10, 20, 1, "")

    def test_surfaces_errors_when_every_image_provider_fails(self):
        with (
            patch.object(config, "IMAGE_PROVIDER", "pollinations"),
            patch.object(config, "IMAGE_FALLBACK", "huggingface"),
            patch.dict(image._PROVIDERS, {
                "pollinations": Mock(side_effect=http_error()),
                "huggingface": Mock(side_effect=RuntimeError("token missing")),
            }),
        ):
            with self.assertRaisesRegex(RuntimeError, "All image providers failed"):
                image.generate("prompt", 10, 20, 1)

    def test_pollinations_only_requests_watermark_removal_with_a_key(self):
        response = http_response()
        with (
            patch.object(
                image, "env",
                side_effect=lambda name, default="": "test-key" if name == "POLLINATIONS_API_KEY" else default,
            ),
            patch.object(image.httpx, "get", return_value=response) as request,
            patch.object(image, "_to_jpeg", return_value=b"jpeg"),
        ):
            image._pollinations("prompt", 10, 20, 1)

        self.assertEqual(request.call_args.kwargs["params"]["nologo"], "true")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

        response = http_response()
        with (
            patch.object(image, "env", side_effect=lambda name, default="": "" if name == "POLLINATIONS_API_KEY" else default),
            patch.object(image.httpx, "get", return_value=response) as request,
            patch.object(image, "_to_jpeg", return_value=b"jpeg"),
        ):
            image._pollinations("prompt", 10, 20, 1)

        self.assertNotIn("nologo", request.call_args.kwargs["params"])
        self.assertEqual(request.call_args.kwargs["headers"], {})

    def test_pollinations_retries_service_unavailable_after_retry_after(self):
        import httpx

        unavailable = httpx.Response(
            503,
            headers={"Retry-After": "12"},
            request=httpx.Request("GET", "https://example.test/image"),
        )
        with (
            patch.object(image, "env", side_effect=lambda name, default="": default),
            patch.object(image.httpx, "get", side_effect=[unavailable, http_response()]) as request,
            patch.object(image.time, "sleep") as sleep,
            patch.object(image, "_to_jpeg", return_value=b"jpeg"),
        ):
            result = image._pollinations("prompt", 10, 20, 1)

        self.assertEqual(result, b"jpeg")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(12)

    def test_pollinations_retries_free_limit_402(self):
        import httpx

        free_limit = httpx.Response(
            402,
            text='{"code":"FREE_LIMIT","message":"Retry after 10"}',
            request=httpx.Request("GET", "https://example.test/image"),
        )
        with (
            patch.object(image, "env", side_effect=lambda name, default="": default),
            patch.object(image.httpx, "get", side_effect=[free_limit, http_response()]) as request,
            patch.object(image.time, "sleep") as sleep,
            patch.object(image, "_to_jpeg", return_value=b"jpeg"),
        ):
            result = image._pollinations("prompt", 10, 20, 1)

        self.assertEqual(result, b"jpeg")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(13)

    def test_cloudflare_request_omits_unsupported_seed(self):
        import base64

        response = Mock()
        response.json.return_value = {"result": {"image": base64.b64encode(b"image").decode()}}
        settings = {"CF_ACCOUNT_ID": "account", "CF_API_TOKEN": "token"}
        with (
            patch.object(image, "env", side_effect=lambda name, default="": settings.get(name, default)),
            patch.object(image.httpx, "post", return_value=response) as request,
            patch.object(image, "_to_jpeg", return_value=b"jpeg"),
        ):
            result = image._cloudflare("prompt", 10, 20, 1)

        self.assertEqual(result, b"jpeg")
        self.assertEqual(request.call_args.kwargs["json"], {"prompt": "prompt", "steps": 8})


def http_error():
    import httpx

    return httpx.ConnectError("unavailable")


def http_response():
    import httpx

    return httpx.Response(200, request=httpx.Request("GET", "https://example.test/image"), content=b"image")


if __name__ == "__main__":
    unittest.main()
