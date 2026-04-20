import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
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
