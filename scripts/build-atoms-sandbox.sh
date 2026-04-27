#!/usr/bin/env bash
set -u

IMAGE_TAG="${ATOMS_SANDBOX_IMAGE_TAG:-atoms-sandbox:latest}"
CONTEXT="${ATOMS_SANDBOX_CONTEXT:-docker/atoms-sandbox}"
MAX_ATTEMPTS="${ATOMS_SANDBOX_MAX_ATTEMPTS:-3}"
RETRY_SLEEP_SECONDS="${ATOMS_SANDBOX_RETRY_SLEEP_SECONDS:-2}"
PROXY_URL="${ATOMS_PROXY_URL:-http://127.0.0.1:7890}"
CLASH_API="${CLASH_API:-http://127.0.0.1:9090}"
CLASH_PROXY_GROUP="${CLASH_PROXY_GROUP:-Auto}"
CLASH_TEST_URL="${CLASH_TEST_URL:-https://www.gstatic.com/generate_204}"

if ! [[ "${MAX_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "ATOMS_SANDBOX_MAX_ATTEMPTS must be a positive integer" >&2
  exit 2
fi

urlencode() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import quote

print(quote(sys.argv[1], safe=""))
PY
}

refresh_clash_group() {
  local group
  local test_url
  group="$(urlencode "${CLASH_PROXY_GROUP}")"
  test_url="$(urlencode "${CLASH_TEST_URL}")"

  echo "Refreshing Clash proxy group '${CLASH_PROXY_GROUP}' before retry..." >&2
  if ! curl -fsS --max-time 15 \
    "${CLASH_API%/}/proxies/${group}/delay?timeout=5000&url=${test_url}" >/dev/null; then
    echo "Warning: failed to refresh Clash proxy group '${CLASH_PROXY_GROUP}'. Retrying Docker build anyway." >&2
  fi
}

build_image() {
  DOCKER_BUILDKIT=1 docker build --network=host \
    --build-arg "http_proxy=${PROXY_URL}" \
    --build-arg "https_proxy=${PROXY_URL}" \
    --build-arg "HTTP_PROXY=${PROXY_URL}" \
    --build-arg "HTTPS_PROXY=${PROXY_URL}" \
    -t "${IMAGE_TAG}" "${CONTEXT}"
}

attempt=1
while [[ "${attempt}" -le "${MAX_ATTEMPTS}" ]]; do
  echo "Building atoms sandbox image '${IMAGE_TAG}' attempt ${attempt}/${MAX_ATTEMPTS}..." >&2
  if build_image; then
    echo "Built atoms sandbox image '${IMAGE_TAG}'." >&2
    exit 0
  fi

  status=$?
  if [[ "${attempt}" -eq "${MAX_ATTEMPTS}" ]]; then
    echo "Docker build failed after ${MAX_ATTEMPTS} attempts." >&2
    exit "${status}"
  fi

  refresh_clash_group
  sleep "${RETRY_SLEEP_SECONDS}"
  attempt=$((attempt + 1))
done
