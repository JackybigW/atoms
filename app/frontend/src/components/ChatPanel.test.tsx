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

const messageCreateMock = vi.hoisted(() => vi.fn());
const createAgentRealtimeSessionMock = vi.hoisted(() => vi.fn());

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
        create: messageCreateMock,
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
  createAgentRealtimeSession: createAgentRealtimeSessionMock.mockImplementation(({ onEvent }) => {
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
    createAgentRealtimeSessionMock.mockClear();
    messageCreateMock.mockReset();
    messageCreateMock.mockResolvedValue({});
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

  it("keeps the progress feed collapsed by default and reveals it when expanded", async () => {
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
      realtimeHarness.onEvent?.({ type: "progress", label: "Editing src/App.tsx" });
      realtimeHarness.onEvent?.({ type: "terminal.log", content: "$ pnpm test" });
    });

    expect(screen.queryByText("Editing src/App.tsx")).not.toBeInTheDocument();
    expect(screen.queryByText("$ pnpm test")).not.toBeInTheDocument();
    const progressToggle = screen.getByRole("button", { name: /progress/i });
    expect(progressToggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(progressToggle);

    expect(screen.getByText("Editing src/App.tsx")).toBeInTheDocument();
    expect(screen.queryByText("$ pnpm test")).not.toBeInTheDocument();
  });

  it("aborts a bootstrap ticket fetch cleanly when Stop is clicked first", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_, options?: { signal?: AbortSignal }) => {
        return new Promise((_, reject) => {
          options?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        });
      })
    );

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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /stop agent/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /stop agent/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /stop agent/i })).not.toBeInTheDocument();
      expect(realtimeHarness.sendUserMessage).not.toHaveBeenCalled();
      expect(createAgentRealtimeSessionMock).not.toHaveBeenCalled();
      expect(screen.queryByText(/the operation was aborted/i)).not.toBeInTheDocument();
    });
  });

  it("swaps the send button for a Stop agent control while a run is active", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    expect(screen.queryByRole("button", { name: /send message/i })).not.toBeInTheDocument();
    const stopButton = screen.getByRole("button", { name: /stop agent/i });

    fireEvent.click(stopButton);

    expect(realtimeHarness.stopRun).toHaveBeenCalledTimes(1);
  });

  it("does not send a second prompt when Enter is pressed after stopping a run", async () => {
    render(
      <WorkspaceProvider>
        <WorkspaceHarness>
          <ChatPanel mode="engineer" />
        </WorkspaceHarness>
      </WorkspaceProvider>
    );

    const textarea = screen.getByPlaceholderText(/Describe what you want to build/i);

    fireEvent.change(textarea, {
      target: { value: "build auth" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalledTimes(1);
    });

    fireEvent.click(screen.getByRole("button", { name: /stop agent/i }));

    fireEvent.change(textarea, {
      target: { value: "build payments" },
    });
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", charCode: 13 });

    expect(realtimeHarness.sendUserMessage).toHaveBeenCalledTimes(1);
  });

  it("renders the draft plan card incrementally and only shows Approve after ready", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({
        type: "draft_plan.start",
        request_key: "req-1",
      });
    });

    expect(screen.getByText("Draft Plan")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText("Create homepage")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();

    act(() => {
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-1",
        item: { id: "1", text: "Create homepage" },
      });
    });

    expect(screen.getByText("Create homepage")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();

    act(() => {
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-1",
        item: { id: "2", text: "Add billing page" },
      });
    });

    expect(screen.getByText("Add billing page")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();

    act(() => {
      realtimeHarness.onEvent?.({
        type: "draft_plan.ready",
        request_key: "req-1",
      });
    });

    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("clears a ready draft plan card when Stop is clicked", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.start", request_key: "req-stop" });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-stop",
        item: { id: "1", text: "Create homepage" },
      });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-stop",
        item: { id: "2", text: "Add billing page" },
      });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-stop",
        item: { id: "3", text: "Ship deploy flow" },
      });
      realtimeHarness.onEvent?.({ type: "draft_plan.ready", request_key: "req-stop" });
    });

    expect(screen.getByText("Draft Plan")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop agent/i }));

    expect(realtimeHarness.stopRun).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Draft Plan")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("calls approveDraftPlan with the current draft plan request_key", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.start", request_key: "req-7" });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-7",
        item: { id: "1", text: "Create homepage" },
      });
      realtimeHarness.onEvent?.({ type: "draft_plan.ready", request_key: "req-7" });
    });

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(realtimeHarness.approveDraftPlan).toHaveBeenCalledWith({ requestKey: "req-7" });
  });

  it("clears the pending draft plan card when draft_plan.approved arrives", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.start", request_key: "req-1" });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-1",
        item: { id: "1", text: "Create homepage" },
      });
      realtimeHarness.onEvent?.({ type: "draft_plan.ready", request_key: "req-1" });
    });

    expect(screen.getByText("Draft Plan")).toBeInTheDocument();
    expect(screen.getByText("Create homepage")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.approved", request_key: "req-1" });
    });

    expect(screen.queryByText("Draft Plan")).not.toBeInTheDocument();
    expect(screen.queryByText("Create homepage")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("does not clear the current draft plan card when draft_plan.approved has a different request_key", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.start", request_key: "req-1" });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-1",
        item: { id: "1", text: "Create homepage" },
      });
      realtimeHarness.onEvent?.({ type: "draft_plan.ready", request_key: "req-1" });
      realtimeHarness.onEvent?.({ type: "draft_plan.approved", request_key: "req-2" });
    });

    expect(screen.getByText("Draft Plan")).toBeInTheDocument();
    expect(screen.getByText("Create homepage")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
  });

  it("ignores late draft plan events after Stop is requested", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: /stop agent/i }));

    act(() => {
      realtimeHarness.onEvent?.({ type: "draft_plan.start", request_key: "req-1" });
      realtimeHarness.onEvent?.({
        type: "draft_plan.item",
        request_key: "req-1",
        item: { id: "1", text: "Create homepage" },
      });
      realtimeHarness.onEvent?.({ type: "draft_plan.ready", request_key: "req-1" });
    });

    expect(screen.queryByText("Draft Plan")).not.toBeInTheDocument();
    expect(screen.queryByText("Create homepage")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
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

  it("stops revealing buffered assistant text immediately when Stop is clicked", async () => {
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
      const assistantText = "QZ stop should freeze this buffered stream";

      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: assistantText });
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(24);
      });

      const visibleParagraph = screen.getByText(
        (_, element) => element?.tagName === "P" && (element.textContent?.startsWith("QZ") ?? false)
      );
      const renderedBeforeStop = visibleParagraph.textContent;

      const activeButtons = screen.getAllByRole("button");
      fireEvent.click(activeButtons[activeButtons.length - 1]);

      expect(realtimeHarness.stopRun).toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(120);
      });

      expect(screen.getByText((_, element) => element?.tagName === "P" && (element.textContent?.startsWith("QZ") ?? false)).textContent).toBe(renderedBeforeStop);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(["completed", "failed"] as const)(
    "ignores late assistant events after Stop and clears the temp bubble when session.state=%s arrives",
    async (status) => {
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
        act(() => {
          realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: "Stopped reply should" });
        });

        await act(async () => {
          await vi.advanceTimersByTimeAsync(24);
        });

        const stopButton = screen.getByRole("button", { name: /stop agent/i });
        const visibleParagraph = screen.getByText(
          (_, element) => element?.tagName === "P" && (element.textContent?.includes("Stop") ?? false)
        );
        const renderedBeforeStop = visibleParagraph.textContent;

        fireEvent.click(stopButton);

        act(() => {
          realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: " not persist" });
          realtimeHarness.onEvent?.({ type: "assistant.message_done", agent: "swe" });
        });

        await act(async () => {
          await vi.runAllTimersAsync();
        });

        expect(
          screen.getByText(
            (_, element) => element?.tagName === "P" && (element.textContent?.includes("Stop") ?? false)
          ).textContent
        ).toBe(renderedBeforeStop);
        expect(
          messageCreateMock.mock.calls.filter(([payload]) => payload.data.role === "assistant")
        ).toHaveLength(0);

        act(() => {
          realtimeHarness.onEvent?.({ type: "session.state", status });
        });

        await act(async () => {
          await Promise.resolve();
        });

        expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /stop agent/i })).not.toBeInTheDocument();

        expect(
          screen.queryByText(
            (_, element) => element?.tagName === "P" && (element.textContent?.includes("Stop") ?? false)
          )
        ).not.toBeInTheDocument();
        expect(
          messageCreateMock.mock.calls.filter(([payload]) => payload.data.role === "assistant")
        ).toHaveLength(0);
      } finally {
        vi.useRealTimers();
      }
    }
  );

  it("commits the final assistant reply when session.state arrives before message_done on a normal run", async () => {
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
      const replyText = "Session state first should still commit";

      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: replyText });
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(24);
      });

      const partialBubble = screen.getByText(
        (_, element) => element?.tagName === "P" && (element.textContent?.includes("Session") ?? false)
      );
      const renderedBeforeSessionState = partialBubble.textContent;

      act(() => {
        realtimeHarness.onEvent?.({ type: "session.state", status: "completed" });
      });

      expect(
        screen.getByText(
          (_, element) => element?.tagName === "P" && (element.textContent?.includes("Session") ?? false)
        ).textContent
      ).toBe(renderedBeforeSessionState);
      expect(screen.getByRole("button", { name: /stop agent/i })).toBeInTheDocument();

      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.message_done", agent: "swe" });
      });

      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(screen.getByText(replyText)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
      expect(messageCreateMock.mock.calls.filter(([payload]) => payload.data.role === "assistant")).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps stale session events out of a new run after Stop and immediate resend", async () => {
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
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalledTimes(1);
    });

    const oldSessionEvent = realtimeHarness.onEvent;

    fireEvent.click(screen.getByRole("button", { name: /stop agent/i }));

    act(() => {
      oldSessionEvent?.({ type: "run.stopped" });
    });

    fireEvent.change(screen.getByPlaceholderText(/Describe what you want to build/i), {
      target: { value: "build payments" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(realtimeHarness.sendUserMessage).toHaveBeenCalledTimes(2);
    });

    const newSessionEvent = realtimeHarness.onEvent;
    expect(newSessionEvent).not.toBe(oldSessionEvent);

    vi.useFakeTimers();
    try {
      act(() => {
        oldSessionEvent?.({ type: "assistant.delta", agent: "swe", content: "OLD STALE" });
        oldSessionEvent?.({ type: "assistant.message_done", agent: "swe" });
      });

      act(() => {
        newSessionEvent?.({ type: "assistant.delta", agent: "swe", content: "NEW CLEAN" });
        newSessionEvent?.({ type: "assistant.message_done", agent: "swe" });
      });

      await act(async () => {
        await vi.runAllTimersAsync();
      });

      expect(screen.getByText("NEW CLEAN")).toBeInTheDocument();
      expect(screen.queryByText("OLD STALE")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the cursor visible while the stream is open even after rendered text catches up", async () => {
    const { container } = render(
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
      act(() => {
        realtimeHarness.onEvent?.({ type: "assistant.delta", agent: "swe", content: "Short" });
      });

      for (let i = 0; i < 3; i += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(24);
        });
      }

      expect(screen.getByText("Short")).toBeInTheDocument();
      expect(container.querySelector("span.animate-pulse")).toBeInTheDocument();
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
