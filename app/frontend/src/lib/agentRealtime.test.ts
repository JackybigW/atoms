import { describe, expect, it } from "vitest";

import { createAgentRealtimeSession } from "./agentRealtime";

describe("createAgentRealtimeSession", () => {
  it("sends user.message and stop frames over websocket", async () => {
    const sent: string[] = [];

    class FakeSocket {
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;

      constructor() {
        queueMicrotask(() => this.onopen?.());
      }

      send(value: string) {
        sent.push(value);
      }
    }

    const session = createAgentRealtimeSession({
      WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
      url: "ws://example.test",
    });

    await Promise.resolve();

    session.sendUserMessage({ projectId: 1, prompt: "build auth" });
    session.stopRun();

    expect(sent).toEqual([
      JSON.stringify({ type: "user.message", project_id: 1, prompt: "build auth" }),
      JSON.stringify({ type: "run.stop" }),
    ]);
  });
});
