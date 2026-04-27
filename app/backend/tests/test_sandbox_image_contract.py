from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[3] / "docker" / "atoms-sandbox" / "Dockerfile"


def dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def test_sandbox_image_installs_native_qr_decode_dependency() -> None:
    assert "libzbar0" in dockerfile_text()


def test_sandbox_image_prewarms_common_fastapi_and_qr_packages() -> None:
    text = dockerfile_text()

    for package in (
        "fastapi",
        "uvicorn[standard]",
        "python-multipart",
        "pillow",
        "qrcode[pil]",
        "pyzbar",
    ):
        assert package in text


def test_sandbox_image_declares_runtime_cache_env() -> None:
    text = dockerfile_text()

    assert "UV_CACHE_DIR=/root/.cache/uv" in text
    assert "PNPM_STORE_DIR=/root/.local/share/pnpm/store" in text
