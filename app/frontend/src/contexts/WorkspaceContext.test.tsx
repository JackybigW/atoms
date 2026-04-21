import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useEffect } from "react";
import { WorkspaceProvider, useWorkspace } from "./WorkspaceContext";
import type { WorkspacePreviewBundle } from "@/lib/workspaceRuntime";

function WorkspaceConsumer({ projectId }: { projectId: number | null }) {
  const {
    previewKey,
    reloadPreview,
    preview,
    setPreview,
    setProjectId,
    applyFileSnapshot,
    files,
    applyRealtimeEvent,
    sessionStatus,
    progressItems,
  } = useWorkspace();

  useEffect(() => {
    setProjectId(projectId);
  }, [projectId, setProjectId]);

  return (
    <div>
      <div data-testid="preview-key">{previewKey}</div>
      <div data-testid="preview-url">{preview.preview_frontend_url ?? ""}</div>
      <div data-testid="file-content">{files.find((file) => file.file_path === "src/App.tsx")?.content ?? ""}</div>
      <div data-testid="session-status">{sessionStatus}</div>
      <div data-testid="progress-items">{progressItems.join(" | ")}</div>
      <button onClick={reloadPreview} type="button">
        Reload preview
      </button>
      <button
        onClick={() =>
          applyFileSnapshot({
            path: "src/App.tsx",
            content: "export default function App() { return <main />; }",
          })
        }
        type="button"
      >
        Apply file snapshot
      </button>
      <button
        onClick={() =>
          applyRealtimeEvent({
            type: "session.state",
            status: "running",
          })
        }
        type="button"
      >
        Apply session state
      </button>
      <button
        onClick={() =>
          applyRealtimeEvent({
            type: "progress",
            label: "Editing src/App.tsx",
          })
        }
        type="button"
      >
        Apply progress
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

  it("applies file.snapshot updates without reloading files", () => {
    render(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={1} />
      </WorkspaceProvider>
    );

    act(() => {
      screen.getByRole("button", { name: "Apply file snapshot" }).click();
    });

    expect(screen.getByTestId("file-content")).toHaveTextContent("return <main />");
  });

  it("stores realtime session state and progress items", () => {
    render(
      <WorkspaceProvider>
        <WorkspaceConsumer projectId={1} />
      </WorkspaceProvider>
    );

    act(() => {
      screen.getByRole("button", { name: "Apply session state" }).click();
      screen.getByRole("button", { name: "Apply progress" }).click();
    });

    expect(screen.getByTestId("session-status")).toHaveTextContent("running");
    expect(screen.getByTestId("progress-items")).toHaveTextContent("Editing src/App.tsx");
  });
});
