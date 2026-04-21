export type AgentRealtimeEvent =
  | { type: "session.state"; status: string; project_id?: number; assistant_role?: string }
  | { type: "assistant.delta"; content: string; agent?: string }
  | { type: "assistant.message_done"; agent?: string }
  | { type: "progress"; label: string }
  | { type: "terminal.log"; content: string }
  | { type: "file.snapshot"; path: string; content: string }
  | { type: "file.changed"; path: string }
  | {
      type: "preview_ready";
      preview_session_key?: string;
      preview_frontend_url?: string;
      preview_backend_url?: string;
      frontend_status?: string;
      backend_status?: string;
    }
  | { type: "preview_failed"; reason?: string; error?: string }
  | { type: "workspace_sync"; changed_files?: string[] }
  | { type: "run.stopped" }
  | { type: "error"; error?: string; message?: string };

type WebSocketLike = {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  send(data: string): void;
  close(): void;
};

type WebSocketConstructor = new (url: string) => WebSocketLike;

export function createAgentRealtimeSession({
  WebSocketImpl = WebSocket as unknown as WebSocketConstructor,
  url,
  onEvent,
}: {
  WebSocketImpl?: WebSocketConstructor;
  url: string;
  onEvent?: (event: AgentRealtimeEvent) => void;
}) {
  const socket = new WebSocketImpl(url);
  const pendingFrames: string[] = [];
  let isOpen = false;

  const flushPendingFrames = () => {
    if (!isOpen) return;
    while (pendingFrames.length > 0) {
      const frame = pendingFrames.shift();
      if (frame) {
        socket.send(frame);
      }
    }
  };

  const sendFrame = (payload: Record<string, unknown>) => {
    const frame = JSON.stringify(payload);
    if (isOpen) {
      socket.send(frame);
      return;
    }
    pendingFrames.push(frame);
  };

  socket.onopen = () => {
    isOpen = true;
    flushPendingFrames();
  };

  socket.onmessage = (message) => {
    onEvent?.(JSON.parse(String(message.data)) as AgentRealtimeEvent);
  };

  socket.onclose = () => {
    isOpen = false;
  };

  return {
    sendUserMessage({ projectId, prompt }: { projectId: number; prompt: string }) {
      sendFrame({ type: "user.message", project_id: projectId, prompt });
    },
    stopRun() {
      sendFrame({ type: "run.stop" });
    },
    close() {
      socket.close();
    },
  };
}
