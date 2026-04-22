import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as WorkspaceContextModule from "@/contexts/WorkspaceContext";
import ProjectWorkspacePage from "./ProjectWorkspace";

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
      projects: {
        get: vi.fn().mockResolvedValue({
          data: {
            id: 42,
            name: "Test Project",
            description: "",
            visibility: "private",
            framework: "react",
            deploy_url: null,
          },
        }),
      },
      project_files: {
        query: vi.fn().mockResolvedValue({ data: { items: [] } }),
      },
      messages: {
        query: vi.fn().mockResolvedValue({ data: { items: [] } }),
      },
    },
  },
}));

describe("task summary strip", () => {
  afterEach(() => {
    cleanup();
  });

  it("shows task strip when task summaries are present", async () => {
    const spy = vi.spyOn(WorkspaceContextModule, "useWorkspace").mockReturnValue({
      setProjectId: vi.fn(),
      previewHtml: "",
      preview: {},
      setPreview: vi.fn(),
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
          <Route path="/workspace/:id" element={<ProjectWorkspacePage />} />
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
        <Route path="/workspace/:id" element={<ProjectWorkspacePage />} />
      </Routes>
    </MemoryRouter>
  );

  // Wait for the project to load, then switch to the App Viewer tab to trigger preview rendering
  const appViewerTab = await screen.findByRole("button", { name: /App Viewer/i });
  fireEvent.click(appViewerTab);

  expect(await screen.findByText(/Backend preview is still starting/i)).toBeInTheDocument();
});
