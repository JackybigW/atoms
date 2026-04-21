import re
from dataclasses import dataclass


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
    r"^(?:how\s+do\s+i|how\s+should\s+i|how\s+can\s+i|what\s+is\s+the\s+best\s+way\s+to|what\s+should\s+i|why\s+should\s+i)\b",
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
