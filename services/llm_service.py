from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.config_loader import load_web_settings
from services.http_client import SESSION


class LlmServiceError(RuntimeError):
    pass


def _chat_completions_url(base_url: str) -> str:
    root = str(base_url or "").strip().rstrip("/")
    if not root:
        raise LlmServiceError("LLM base_url is empty")
    if root.endswith("/chat/completions"):
        return root
    return f"{root}/chat/completions"


def llm_config_status() -> Dict[str, Any]:
    settings = load_web_settings()
    llm_settings = settings.get("llm_settings") if isinstance(settings.get("llm_settings"), dict) else {}
    return {
        "enabled": bool(llm_settings.get("enabled")),
        "provider": llm_settings.get("provider") or "",
        "base_url": llm_settings.get("base_url") or "",
        "model": llm_settings.get("model") or "",
        "has_api_key": bool(str(settings.get("llm_api_key") or "").strip()),
        "temperature": llm_settings.get("temperature"),
        "max_tokens": llm_settings.get("max_tokens"),
        "timeout_sec": llm_settings.get("timeout_sec"),
    }


def create_chat_completion(
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    settings = load_web_settings()
    llm_settings = settings.get("llm_settings") if isinstance(settings.get("llm_settings"), dict) else {}
    if not bool(llm_settings.get("enabled")):
        raise LlmServiceError("LLM is disabled in Settings")
    api_key = str(settings.get("llm_api_key") or "").strip()
    if not api_key:
        raise LlmServiceError("LLM API key is missing in Settings")
    if not messages:
        raise LlmServiceError("messages is required")

    payload: Dict[str, Any] = {
        "model": str(model or llm_settings.get("model") or "").strip(),
        "messages": messages,
        "temperature": float(temperature if temperature is not None else llm_settings.get("temperature", 0.2)),
        "max_tokens": int(max_tokens if max_tokens is not None else llm_settings.get("max_tokens", 2048)),
        "stream": False,
    }
    if not payload["model"]:
        raise LlmServiceError("LLM model is empty")
    if response_format:
        payload["response_format"] = response_format
    if extra_body:
        payload.update(extra_body)

    response = SESSION.post(
        _chat_completions_url(str(llm_settings.get("base_url") or "")),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=int(llm_settings.get("timeout_sec") or 60),
    )
    try:
        data = response.json()
    except Exception as exc:
        raise LlmServiceError(f"LLM response is not JSON: HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        message = data.get("message") or data.get("error") or data
        raise LlmServiceError(f"LLM request failed: HTTP {response.status_code}: {message}")
    return data


def complete_text(
    *,
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    result = create_chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice, dict) else {}
    return {
        "content": (message or {}).get("content") or "",
        "usage": result.get("usage") or {},
        "raw": result,
    }
