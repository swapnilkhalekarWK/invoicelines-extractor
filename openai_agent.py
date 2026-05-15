"""OpenAI agent invocation for low-confidence invoice extraction analysis."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from env_loader import load_dotenv


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
AI_PROJECT_CONTEXT_PATH = ROOT / "AI_PROJECT_CONTEXT.md"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
AGENT_NAME = "invoice_extraction_failure_analysis_agent"


def invoke_analysis_agent(
    file_record: Mapping[str, Any],
    *,
    confidence_threshold: float,
) -> dict[str, str]:
    """Invoke the OpenAI analysis agent for one low-confidence OCR file."""

    load_dotenv(ENV_PATH)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured in .env yet.")

    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    payload = {
        "model": model,
        "instructions": build_agent_instructions(),
        "input": build_analysis_prompt(file_record, confidence_threshold=confidence_threshold),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API request failed: {exc.code} {detail}") from exc

    return parse_llm_analysis(extract_openai_text(response_payload))


def build_agent_instructions() -> str:
    return (
        f"You are {AGENT_NAME}. Diagnose invoice OCR extraction failures. "
        "Use the included AI_PROJECT_CONTEXT.md content as project memory on every request. "
        "Be concrete, avoid speculation, and return only valid JSON."
    )


def build_analysis_prompt(
    file_record: Mapping[str, Any],
    *,
    confidence_threshold: float,
) -> str:
    project_context = load_ai_project_context()
    diagnostics = json.loads(str(file_record["diagnostics_json"]))
    result = json.loads(str(file_record["result_json"]))
    raw_content = str(file_record["content_text"])

    max_chars = parse_int_env("OPENAI_MAX_OCR_CHARS", 120000)
    if len(raw_content) > max_chars:
        raw_content = raw_content[:max_chars] + "\n\n[OCR content truncated for analysis]"

    return f"""
Agent invocation: {AGENT_NAME}

Project context source: AI_PROJECT_CONTEXT.md

<AI_PROJECT_CONTEXT.md>
{project_context}
</AI_PROJECT_CONTEXT.md>

Task:
Review this failed or low-confidence OCR file and explain why the current regex and geometry based extractor struggled. Then propose concrete implementation changes for a future upgrade.

Return a JSON object with exactly these keys:
- llm_response: a concise explanation for a product user
- fix_proposal: specific engineering changes to try next

MVP confidence threshold: {confidence_threshold:.2f}

File name: {file_record["filename"]}
Current status: {file_record["status"]}
Minimum confidence: {file_record["min_confidence"]}
Line count: {file_record["line_count"]}
Extractor error: {file_record["error"] or "None"}

Current extraction output:
{json.dumps(result, ensure_ascii=False, indent=2)}

Extractor diagnostics:
{json.dumps(diagnostics, ensure_ascii=False, indent=2)}

OCR file content:
{raw_content}
""".strip()


def load_ai_project_context() -> str:
    if not AI_PROJECT_CONTEXT_PATH.exists():
        raise RuntimeError("AI_PROJECT_CONTEXT.md is missing.")
    return AI_PROJECT_CONTEXT_PATH.read_text(encoding="utf-8-sig").strip()


def parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def extract_openai_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]

    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def parse_llm_analysis(text: str) -> dict[str, str]:
    if not text:
        raise RuntimeError("OpenAI returned an empty response.")

    candidate = text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]

    try:
        parsed = json.loads(candidate)
        return {
            "llm_response": str(parsed.get("llm_response", "")).strip() or text,
            "fix_proposal": str(parsed.get("fix_proposal", "")).strip(),
        }
    except json.JSONDecodeError:
        return {"llm_response": text, "fix_proposal": ""}
