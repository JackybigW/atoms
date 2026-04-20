import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useEffect } from "react";
import { WorkspaceProvider, useWorkspace } from "./WorkspaceContext";
import type { WorkspacePreviewBundle } from "@/lib/workspaceRuntime";

function WorkspaceConsumer({ projectId }: { projectId: number | null }) {
  const { previewKey, reloadPreview, preview, setPreview, setProjectId } = useWorkspace();

  useEffect(() => {
    setProjectId(projectId);
  }, [projectId, setProjectId]);

  return (
    <div>
      <div data-testid="preview-key">{previewKey}</div>
      <div data-testid="preview-url">{preview.preview_frontend_url ?? ""}</div>
      <button onClick={reloadPreview} type="button">
        Reload preview
      </button>
      <button
        onClick={() =>
          setPreview({
            preview_session_key: "sess-1",
            preview_frontend_url: "http://localhost:4173",
            preview_backend_url: "http://localhost:4174",
            frontend_status: "running",
            backend_status: "running",
          } satisfies Partial<WorkspacePreviewBundle>)
        }
        type="button"
      >
        Set preview URL
      </button>
    </div>
  );
}

describe("WorkspaceContext", () => {
  afterEach(() => {
    cleanup();
  });

  it("reloadPreview increments previewKey on each call", () => {
    render(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={1} />
      </WorkspaceProvider>
    );

    expect(screen.getByTestId("preview-key")).toHaveTextContent("0");

    act(() => {
      screen.getByRole("button", { name: "Reload preview" }).click();
    });

    expect(screen.getByTestId("preview-key")).toHaveTextContent("1");

    act(() => {
      screen.getByRole("button", { name: "Reload preview" }).click();
    });

    expect(screen.getByTestId("preview-key")).toHaveTextContent("2");
  });

  it('changing projectId resets previewUrl to ""', () => {
    const { rerender } = render(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={1} />
      </WorkspaceProvider>
    );

    act(() => {
      screen.getByRole("button", { name: "Set preview URL" }).click();
    });

    expect(screen.getByTestId("preview-url")).toHaveTextContent("http://localhost:4173");

    rerender(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={2} />
      </WorkspaceProvider>
    );

    expect(screen.getByTestId("preview-url")).toHaveTextContent("");
  });

  it("setPreview stores the full preview bundle", () => {
    render(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={1} />
      </WorkspaceProvider>
    );

    act(() => {
      screen.getByRole("button", { name: "Set preview URL" }).click();
    });

    expect(screen.getByTestId("preview-url")).toHaveTextContent("http://localhost:4173");
  });
});
