import "@testing-library/jest-dom/vitest";
import { useEffect, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ChatPanel from "./ChatPanel";
import { WorkspaceProvider, useWorkspace } from "@/contexts/WorkspaceContext";

const realtimeHarness: {
  onEvent: ((event: Record<string, unknown>) => void) | null;
  sendUserMessage: ReturnType<typeof vi.fn>;
  stopRun: ReturnType<typeof vi.fn>;
  approveDraftPlan: ReturnType<typeof vi.fn>;
} = {
  onEvent: null,
  sendUserMessage: vi.fn(),
  stopRun: vi.fn(),
  approveDraftPlan: vi.fn(),
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
      approveDraftPlan: realtimeHarness.approveDraftPlan,
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
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    realtimeHarness.onEvent = null;
    realtimeHarness.sendUserMessage.mockReset();
    realtimeHarness.stopRun.mockReset();
    realtimeHarness.approveDraftPlan.mockReset();
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

  it("renders a draft plan card with an approve button when draft_plan.pending arrives", async () => {
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
      realtimeHarness.onEvent?.({
        type: "draft_plan.pending",
        request_key: "req-1",
        items: [
          { id: "1", text: "Create homepage" },
          { id: "2", text: "Add billing page" },
        ],
      });
    });

    expect(await screen.findByText("Create homepage")).toBeInTheDocument();
    expect(screen.getByText("Add billing page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("buffers assistant delta text until message_done flushes the remainder", async () => {
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

    vi.useFakeTimers();
    try {
      const assistantText = "QZ streaming response should not render all at once";

      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: assistantText });
      });

      expect(screen.queryByText(assistantText)).not.toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(24);
      });

      expect(
        screen.getByText((_, element) => element?.tagName === "P" && (element.textContent?.startsWith("QZ") ?? false))
      ).toBeInTheDocument();
      expect(screen.queryByText(assistantText)).not.toBeInTheDocument();

      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.message_done", agent: "swe" });
      });

      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(screen.getByText(assistantText)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not render a draft plan card for normal assistant messages", async () => {
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

    await waitFor(() => expect(realtimeHarness.sendUserMessage).toHaveBeenCalled());

    act(() => {
      realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: "Working on it" });
      realtimeHarness.onEvent?.({ type: "assistant.message_done", agent: "swe" });
    });

    await screen.findByText("Working on it");
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });
});
