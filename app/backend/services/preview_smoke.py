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
    if not isinstance(payload, dict):
        raise ValueError("Smoke contract payload must be a dict")

    if "version" not in payload:
        raise ValueError("Smoke contract missing version")

    version = payload["version"]
    if version != 1:
        raise ValueError(f"Unsupported smoke contract version: {version!r}")

    if "checks" not in payload:
        raise ValueError("Smoke contract missing checks")

    checks = payload["checks"]
    if not isinstance(checks, list):
        raise ValueError("Smoke contract checks must be a list")
    if not checks:
        raise ValueError("Smoke contract checks must not be empty")

    return SmokeContract(
        version=version,
        checks=[_parse_check(check, index) for index, check in enumerate(checks)],
    )


def _parse_check(payload: Any, index: int) -> SmokeCheck:
    if not isinstance(payload, dict):
        raise ValueError(f"Smoke check {index} must be a dict")

    if "name" not in payload:
        raise ValueError(f"Smoke check {index} missing name")

    name = payload["name"]
    if not isinstance(name, str) or not name:
        raise ValueError(f"Smoke check {index} name must be a non-empty string")

    label = f"Smoke check {index} ({name})"
    path = _required_check_field(payload, "path", label)
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"{label} path must start with /")

    service = _required_check_field(payload, "service", label)
    if service not in ("frontend", "backend"):
        raise ValueError(f"{label} has unsupported service: {service!r}")

    method = _required_check_field(payload, "method", label)
    if method not in ("GET", "POST"):
        raise ValueError(f"{label} has unsupported method: {method!r}")

    if method == "GET" and "json" in payload:
        raise ValueError(f"{label} json is only valid for POST")

    expect_payload = _required_check_field(payload, "expect", label)
    if not isinstance(expect_payload, dict):
        raise ValueError(f"{label} expect must be a dict")

    if "status" not in expect_payload:
        raise ValueError(f"{label} missing expect.status")

    status = expect_payload["status"]
    if type(status) is not int:
        raise ValueError(f"{label} expect.status must be an int")

    content_type = _optional_string(expect_payload, "content_type", label)
    body_contains = _optional_string(expect_payload, "body_contains", label)
    body_prefix_base64 = _optional_string(expect_payload, "body_prefix_base64", label)
    if body_prefix_base64:
        try:
            base64.b64decode(body_prefix_base64, validate=True)
        except ValueError as exc:
            raise ValueError(f"{label} body_prefix_base64 must be valid base64") from exc

    expect = SmokeExpectation(
        status=status,
        content_type=content_type,
        body_contains=body_contains,
        body_prefix_base64=body_prefix_base64,
    )
    headers = payload.get("headers")
    if headers is not None and not isinstance(headers, dict):
        raise ValueError(f"{label} headers must be a dict")
    if headers is not None and not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
        raise ValueError(f"{label} headers must be a dict of strings")

    return SmokeCheck(
        name=name,
        service=service,
        method=method,
        path=path,
        headers=headers,
        json_body=payload.get("json"),
        expect=expect,
    )


def _required_check_field(payload: dict[str, Any], field: str, label: str) -> Any:
    if field not in payload:
        raise ValueError(f"{label} missing {field}")
    return payload[field]


def _optional_string(payload: dict[str, Any], field: str, label: str) -> str | None:
    value = payload.get(field)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} {field} must be a string")
    return value


def smoke_contract_required(openapi: Any, *, ignored_paths: set[str] | None = None) -> bool:
    all_ignored_paths = {"/health", "/openapi.json", "/docs", "/redoc"}
    if ignored_paths is not None:
        all_ignored_paths.update(ignored_paths)
    http_methods = {"get", "post", "put", "patch", "delete"}

    if not isinstance(openapi, dict):
        return False
    paths = openapi.get("paths", {})
    if not isinstance(paths, dict):
        return False

    for path, path_item in paths.items():
        if path in all_ignored_paths:
            continue
        if isinstance(path_item, dict) and http_methods.intersection(path_item):
            return True
    return False


class PreviewSmokeRunner:
    def __init__(self, sandbox_service: Any):
        self.sandbox_service = sandbox_service

    async def require_contract_if_needed(
        self,
        container_name: str,
        host_root: Path,
        *,
        ignored_paths: set[str] | None = None,
    ) -> SmokeResult:
        try:
            contract = load_smoke_contract(host_root)
        except ValueError:
            return SmokeResult(ok=True, failures=[])
        if contract is not None:
            return SmokeResult(ok=True, failures=[])

        status, headers, body = await self.sandbox_service.smoke_request(
            container_name,
            service="backend",
            method="GET",
            path="/openapi.json",
        )
        if status != 200:
            return SmokeResult(ok=True, failures=[])
        try:
            openapi = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return SmokeResult(ok=True, failures=[])

        if not smoke_contract_required(openapi, ignored_paths=ignored_paths):
            return SmokeResult(ok=True, failures=[])

        return SmokeResult(
            ok=False,
            failures=[
                SmokeFailure(
                    name=".atoms/smoke.json",
                    reason="backend exposes API routes but smoke contract is missing",
                )
            ],
        )

    async def run(self, container_name: str, host_root: Path) -> SmokeResult:
        contract = load_smoke_contract(host_root)
        if contract is None:
            return SmokeResult(ok=True, failures=[])

        failures: list[SmokeFailure] = []
        for check in contract.checks:
            try:
                status, headers, body = await self.sandbox_service.smoke_request(
                    container_name,
                    service=check.service,
                    method=check.method,
                    path=check.path,
                    headers=check.headers,
                    json_body=check.json_body,
                )
            except Exception as exc:
                failures.append(SmokeFailure(name=check.name, reason=f"request failed: {exc}"))
                continue
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
