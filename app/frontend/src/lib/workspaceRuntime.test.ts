import { afterEach, describe, expect, it, vi } from "vitest";
import { buildPreviewUrl, ensureWorkspaceRuntime } from "./workspaceRuntime";

describe("buildPreviewUrl", () => {
  it("keeps backend preview paths untouched", () => {
    expect(buildPreviewUrl("/api/v1/workspace-runtime/projects/42/preview/")).toBe(
      "/api/v1/workspace-runtime/projects/42/preview/"
    );
  });
});

describe("ensureWorkspaceRuntime", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends auth headers and returns preview bundle", async () => {
    globalThis.localStorage.setItem("token", "test-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          project_id: 42,
          status: "running",
          preview_session_key: "preview-session-123",
          preview_frontend_url: "/preview/preview-session-123/frontend/",
          preview_backend_url: "/preview/preview-session-123/backend/",
          frontend_status: "running",
          backend_status: "starting",
        }),
        { status: 200 }
      )
    );

    const result = await ensureWorkspaceRuntime(42);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workspace-runtime/projects/42/ensure",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      })
    );
    expect(result.preview_frontend_url).toBe("/preview/preview-session-123/frontend/");
  });
});
