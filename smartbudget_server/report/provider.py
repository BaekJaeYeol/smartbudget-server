"""비식별 집계를 Azure OpenAI에 전송해 소비 분석을 생성합니다."""

import asyncio
import json

import httpx

from smartbudget_server.config import Settings


class ProviderFailureError(Exception):
    """외부 분석 요청이나 응답이 실패했음을 나타냅니다."""


class ProviderTimeoutError(Exception):
    """외부 분석 요청이 전체 시간 제한을 초과했음을 나타냅니다."""


async def _request_report(settings: Settings, aggregate: dict) -> str:
    """서버 인증 정보와 집계만 사용해 단일 LLM 요청을 수행합니다."""
    if not all(
        (
            settings.azure_openai_endpoint,
            settings.azure_openai_key,
            settings.azure_openai_deployment,
        )
    ):
        raise ProviderFailureError()
    url = settings.azure_openai_endpoint.rstrip("/") + "/openai/v1/chat/completions"
    body = {
        "model": settings.azure_openai_deployment,
        "messages": [
            {
                "role": "system",
                "content": "당신은 가계 소비 분석가입니다. 주어진 집계만 사용하여 한국어 Markdown 리포트를 작성하고, 지출이 없으면 명시하세요.",
            },
            {"role": "user", "content": json.dumps(aggregate, ensure_ascii=False)},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers={"api-key": settings.azure_openai_key.get_secret_value()},
                json=body,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ProviderFailureError()
            return text
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError() from exc
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderFailureError() from exc


def generate_report(settings: Settings, aggregate: dict) -> str:
    """외부 요청의 시작부터 완료까지 60초를 제한하고 재시도하지 않습니다."""

    async def limited() -> str:
        """네트워크 요청 전체에 단일 제한 시간을 적용합니다."""
        return await asyncio.wait_for(_request_report(settings, aggregate), timeout=60)

    try:
        return asyncio.run(limited())
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ProviderTimeoutError() from exc
