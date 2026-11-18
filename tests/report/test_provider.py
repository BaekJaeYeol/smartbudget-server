"""Azure 모델 호출의 전송 내용과 실패 처리를 검증합니다."""

import json

import httpx
import pytest

from smartbudget_server.config import Settings
from smartbudget_server.report.provider import (
    ProviderFailureError,
    ProviderTimeoutError,
    generate_report,
)


def test_provider_sends_only_aggregate(monkeypatch):
    """모델 요청에는 비식별 집계만 포함하고 Markdown을 반환합니다."""
    seen = []

    def handler(request):
        """외부 서비스 응답을 대체하고 실제 HTTP 본문을 검사합니다."""
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "# 결과"}}]}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(
        _env_file=None,
        jwt_secret="x" * 43,
        azure_openai_endpoint="https://example.org",
        azure_openai_key="test-key",
        azure_openai_deployment="test-model",
    )
    aggregate = {
        "period_type": "daily",
        "period_start": "2026-09-16",
        "period_end": "2026-09-16",
        "categories": [],
    }
    assert generate_report(settings, aggregate) == "# 결과"
    assert json.loads(seen[0]["messages"][1]["content"]) == aggregate
    assert seen[0]["model"] == "test-model"


def test_provider_failures(monkeypatch):
    """제공자 오류와 시간 초과를 서로 다르게 전달합니다."""
    original = httpx.AsyncClient
    settings = Settings(
        _env_file=None,
        jwt_secret="x" * 43,
        azure_openai_endpoint="https://example.org",
        azure_openai_key="test-key",
        azure_openai_deployment="test-model",
    )

    def fail(request):
        """HTTP 오류 응답을 반환합니다."""
        return httpx.Response(500)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(fail), **kwargs),
    )
    with pytest.raises(ProviderFailureError):
        generate_report(settings, {})

    def timeout(request):
        """제공자 시간 초과를 발생시킵니다."""
        raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(timeout), **kwargs),
    )
    with pytest.raises(ProviderTimeoutError):
        generate_report(settings, {})
