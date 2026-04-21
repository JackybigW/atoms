import "@testing-library/jest-dom/vitest";
import { useEffect, type ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatPanel from "./ChatPanel";
import { WorkspaceProvider, useWorkspace } from "@/contexts/WorkspaceContext";

const realtimeHarness: {
  onEvent: ((event: Record<string, unknown>) => void) | null;
  sendUserMessage: ReturnType<typeof vi.fn>;
  stopRun: ReturnType<typeof vi.fn>;
} = {
  onEvent: null,
  sendUserMessage: vi.fn(),
  stopRun: vi.fn(),
};

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "user-1", avatar_url: null },
    isAuthenticated: true,
  }),
}));

vi.mock("@/lib/api", () => ({
  client: {
    entities: {
      messages: {
        query: vi.fn().mockResolvedValue({ data: { items: [] } }),
        create: vi.fn().mockResolvedValue({}),
      },
    },
  },
}));

vi.mock("@/lib/config", () => ({
  getAPIBaseURL: () => "http://127.0.0.1:8000",
}));

vi.mock("@/lib/authToken", () => ({
  buildAuthHeaders: () => ({ Authorization: "Bearer test-token" }),
}));

vi.mock("@/lib/agentRealtime", () => ({
  createAgentRealtimeSession: vi.fn(({ onEvent }) => {
    realtimeHarness.onEvent = onEvent;
    return {
      sendUserMessage: realtimeHarness.sendUserMessage,
      stopRun: realtimeHarness.stopRun,
      close: vi.fn(),
    };
  }),
}));

function WorkspaceHarness({ children }: { children: ReactNode }) {
  const { setProjectId } = useWorkspace();

  useEffect(() => {
    setProjectId(42);
  }, [setProjectId]);

  return <>{children}</>;
}

describe("ChatPanel", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    realtimeHarness.onEvent = null;
    realtimeHarness.sendUserMessage.mockReset();
    realtimeHarness.stopRun.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ticket: "ticket-123" }),
        body: {
          getReader: () => ({
            read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
          }),
        },
      })
    );
  });

  it("shows engineer progress without rendering raw terminal logs in chat", async () => {
    render(
      <WorkspaceProvider>
        <WorkspaceHarness>
          <ChatPanel mode="engineer" />
        </WorkspaceHarness>
      </WorkspaceProvider>
    );

    fireEvent.change(screen.getByPlaceholderText(/Describe what you want to build/i), {
      target: { value: "build auth" },
    });
    const buttons = screen.getAllByRole("button");
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: "Updating auth flow" });
      realtimeHarness.onEvent?.({ type: "progress", label: "Editing src/App.tsx" });
      realtimeHarness.onEvent?.({ type: "terminal.log", content: "$ pnpm test" });
      realtimeHarness.onEvent?.({ type: "assistant.message_done", agent: "swe" });
    });

    expect(await screen.findByText("Updating auth flow")).toBeInTheDocument();
    expect(screen.getByText("Editing src/App.tsx")).toBeInTheDocument();
    expect(screen.queryByText("$ pnpm test")).not.toBeInTheDocument();
  });
});
