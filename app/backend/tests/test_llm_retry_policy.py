import httpx

from openai import BadRequestError, RateLimitError

from openmanus_runtime.llm import _is_retryable_openai_error


def _make_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.Response(status_code=status_code, request=request)


def test_retry_policy_does_not_retry_bad_request_errors():
    error = BadRequestError(
        "invalid params, tool result id not found",
        response=_make_response(400),
        body={"error": {"message": "bad request"}},
    )

    assert _is_retryable_openai_error(error) is False


def test_retry_policy_still_retries_transient_rate_limit_errors():
    error = RateLimitError(
        "rate limit",
        response=_make_response(429),
        body={"error": {"message": "rate limit"}},
    )

    assert _is_retryable_openai_error(error) is True
