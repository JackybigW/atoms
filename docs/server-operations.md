# Server Operations

Production server:

- Host: `129.211.217.58`
- Project path: `/home/ubuntu/atoms`
- Main branch: `main`

## Public Ingress

The public site currently reaches the server through Cloudflare Tunnel. The
tunnel hostname route is managed in the Cloudflare Dashboard, not in a local
`cloudflared` config file, because the service runs in token mode:

```text
cloudflared tunnel run --token ...
```

The production request path is:

```text
Cloudflare Tunnel -> http://localhost:8000 -> nginx -> app/frontend/dist
                                                   -> /api/     -> FastAPI :8001
                                                   -> /preview/ -> FastAPI :8001
```

Current live ports:

```text
nginx ingress: 80, 443, 8000, 8080
FastAPI backend: 8001
Clash proxy: 7890
PostgreSQL: 5432
```

Cloudflare public hostnames for the same Atoms app should route to:

```text
http://localhost:8000
```

Use `http://localhost:8001` only for a deliberate API-only hostname. That
bypasses the frontend and nginx SPA fallback.

The live nginx config is:

```text
/etc/nginx/conf.d/atoms.conf
```

The repo template is:

```text
deploy/nginx-atoms.conf
```

`deploy.sh` does not copy the repo template into `/etc/nginx/conf.d/`. It
builds the frontend and restarts `atoms-backend`, so nginx config changes must
be applied deliberately and verified with:

```bash
nginx -t
systemctl reload nginx
```

## Network Proxy

Use Clash as the only server-side outbound proxy.

- Service: `clash.service`
- Binary: `/usr/local/bin/clash`
- Config directory: `/etc/clash`
- Config file: `/etc/clash/config.yaml`
- Mixed proxy port: `7890`
- Clash API: `127.0.0.1:9090`
- Subscription URL: `https://yfssce.net/s/9c38abc412c5746f79b5ce98db6d6758`

The subscription currently returns a base64 node list, not raw Clash YAML.
Generate `/etc/clash/config.yaml` from that subscription before restarting
Clash. Keep the `Auto` url-test group enabled so the server can choose a
working node automatically.

Vibe Coding Studio production constrains Clash outbound traffic to non-direct Singapore,
Japan, Taiwan, United States, and Korea nodes. Apply the policy after updating
the subscription config:

```bash
cd /home/ubuntu/atoms
bash scripts/configure-server-clash-policy.sh
```

The policy removes Hong Kong, direct, metadata, and unrelated region nodes from
the active config. `Auto` uses `url-test` with a `500ms` tolerance so it does not
switch nodes for small latency changes.

Do not run `xray` or `v2ray` alongside Clash on this server. They were removed
to avoid competing proxy ports and unstable Docker/Git outbound behavior.

## Docker Proxy

Docker daemon pulls should go through Clash:

```ini
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1"
```

The active drop-in is:

```text
/etc/systemd/system/docker.service.d/http-proxy.conf
```

Avoid registry mirrors unless they have been verified. A stale mirror can fail
before Docker falls back to the next source.

## Git Proxy

For one-off GitHub operations from the server, prefer explicit proxy variables:

```bash
cd /home/ubuntu/atoms
http_proxy=http://127.0.0.1:7890 \
https_proxy=http://127.0.0.1:7890 \
git fetch origin main
```

This avoids relying on global Git proxy state.

## Sandbox Image

After changes under `docker/atoms-sandbox`, rebuild the runtime image on the
server with retry support:

```bash
cd /home/ubuntu/atoms
scripts/build-atoms-sandbox.sh
```

The script retries failed Docker builds up to three times. Before each retry it
asks the Clash `Auto` group to refresh latency selection through the local Clash
API, then rebuilds with `--network=host` and the Docker proxy build args.

Useful overrides:

```bash
ATOMS_SANDBOX_MAX_ATTEMPTS=5 \
ATOMS_SANDBOX_IMAGE_TAG=atoms-sandbox:latest \
CLASH_PROXY_GROUP=Auto \
scripts/build-atoms-sandbox.sh
```
