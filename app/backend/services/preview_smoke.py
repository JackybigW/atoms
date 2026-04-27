import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


SmokeService = Literal["frontend", "backend"]
SmokeMethod = Literal["GET", "POST"]


@dataclass(frozen=True)
class SmokeExpectation:
    status: int
    content_type: str | None = None
    body_contains: str | None = None
    body_prefix_base64: str | None = None


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    service: SmokeService
    method: SmokeMethod
    path: str
    expect: SmokeExpectation
    headers: dict[str, str] | None = None
    json_body: Any = None


@dataclass(frozen=True)
class SmokeContract:
    version: int
    checks: list[SmokeCheck]


@dataclass(frozen=True)
class SmokeFailure:
    name: str
    reason: str


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    failures: list[SmokeFailure]


def load_smoke_contract(host_root: Path) -> SmokeContract | None:
    contract_path = Path(host_root) / ".atoms" / "smoke.json"
    if not contract_path.exists():
        return None

    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    version = payload["version"]
    if version != 1:
        raise ValueError(f"Unsupported smoke contract version: {version!r}")

    return SmokeContract(
        version=version,
        checks=[_parse_check(check) for check in payload.get("checks", [])],
    )


def _parse_check(payload: dict[str, Any]) -> SmokeCheck:
    path = payload["path"]
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Smoke check path must start with /")

    service = payload["service"]
    if service not in ("frontend", "backend"):
        raise ValueError(f"Unsupported smoke check service: {service!r}")

    method = payload["method"]
    if method not in ("GET", "POST"):
        raise ValueError(f"Unsupported smoke check method: {method!r}")

    expect_payload = payload["expect"]
    expect = SmokeExpectation(
        status=expect_payload["status"],
        content_type=expect_payload.get("content_type"),
        body_contains=expect_payload.get("body_contains"),
        body_prefix_base64=expect_payload.get("body_prefix_base64"),
    )
    headers = payload.get("headers")

    return SmokeCheck(
        name=payload["name"],
        service=service,
        method=method,
        path=path,
        headers={str(key): str(value) for key, value in headers.items()} if headers else None,
        json_body=payload.get("json"),
        expect=expect,
    )


def smoke_contract_required(openapi: dict[str, Any]) -> bool:
    ignored_paths = {"/health", "/openapi.json", "/docs", "/redoc"}
    http_methods = {"get", "post", "put", "patch", "delete"}

    for path, path_item in openapi.get("paths", {}).items():
        if path in ignored_paths:
            continue
        if isinstance(path_item, dict) and http_methods.intersection(path_item):
            return True
    return False


class PreviewSmokeRunner:
    def __init__(self, sandbox_service: Any):
        self.sandbox_service = sandbox_service

    async def run(self, container_name: str, host_root: Path) -> SmokeResult:
        contract = load_smoke_contract(host_root)
        if contract is None:
            return SmokeResult(ok=True, failures=[])

        failures: list[SmokeFailure] = []
        for check in contract.checks:
            status, headers, body = await self.sandbox_service.smoke_request(
                container_name,
                service=check.service,
                method=check.method,
                path=check.path,
                headers=check.headers,
                json_body=check.json_body,
            )
            failures.extend(_validate_response(check, status, headers, body))

        return SmokeResult(ok=not failures, failures=failures)


def _validate_response(
    check: SmokeCheck,
    status: int,
    headers: dict[str, str],
    body: bytes,
) -> list[SmokeFailure]:
    failures: list[SmokeFailure] = []
    expect = check.expect

    if status != expect.status:
        failures.append(SmokeFailure(name=check.name, reason=f"expected status {expect.status}, got {status}"))

    if expect.content_type is not None:
        actual_content_type = _content_type(headers)
        expected_content_type = expect.content_type.lower()
        if actual_content_type != expected_content_type:
            failures.append(
                SmokeFailure(
                    name=check.name,
                    reason=f"expected content-type {expected_content_type}, got {actual_content_type}",
                )
            )

    if expect.body_contains is not None:
        text = body.decode("utf-8", errors="replace")
        if expect.body_contains not in text:
            failures.append(SmokeFailure(name=check.name, reason=f"expected body to contain {expect.body_contains!r}"))

    if expect.body_prefix_base64 is not None:
        expected_prefix = base64.b64decode(expect.body_prefix_base64)
        if not body.startswith(expected_prefix):
            failures.append(SmokeFailure(name=check.name, reason="expected body to match binary prefix"))

    return failures


def _content_type(headers: dict[str, str]) -> str:
    for name, value in headers.items():
        if name.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""
