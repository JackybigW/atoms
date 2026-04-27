import json

import pytest

from services.preview_smoke import PreviewSmokeRunner, SmokeFailure, load_smoke_contract


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
