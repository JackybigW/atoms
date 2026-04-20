import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useEffect } from "react";
import { WorkspaceProvider, useWorkspace } from "./WorkspaceContext";

function WorkspaceConsumer({ projectId }: { projectId: number | null }) {
  const { previewKey, reloadPreview, previewUrl, setPreviewUrl, setProjectId } = useWorkspace();

  useEffect(() => {
    setProjectId(projectId);
  }, [projectId, setProjectId]);

  return (
    <div>
      <div data-testid="preview-key">{previewKey}</div>
      <div data-testid="preview-url">{previewUrl}</div>
      <button onClick={reloadPreview} type="button">
        Reload preview
      </button>
      <button onClick={() => setPreviewUrl("http://localhost:4173")} type="button">
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
});
