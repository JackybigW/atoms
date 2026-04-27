import json

import pytest

from services.preview_smoke import PreviewSmokeRunner, SmokeFailure, _parse_check, load_smoke_contract


def write_smoke_contract(tmp_path, contract):
    atoms_dir = tmp_path / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "smoke.json").write_text(json.dumps(contract), encoding="utf-8")


def smoke_contract_with_check(check):
    return {"version": 1, "checks": [check]}


def valid_smoke_check():
    return {
        "name": "health",
        "service": "backend",
        "method": "GET",
        "path": "/health",
        "expect": {"status": 200},
    }


def test_load_smoke_contract_reads_checks(tmp_path):
    atoms_dir = tmp_path / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "smoke.json").write_text(
        json.dumps(
            {
                "version": 1,
                "checks": [
                    {
                        "name": "health",
                        "service": "backend",
                        "method": "GET",
                        "path": "/health",
                        "expect": {"status": 200, "content_type": "application/json"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    contract = load_smoke_contract(tmp_path)

    assert contract is not None
    assert contract.checks[0].name == "health"
    assert contract.checks[0].path == "/health"


@pytest.mark.asyncio
async def test_preview_smoke_runner_passes_binary_prefix_check(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert container_name == "container-1"
            assert service == "backend"
            assert method == "POST"
            assert path == "/api/generate"
            assert json_body == {"content": "atoms-smoke-test"}
            return 200, {"content-type": "image/png"}, b"\x89PNG\r\n\x1a\nabc"

    contract = {
        "version": 1,
        "checks": [
            {
                "name": "generate png",
                "service": "backend",
                "method": "POST",
                "path": "/api/generate",
                "json": {"content": "atoms-smoke-test"},
                "expect": {"status": 200, "content_type": "image/png", "body_prefix_base64": "iVBORw0KGgo="},
            }
        ],
    }
    atoms_dir = tmp_path / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "smoke.json").write_text(json.dumps(contract), encoding="utf-8")

    result = await PreviewSmokeRunner(Sandbox()).run("container-1", tmp_path)

    assert result.ok is True
    assert result.failures == []


@pytest.mark.asyncio
async def test_preview_smoke_runner_blocks_content_type_mismatch(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            return 200, {"content-type": "application/json"}, b'{"content":"not an image"}'

    atoms_dir = tmp_path / ".atoms"
    atoms_dir.mkdir()
    (atoms_dir / "smoke.json").write_text(
        json.dumps(
            {
                "version": 1,
                "checks": [
                    {
                        "name": "generate png",
                        "service": "backend",
                        "method": "POST",
                        "path": "/api/generate",
                        "json": {"content": "atoms-smoke-test"},
                        "expect": {"status": 200, "content_type": "image/png"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = await PreviewSmokeRunner(Sandbox()).run("container-1", tmp_path)

    assert result.ok is False
    assert result.failures == [
        SmokeFailure(name="generate png", reason="expected content-type image/png, got application/json")
    ]


def test_smoke_contract_required_when_backend_has_api_routes(tmp_path):
    from services.preview_smoke import smoke_contract_required

    openapi = {
        "paths": {
            "/health": {"get": {}},
            "/api/generate": {"post": {}},
        }
    }

    assert smoke_contract_required(openapi) is True


def test_smoke_contract_not_required_for_health_only_backend(tmp_path):
    from services.preview_smoke import smoke_contract_required

    openapi = {"paths": {"/health": {"get": {}}, "/openapi.json": {"get": {}}}}

    assert smoke_contract_required(openapi) is False


def test_smoke_contract_not_required_for_custom_ignored_health_path(tmp_path):
    from services.preview_smoke import smoke_contract_required

    openapi = {"paths": {"/api/health": {"get": {}}}}

    assert smoke_contract_required(openapi, ignored_paths={"/api/health"}) is False


@pytest.mark.asyncio
async def test_preview_smoke_runner_reports_missing_contract_when_api_routes_exist(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, b'{"paths":{"/health":{"get":{}},"/api/generate":{"post":{}}}}'

    result = await PreviewSmokeRunner(Sandbox()).require_contract_if_needed("container-1", tmp_path)

    assert result.ok is False
    assert result.failures == [
        SmokeFailure(name=".atoms/smoke.json", reason="backend exposes API routes but smoke contract is missing")
    ]


@pytest.mark.asyncio
async def test_preview_smoke_runner_ignores_invalid_utf8_openapi_response(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, b"\xff"

    result = await PreviewSmokeRunner(Sandbox()).require_contract_if_needed("container-1", tmp_path)

    assert result.ok is True
    assert result.failures == []


@pytest.mark.asyncio
async def test_preview_smoke_runner_ignores_invalid_json_openapi_response(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, b"not json"

    result = await PreviewSmokeRunner(Sandbox()).require_contract_if_needed("container-1", tmp_path)

    assert result.ok is True
    assert result.failures == []


@pytest.mark.parametrize("body", [b"[]", b'{"paths": []}'])
@pytest.mark.asyncio
async def test_preview_smoke_runner_ignores_wrong_shape_openapi_response(tmp_path, body):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            assert path == "/openapi.json"
            return 200, {"content-type": "application/json"}, body

    result = await PreviewSmokeRunner(Sandbox()).require_contract_if_needed("container-1", tmp_path)

    assert result.ok is True
    assert result.failures == []


def test_load_smoke_contract_rejects_non_list_checks(tmp_path):
    write_smoke_contract(tmp_path, {"version": 1, "checks": {"name": "health"}})

    with pytest.raises(ValueError, match="checks must be a list"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_empty_checks(tmp_path):
    write_smoke_contract(tmp_path, {"version": 1, "checks": []})

    with pytest.raises(ValueError, match="checks must not be empty"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_missing_checks(tmp_path):
    write_smoke_contract(tmp_path, {"version": 1})

    with pytest.raises(ValueError, match="missing checks"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_missing_version(tmp_path):
    write_smoke_contract(tmp_path, {"checks": [valid_smoke_check()]})

    with pytest.raises(ValueError, match="missing version"):
        load_smoke_contract(tmp_path)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("path", "missing path"),
        ("service", "missing service"),
        ("method", "missing method"),
        ("expect", "missing expect"),
    ],
)
def test_load_smoke_contract_rejects_missing_check_fields(tmp_path, field, message):
    check = valid_smoke_check()
    del check[field]
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match=message):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_missing_expect_status(tmp_path):
    check = valid_smoke_check()
    check["expect"] = {}
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match="missing expect.status"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_empty_name(tmp_path):
    check = valid_smoke_check()
    check["name"] = ""
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match="name must be a non-empty string"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_string_status(tmp_path):
    write_smoke_contract(
        tmp_path,
        {
            "version": 1,
            "checks": [
                {
                    "name": "health",
                    "service": "backend",
                    "method": "GET",
                    "path": "/health",
                    "expect": {"status": "200"},
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"check 0 .*expect.status must be an int"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_json_on_get(tmp_path):
    write_smoke_contract(
        tmp_path,
        {
            "version": 1,
            "checks": [
                {
                    "name": "health",
                    "service": "backend",
                    "method": "GET",
                    "path": "/health",
                    "json": {"content": "atoms-smoke-test"},
                    "expect": {"status": 200},
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"check 0 .*json is only valid for POST"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_invalid_body_prefix_base64(tmp_path):
    write_smoke_contract(
        tmp_path,
        {
            "version": 1,
            "checks": [
                {
                    "name": "generate png",
                    "service": "backend",
                    "method": "POST",
                    "path": "/api/generate",
                    "expect": {"status": 200, "body_prefix_base64": "not-base64!!!"},
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"check 0 .*body_prefix_base64 must be valid base64"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_non_string_body_prefix_base64(tmp_path):
    check = valid_smoke_check()
    check["expect"]["body_prefix_base64"] = 123
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match=r"check 0 .*body_prefix_base64 must be a string"):
        load_smoke_contract(tmp_path)


@pytest.mark.parametrize("field", ["content_type", "body_contains"])
def test_load_smoke_contract_rejects_non_string_expectation_fields(tmp_path, field):
    check = valid_smoke_check()
    check["expect"][field] = 123
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match=rf"check 0 .*{field} must be a string"):
        load_smoke_contract(tmp_path)


def test_load_smoke_contract_rejects_non_string_header_value(tmp_path):
    check = valid_smoke_check()
    check["headers"] = {"accept": 123}
    write_smoke_contract(tmp_path, smoke_contract_with_check(check))

    with pytest.raises(ValueError, match=r"check 0 .*headers must be a dict of strings"):
        load_smoke_contract(tmp_path)


def test_parse_smoke_check_rejects_non_string_header_key():
    check = valid_smoke_check()
    check["headers"] = {1: "application/json"}

    with pytest.raises(ValueError, match=r"check 0 .*headers must be a dict of strings"):
        _parse_check(check, 0)


@pytest.mark.asyncio
async def test_preview_smoke_runner_converts_request_exception_to_failure(tmp_path):
    class Sandbox:
        def __init__(self):
            self.calls = 0

        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("connection refused")
            return 503, {"content-type": "application/json"}, b'{"ok":false}'

    write_smoke_contract(
        tmp_path,
        {
            "version": 1,
            "checks": [
                {
                    "name": "health",
                    "service": "backend",
                    "method": "GET",
                    "path": "/health",
                    "expect": {"status": 200},
                },
                {
                    "name": "ready",
                    "service": "backend",
                    "method": "GET",
                    "path": "/ready",
                    "expect": {"status": 200},
                }
            ],
        },
    )

    result = await PreviewSmokeRunner(Sandbox()).run("container-1", tmp_path)

    assert result.ok is False
    assert result.failures == [
        SmokeFailure(name="health", reason="request failed: connection refused"),
        SmokeFailure(name="ready", reason="expected status 200, got 503"),
    ]


@pytest.mark.asyncio
async def test_preview_smoke_runner_blocks_status_mismatch(tmp_path):
    class Sandbox:
        async def smoke_request(self, container_name, *, service, method, path, headers=None, json_body=None):
            return 500, {"content-type": "application/json"}, b'{"ok":false}'

    write_smoke_contract(
        tmp_path,
        {
            "version": 1,
            "checks": [
                {
                    "name": "health",
                    "service": "backend",
                    "method": "GET",
                    "path": "/health",
                    "expect": {"status": 200},
                }
            ],
        },
    )

    result = await PreviewSmokeRunner(Sandbox()).run("container-1", tmp_path)

    assert result.ok is False
    assert result.failures == [SmokeFailure(name="health", reason="expected status 200, got 500")]
