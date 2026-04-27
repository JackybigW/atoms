import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as WorkspaceContextModule from "@/contexts/WorkspaceContext";
import { ensureWorkspaceRuntime } from "@/lib/workspaceRuntime";
import ProjectWorkspacePage, { PreviewSurface } from "./ProjectWorkspace";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", credits: 0 }, isAuthenticated: true }),
}));

vi.mock("@/lib/workspaceRuntime", () => ({
  ensureWorkspaceRuntime: vi.fn().mockResolvedValue({
    project_id: 42,
    status: "running",
    preview_session_key: "preview-session-123",
    preview_frontend_url: "/preview/preview-session-123/frontend/",
    preview_backend_url: "/preview/preview-session-123/backend/",
    frontend_status: "running",
    backend_status: "starting",
  }),
}));

// Mock heavy components that have complex deps
vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel">ChatPanel</div>,
}));

vi.mock("@/components/CodeEditor", () => ({
  default: () => <div data-testid="code-editor">CodeEditor</div>,
}));

// Mock the api client
vi.mock("@/lib/api", () => ({
  client: {
    entities: {
      project_files: {
        query: vi.fn().mockResolvedValue({ data: { items: [] } }),
      },
      messages: {
        query: vi.fn().mockResolvedValue({ data: { items: [] } }),
      },
    },
  },
}));

// Mock fetch for the /by-number/ endpoint
vi.stubGlobal(
  "fetch",
  vi.fn().mockResolvedValue({
    ok: true,
    json: () =>
      Promise.resolve({
        id: 42,
        project_number: 42,
        name: "Test Project",
        description: "",
        visibility: "private",
        framework: "react",
        deploy_url: null,
      }),
  })
);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("task summary strip", () => {
  it("shows task strip when task summaries are present", async () => {
    const spy = vi.spyOn(WorkspaceContextModule, "useWorkspace").mockReturnValue({
      setProjectId: vi.fn(),
      previewHtml: "",
      previewFailure: null,
      preview: {},
      setPreview: vi.fn(),
      setPreviewFailure: vi.fn(),
      clearPreview: vi.fn(),
      terminalLogs: [],
      previewKey: 0,
      reloadPreview: vi.fn(),
      taskSummaries: [{ id: 1, subject: "Create homepage", status: "in_progress", blocked_by: [] }],
      setTaskSummaries: vi.fn(),
      files: [],
      setFiles: vi.fn(),
      writeFile: vi.fn(),
      writeMultipleFiles: vi.fn(),
      projectId: 42,
      previewUrl: "",
      setPreviewUrl: vi.fn(),
      addTerminalLog: vi.fn(),
      sessionStatus: "",
      progressItems: [],
      applyFileSnapshot: vi.fn(),
      applyRealtimeEvent: vi.fn(),
      fileVersion: 0,
      reloadFiles: vi.fn(),
    } as ReturnType<typeof WorkspaceContextModule.useWorkspace>);

    render(
      <MemoryRouter initialEntries={["/workspace/42"]}>
        <Routes>
          <Route path="/workspace/:projectNumber" element={<ProjectWorkspacePage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByTestId("task-summary-strip")).toBeInTheDocument();
    expect(screen.getByText("Create homepage")).toBeInTheDocument();

    spy.mockRestore();
  });
});

it("shows degraded banner when backend is not yet running", async () => {
  render(
    <MemoryRouter initialEntries={["/workspace/42"]}>
      <Routes>
        <Route path="/workspace/:projectNumber" element={<ProjectWorkspacePage />} />
      </Routes>
    </MemoryRouter>
  );

  // Wait for the project to load, then switch to the App Viewer tab to trigger preview rendering
  const appViewerTab = await screen.findByRole("button", { name: /App Viewer/i });
  fireEvent.click(appViewerTab);

  expect(await screen.findByText(/Backend preview is still starting/i)).toBeInTheDocument();
});

it("does not show degraded banner for frontend-only preview", async () => {
  vi.mocked(ensureWorkspaceRuntime).mockResolvedValueOnce({
    project_id: 42,
    status: "running",
    preview_session_key: "preview-session-123",
    preview_frontend_url: "/preview/preview-session-123/frontend/",
    preview_backend_url: "/preview/preview-session-123/backend/",
    frontend_status: "running",
    backend_status: "not_configured",
  });

  render(
    <MemoryRouter initialEntries={["/workspace/42"]}>
      <Routes>
        <Route path="/workspace/:projectNumber" element={<ProjectWorkspacePage />} />
      </Routes>
    </MemoryRouter>
  );

  const appViewerTab = await screen.findByRole("button", { name: /App Viewer/i });
  fireEvent.click(appViewerTab);

  expect(screen.queryByText(/Backend preview is still starting/i)).not.toBeInTheDocument();
});

it("shows preview failure state instead of falling back to srcDoc", async () => {
  render(
    <div className="h-[400px] w-[600px]">
      <PreviewSurface
        frontendUrl=""
        previewHtml="<html><body><main>Fallback preview</main></body></html>"
        previewFailure={{ reason: "timeout", error: "preview bootstrap failed" }}
        previewKey={0}
      />
    </div>
  );

  expect(await screen.findByText(/Preview failed to start/i)).toBeInTheDocument();
  expect(screen.getByText("preview bootstrap failed")).toBeInTheDocument();
  expect(screen.queryByTitle("App Preview")).not.toBeInTheDocument();
});
