import logging
import os
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


IMPLEMENTATION_KEYWORDS = (
    "build",
    "implement",
    "create",
    "add",
    "update",
    "modify",
    "新增",
    "添加",
    "修改",
    "增加",
    "fix",
)
BACKEND_KEYWORDS = (
    "api",
    "backend",
    "database",
    "auth",
    "storage",
    "payment",
    "后端",
    "接口",
)

_IMPLEMENTATION_PATTERN = re.compile(r"\b(?:build|implement|create|add|update|modify|fix)\b", re.IGNORECASE)
_BACKEND_PATTERN = re.compile(r"\b(?:api|backend|database|auth|storage|payment)\b", re.IGNORECASE)
_ADVISORY_QUESTION_PATTERN = re.compile(
    r"^(?:how\s+do\s+i|how\s+should\s+i|how\s+can\s+i|what(?:'s|\s+is)\s+the\s+best\s+way\s+to|what\s+is\s+the\s+best\s+way\s+to|what\s+should\s+i|why\s+should\s+i|can\s+i|should\s+we)\b",
    re.IGNORECASE,
)
_POLITE_EXECUTION_PATTERN = re.compile(r"^(?:can\s+you|could\s+you|please)\b", re.IGNORECASE)


@dataclass(frozen=True)
class BootstrapContext:
    mode: str
    requires_backend_readme: bool
    requires_draft_plan: bool


def _contains_keyword(prompt: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in prompt for keyword in keywords)


def classify_user_request(prompt: str) -> BootstrapContext:
    lowered = prompt.lower()
    advisory_question = bool(_ADVISORY_QUESTION_PATTERN.search(lowered))
    polite_execution = bool(_POLITE_EXECUTION_PATTERN.search(lowered))
    implementation = bool(_IMPLEMENTATION_PATTERN.search(lowered)) or _contains_keyword(prompt, IMPLEMENTATION_KEYWORDS[6:])
    if advisory_question and not polite_execution:
        implementation = False
    backend = bool(_BACKEND_PATTERN.search(lowered)) or _contains_keyword(prompt, BACKEND_KEYWORDS)
    return BootstrapContext(
        mode="implementation" if implementation else "conversation",
        requires_backend_readme=backend,
        requires_draft_plan=implementation,
    )


def build_bootstrap_context(prompt: str) -> BootstrapContext:
    return classify_user_request(prompt)


# ---------------------------------------------------------------------------
# LLM-based classifier (async, production path)
# ---------------------------------------------------------------------------

class _ClassificationResult(BaseModel):
    mode: Literal["implementation", "conversation"]
    requires_backend_readme: bool

_CLASSIFICATION_SYSTEM_PROMPT = """\
You are a request classifier for an AI coding assistant called Alex.
Classify the user's request and respond ONLY with a JSON object.

Rules:
- mode "implementation": user wants code written, features built, pages created, files modified, bugs fixed, or tasks executed
- mode "conversation": user is asking a question, seeking advice, reviewing, or discussing without requesting code changes
- requires_backend_readme: true if the request involves backend/API/database/auth/storage/payment features

Edge cases:
- "帮我做/创建/新增/添加 X" → implementation
- "Can you / Could you / Please + [action]" → implementation
- "How do I / What's the best way to / Should we" → conversation
- "Can I / Should I" (self-question) → conversation

Respond with valid JSON only, no markdown fences."""


async def _classify_with_llm(prompt: str) -> _ClassificationResult:
    from langchain_deepseek import ChatDeepSeek

    api_key = os.getenv("DEEPSEEK_API_KEY")
    llm = ChatDeepSeek(model="deepseek-chat", api_key=api_key, temperature=0)
    structured_llm = llm.with_structured_output(_ClassificationResult, method="json_mode")
    result = await structured_llm.ainvoke([
        {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return result


async def classify_user_request_async(prompt: str) -> BootstrapContext:
    """LLM-based classifier with regex fallback on error."""
    try:
        result = await _classify_with_llm(prompt)
        return BootstrapContext(
            mode=result.mode,
            requires_backend_readme=result.requires_backend_readme,
            requires_draft_plan=result.mode == "implementation",
        )
    except Exception as exc:
        logger.warning("LLM classification failed, falling back to regex: %s", exc)
        return classify_user_request(prompt)
