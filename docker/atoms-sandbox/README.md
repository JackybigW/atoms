# atoms-sandbox image

Base runtime for atoms per-project sandboxes. The backend builds/tags this as
`atoms-sandbox:latest` and references it from `SandboxRuntimeService`.

## Build

```
docker build -t atoms-sandbox:latest docker/atoms-sandbox
```

## Run contract

The backend starts each sandbox with:

```
docker run -d \
  --name atoms-<user>-<project_id> \
  -v <host_workspace>:/workspace \
  -w /workspace \
  -p 0:3000 -p 0:8000 \
  -e ATOMS_PROJECT_ID=<project_id> \
  atoms-sandbox:latest \
  sleep infinity
```

After the SWE agent finishes editing the project, the backend calls
`docker exec <container> /usr/local/bin/start-dev`, which installs deps if
needed and launches `pnpm run dev` bound to `0.0.0.0:3000` with the correct
`--base` for the preview reverse proxy.

After the SWE agent finishes editing the project, the backend calls
`docker exec <container> /usr/local/bin/start-preview`, which reads `.atoms/preview.json`,
starts the frontend on `3000`, optionally starts the backend on `8000`, and keeps both
reachable through same-origin preview proxy routes.

## Preview Manifest

Generated apps should write `/workspace/.atoms/preview.json`:

```json
{
  "frontend": {
    "command": "pnpm run dev -- --host 0.0.0.0 --port 3000",
    "healthcheck_path": "/"
  },
  "backend": {
    "command": "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000",
    "healthcheck_path": "/health"
  }
}
```

Preview routes:

- `/preview/<preview_session_key>/frontend/`
- `/preview/<preview_session_key>/backend/`
