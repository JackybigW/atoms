# App View Full-Stack Preview Design

Date: 2026-04-21
Status: Proposed
Branch: `feature/app-view-preview`

## Summary

App Viewer should behave like a real in-product browser for platform-generated apps:

- render the sandboxed frontend inside the platform UI
- let users click through the app normally
- support app flows such as sign-up, login, service actions, billing, and subscription flows
- keep frontend and generated backend online behind a same-origin gateway

The recommended design is:

- keep the main platform auth model unchanged
- introduce a preview-session key scoped to a single runtime session
- expose sandbox frontend and sandbox backend through same-origin preview gateway routes
- move runtime startup from frontend-only `start-dev` semantics to a full preview runtime contract
- require platform-generated apps to consume injected preview base URLs instead of assuming root `/api`

This is a full-stack preview design, not a generic arbitrary-app reverse proxy.

## Problem Statement

The current preview path can only partially support frontend rendering and does not provide a usable full-stack application preview.

Current behavior:

- frontend `ensureWorkspaceRuntime()` uses bare `fetch`, so it does not reliably authenticate
- preview iframe loads a protected proxy route, but iframe navigation cannot send the Bearer token stored in `localStorage`
- sandbox startup only launches the frontend dev server on port `3000`
- preview proxy only forwards traffic to `frontend_port`
- generated app backend traffic is not started, routed, or authenticated through App Viewer

As a result:

- App Viewer often fails to load
- even when frontend HTML loads, backend-dependent features do not work
- the current route model cannot support realistic SaaS preview flows

## Goals

- Make App Viewer reliably load for authenticated platform users.
- Provide same-origin access to both sandbox frontend and sandbox backend.
- Keep preview access isolated per user, project, and runtime session.
- Preserve current main app auth architecture for non-preview traffic.
- Support platform-generated full-stack apps that need working frontend and backend flows during preview.
- Keep the design compatible with future realtime upgrades, but do not require a platform-wide WebSocket migration now.

## Non-Goals

- Support arbitrary third-party apps with unknown routing assumptions.
- Replace the platform's existing REST + Bearer auth model.
- Introduce multi-user collaborative preview in this iteration.
- Solve long-term production hosting or deployment architecture for generated apps.
- Rewrite the entire runtime subsystem.

## Current Findings

### 1. App Viewer authentication is mismatched to iframe navigation

Relevant files:

- [app/frontend/src/lib/workspaceRuntime.ts](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/app/frontend/src/lib/workspaceRuntime.ts)
- [app/frontend/src/pages/ProjectWorkspace.tsx](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/app/frontend/src/pages/ProjectWorkspace.tsx)
- [app/backend/routers/workspace_runtime.py](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/app/backend/routers/workspace_runtime.py)

Problems:

- `ensureWorkspaceRuntime()` does not reuse the authenticated client wrapper.
- iframe requests to preview routes cannot attach the platform Bearer token from `localStorage`.
- the preview proxy currently depends on `get_current_user`, which only works for API-style authenticated requests, not tokenless iframe navigation.

### 2. Preview runtime only starts the frontend

Relevant files:

- [docker/atoms-sandbox/start-dev](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/docker/atoms-sandbox/start-dev)
- [app/backend/routers/agent_runtime.py](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/app/backend/routers/agent_runtime.py)
- [app/backend/services/sandbox_runtime.py](/Users/jackywang/Documents/atoms/.worktrees/feature-app-view-preview/app/backend/services/sandbox_runtime.py)

Problems:

- runtime bootstrap only launches the frontend dev server on `3000`
- no generated app backend process is started
- no backend healthcheck is performed
- no proxy route exists for sandbox backend traffic

### 3. Same-origin full-stack behavior is not defined

The generated frontend currently has no stable contract for how to reach its sandbox backend from inside App Viewer.

Without a contract:

- apps that call `/api/...` hit the platform backend, not the generated app backend
- apps that need a preview-specific backend base URL have nowhere to read it from
- preview routing remains frontend-only

## Options Considered

### Option A: Preview token plus frontend-only proxy

Description:

- fix iframe access by replacing Bearer auth with a preview token
- continue proxying only frontend traffic

Pros:

- smallest change
- quickly fixes blank/unauthorized App Viewer loads

Cons:

- does not deliver full-stack preview
- backend-dependent app flows still fail
- not sufficient for login, billing, service, or subscription testing

Decision:

- rejected as insufficient

### Option B: Cookie/session auth for preview and platform APIs

Description:

- migrate preview and possibly wider platform auth toward cookie-backed browser sessions

Pros:

- naturally compatible with iframe navigation
- clean browser semantics

Cons:

- much larger auth architecture change
- broad impact outside preview
- unnecessary for the immediate App Viewer scope

Decision:

- rejected for this iteration

### Option C: Preview-session key plus same-origin full-stack gateway

Description:

- keep existing platform auth for normal app APIs
- issue a random preview-session key tied to one runtime session
- expose sandbox frontend and sandbox backend behind same-origin gateway routes
- launch both services inside the sandbox when the project declares a preview contract

Pros:

- solves iframe access cleanly
- supports full-stack app flows
- isolates preview traffic per runtime session
- avoids rewriting main auth
- scales naturally to future preview improvements

Cons:

- requires runtime schema updates
- requires a preview contract for generated apps
- requires new backend proxy paths and startup orchestration

Decision:

- recommended

## Recommended Design

## 1. Preview Session Identity

Use a runtime-scoped preview session key stored on `workspace_runtime_sessions`.

New fields on the session model:

- `preview_session_key`: random, unguessable identifier
- `preview_expires_at`: timestamp
- `frontend_status`: optional health state
- `backend_status`: optional health state

Rules:

- key is generated when a runtime session is created or recreated
- key remains stable for the life of that runtime session
- key rotates when the runtime is replaced
- preview access is granted by possession of this key, not by Bearer auth on iframe requests

Rationale:

- iframe navigation needs a browser-friendly access mechanism
- stable session key allows the frontend dev server `base` path to remain valid while the runtime stays alive

## 2. Gateway Route Shape

Expose preview traffic under same-origin paths owned by the platform backend.

Recommended route layout:

- frontend root: `/preview/{preview_session_key}/frontend/`
- frontend assets and SPA routes: `/preview/{preview_session_key}/frontend/{path:path}`
- generated app backend: `/preview/{preview_session_key}/backend/{path:path}`

Platform routes remain unchanged:

- runtime ensure/status stays under `/api/v1/workspace-runtime/...`
- CRUD APIs stay under `/api/v1/...`

Behavior:

- App Viewer iframe loads `preview_frontend_url`
- requests from inside the iframe to the generated backend use `preview_backend_url`
- both frontend and backend are same-origin with the platform host because they are served through platform-owned proxy paths

## 3. Ensure API Response

Extend the workspace runtime ensure response to include the full preview bundle:

- `preview_frontend_url`
- `preview_backend_url`
- `preview_session_key`
- `preview_expires_at`
- `frontend_port`
- `backend_port`
- service status metadata

Frontend integration changes:

- stop using raw `fetch()` for ensure
- use the authenticated client wrapper or shared auth-header helper
- Project Workspace stores the returned preview bundle instead of only a single `preview_url`

## 4. Full Preview Runtime Contract

App Viewer cannot support full-stack preview if runtime startup is limited to `pnpm run dev`.

Introduce a project-level preview contract file:

- path: `.atoms/preview.json`

Recommended shape:

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

Rules:

- frontend section is required for preview UI
- backend section is optional for frontend-only projects
- generated full-stack SaaS apps should always emit this file
- if no preview contract exists, runtime falls back to frontend-only behavior

Rationale:

- autodetection across arbitrary stacks is brittle
- a manifest gives the runtime an explicit contract
- agents can update this file as the project evolves

## 5. Sandbox Startup Changes

Replace current frontend-only startup semantics with a preview runtime launcher.

Recommended change:

- introduce `start-preview` inside `docker/atoms-sandbox`
- `start-preview` reads `.atoms/preview.json`
- it launches:
  - frontend command on `3000`
  - backend command on `8000` when declared
- it writes logs separately for frontend and backend
- it exports preview env vars before launching processes

Required injected env vars:

- `ATOMS_PREVIEW_FRONTEND_BASE=/preview/{preview_session_key}/frontend/`
- `ATOMS_PREVIEW_BACKEND_BASE=/preview/{preview_session_key}/backend/`
- `ATOMS_PROJECT_ID=<id>`

Behavioral changes in runtime service:

- start both services when available
- wait for frontend readiness
- wait for backend readiness when backend is declared
- record readiness status separately

## 6. Generated App Contract

For platform-generated apps, preview support should rely on an explicit contract rather than guessing how each app reaches its backend.

Required app behavior:

- generated frontend code should read preview backend base from injected env or config
- generated backend should listen on the declared backend port
- app templates should avoid assuming root `/api` for preview mode

Recommended frontend contract:

- generated frontend receives `VITE_ATOMS_PREVIEW_BACKEND_BASE`
- app templates use that value as the backend base URL in preview mode

This design supports platform-generated SaaS apps. It does not attempt to transparently rewrite arbitrary third-party apps that hardcode `/api`.

## 7. Proxy Responsibilities

Frontend preview proxy:

- forwards HTML, JS, CSS, images, source maps, and navigation requests to `frontend_port`
- preserves query string and method
- supports SPA history fallback behavior from the dev server

Backend preview proxy:

- forwards HTTP API traffic to `backend_port`
- preserves request body, query string, headers, and status codes
- strips hop-by-hop headers
- supports non-GET methods

Recommended additional support:

- WebSocket proxy support for frontend HMR and generated app realtime features

Reason:

- Vite HMR relies on WebSocket upgrade behavior
- some generated apps may also use WebSockets or SSE

This does not require a platform-wide WebSocket migration. It only requires preview-route proxy support for websocket upgrades.

## 8. Security Model

Preview traffic is public only to holders of the preview-session key and only for the life of that runtime session.

Security properties:

- key is random and unguessable
- key is scoped to one runtime session
- key expires with the runtime session
- key can be revoked by recycling the runtime
- preview routes never expose the raw container name or host port

Additional guardrails:

- reject expired preview session keys
- reject sessions not in `running` state
- validate that frontend/backend ports belong to the session row
- keep iframe sandbox restrictions in place

## 9. Frontend UX Changes

App Viewer should stop treating preview as a single opaque URL string.

State changes:

- store `preview_frontend_url`
- store `preview_backend_url`
- surface runtime status when frontend is up but backend is not
- keep `previewKey` remount behavior for session changes

UI behavior:

- if frontend is ready and backend is unavailable, show degraded preview state with an explicit banner
- if no preview contract exists, show a frontend-only preview state instead of a generic failure
- expose a small debug detail surface for current preview mode and service status

## 10. Migration Strategy

Phase 1:

- make ensure authenticated
- add preview-session key model fields
- add frontend and backend gateway routes
- return full preview bundle from ensure
- switch iframe to the new frontend preview URL

Phase 2:

- add `.atoms/preview.json`
- introduce `start-preview`
- start and monitor backend processes
- add backend preview proxy

Phase 3:

- wire generated templates to `ATOMS_PREVIEW_BACKEND_BASE`
- add websocket upgrade support for preview routes
- improve runtime status UX in App Viewer

## 11. Testing Strategy

Backend tests:

- preview session key generation and reuse
- expired/revoked key rejection
- frontend proxy routing
- backend proxy routing
- ensure response payload shape
- frontend-only contract fallback
- full-stack contract startup path
- websocket upgrade proxy behavior

Frontend tests:

- authenticated ensure request
- Project Workspace consumption of preview bundle
- iframe route selection
- degraded state when backend is unavailable

Smoke tests:

- generated frontend-only app preview
- generated full-stack app preview with login flow
- generated app making backend API calls from inside App Viewer

## 12. Risks and Tradeoffs

- The preview manifest adds one more project contract to maintain.
- Apps that hardcode root `/api` without using the injected preview base will not work correctly in preview mode.
- WebSocket proxying for HMR adds implementation complexity.
- Runtime readiness becomes multi-service rather than single-service.

These tradeoffs are acceptable because they buy a clean same-origin full-stack preview model without forcing a platform-wide auth rewrite.

## Decision

Implement App Viewer as a same-origin full-stack preview gateway backed by:

- preview-session keys on runtime sessions
- separate frontend and backend preview proxy routes
- a project-level preview contract
- a full preview launcher that can start both services

This is the smallest design that can honestly satisfy the product requirement of users interacting with a generated SaaS app inside App Viewer.

## Implementation Notes

**Status: Implemented** on branch `feature/app-view-preview`.

All phases of the recommended design were delivered:

- Preview-session key fields (`preview_session_key`, `preview_expires_at`, `frontend_status`, `backend_status`) added to workspace runtime sessions.
- Frontend and backend gateway routes added under `/preview/{preview_session_key}/frontend/` and `/preview/{preview_session_key}/backend/`.
- Workspace runtime ensure response extended with the full preview bundle (`preview_frontend_url`, `preview_backend_url`, `preview_session_key`, `preview_expires_at`).
- `start-preview` launcher introduced in `docker/atoms-sandbox/`, reading `.atoms/preview.json` and starting both services.
- `SandboxRuntimeService` updated to call `start-preview`, inject preview env vars, and perform per-service healthchecks.
- Frontend `workspaceRuntime.ts` updated to use the authenticated API client; `ProjectWorkspace.tsx` updated to consume the preview bundle and drive the App Viewer iframe from `preview_frontend_url`.
- Preview gateway router (`app/backend/routers/preview_gateway.py`) added with session-key validation, expiry checks, and HTTP proxy for both frontend and backend sandbox traffic.
- Full automated test coverage added: `test_workspace_runtime.py`, `test_preview_gateway.py`, `test_preview_contract.py`, `test_sandbox_runtime.py`, `test_agent_runtime.py`, and updated frontend tests.
