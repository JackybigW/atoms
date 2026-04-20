import { useState, useRef, useEffect, useCallback } from "react";
import { client } from "@/lib/api";
import { getAPIBaseURL } from "@/lib/config";
import { buildAuthHeaders } from "@/lib/authToken";
import { consumeSseBuffer } from "@/lib/sse";
import { useAuth } from "@/contexts/AuthContext";
import { useWorkspace } from "@/contexts/WorkspaceContext";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Send,
  Paperclip,
  Bot,
  User,
  Loader2,
  Code2,
  Palette,
  BarChart3,
  Briefcase,
  Sparkles,
  StopCircle,
  FileCode,
  CheckCircle2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Message {
  id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  thinking?: string;
  agent?: string;
  model?: string;
  created_at?: string;
  isStreaming?: boolean;
  filesWritten?: string[];
}

const MODELS = [
  { id: "MiniMax-M2.7-highspeed", label: "MiniMax M2.7", icon: "⚡" },
  { id: "deepseek-v3.2", label: "DeepSeek V3", icon: "🔮" },
  { id: "gpt-5-chat", label: "GPT-5", icon: "🧠" },
  { id: "claude-4-5-sonnet", label: "Claude 4.5", icon: "🎭" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5", icon: "💎" },
];

const AGENTS = [
  { id: "engineer", label: "Engineer", icon: <Code2 className="w-3.5 h-3.5" /> },
  { id: "pm", label: "PM", icon: <Briefcase className="w-3.5 h-3.5" /> },
  { id: "designer", label: "Designer", icon: <Palette className="w-3.5 h-3.5" /> },
  { id: "analyst", label: "Analyst", icon: <BarChart3 className="w-3.5 h-3.5" /> },
];

const AGENT_COLORS: Record<string, string> = {
  engineer: "text-blue-400",
  pm: "text-green-400",
  designer: "text-pink-400",
  analyst: "text-amber-400",
};

interface ChatPanelProps {
  mode: "engineer" | "team";
}

function createTraceId(): string {
  if (typeof globalThis !== "undefined" && "crypto" in globalThis && "randomUUID" in globalThis.crypto) {
    return globalThis.crypto.randomUUID().replace(/-/g, "").slice(0, 12);
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

export default function ChatPanel({ mode }: ChatPanelProps) {
  const { user, isAuthenticated } = useAuth();
  const { projectId, addTerminalLog, reloadFiles, setPreview, clearPreview, reloadPreview } = useWorkspace();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [selectedModel, setSelectedModel] = useState("MiniMax-M2.7-highspeed");
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Load messages for project
  useEffect(() => {
    if (!projectId || !isAuthenticated) return;
    const loadMessages = async () => {
      try {
        const res = await client.entities.messages.query({
          query: { project_id: projectId },
          sort: "created_at",
          limit: 100,
        });
        if (res?.data?.items) {
          setMessages(
            res.data.items.map((m: Record<string, unknown>) => ({
              id: m.id as number,
              role: m.role as "user" | "assistant" | "system",
              content: m.content as string,
              agent: m.agent as string,
              model: m.model as string,
              created_at: m.created_at as string,
            }))
          );
        }
      } catch (err) {
        console.error("Failed to load messages:", err);
      }
    };
    loadMessages();
  }, [projectId, isAuthenticated]);

  const saveMessage = async (msg: Omit<Message, "id">) => {
    if (!projectId) return;
    try {
      await client.entities.messages.create({
        data: {
          project_id: projectId,
          role: msg.role,
          content: msg.content,
          model: msg.model || selectedModel,
          agent: msg.agent || "engineer",
          tokens_used: 0,
          created_at: new Date().toISOString(),
        },
      });
    } catch (err) {
      console.error("Failed to save message:", err);
    }
  };

  const appendMessage = useCallback((message: Message) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  const logAgentTrace = useCallback((traceId: string, stage: string, details?: Record<string, unknown>) => {
    const suffix = details ? ` ${JSON.stringify(details)}` : "";
    console.info(`[agent:${traceId}] ${stage}${suffix}`);
    addTerminalLog(`$ [agent:${traceId}] ${stage}`);
  }, [addTerminalLog]);

  const handleAgentEvent = useCallback(
    (payload: Record<string, unknown>) => {
      const type = payload.type as string;
      const traceId = String(payload.trace_id || "unknown");
      logAgentTrace(traceId, `event:${type}`);

      if (type === "session") {
        addTerminalLog(`$ [agent:${traceId}] session started @ ${payload.workspace_root ?? ""}`);
        appendMessage({
          role: "assistant",
          agent: String(payload.agent || "swe"),
          content: `Session started.\n\nWorkspace: \`${payload.workspace_root}\``,
          created_at: new Date().toISOString(),
        });
        return;
      }

      if (type === "assistant") {
        appendMessage({
          role: "assistant",
          agent: String(payload.agent || "swe"),
          content: String(payload.content || ""),
          thinking: payload.thinking ? String(payload.thinking) : undefined,
          model: selectedModel,
          created_at: new Date().toISOString(),
        });
        return;
      }

      if (type === "tool_call") {
        const formattedArgs = (() => {
          try {
            return JSON.stringify(JSON.parse(String(payload.arguments || "{}")), null, 2);
          } catch {
            return String(payload.arguments || "{}");
          }
        })();
        addTerminalLog(`$ [agent:${traceId}] tool call: ${String(payload.tool || "")}`);
        appendMessage({
          role: "system",
          content: `Using \`${payload.tool}\`\n\n\`\`\`json\n${formattedArgs}\n\`\`\``,
          created_at: new Date().toISOString(),
        });
        return;
      }

      if (type === "tool_result") {
        addTerminalLog(`$ [agent:${traceId}] tool result: ${String(payload.tool || "")}`);
        appendMessage({
          role: "system",
          content: `Result from \`${payload.tool}\`\n\n\`\`\`\n${String(payload.content || "")}\n\`\`\``,
          created_at: new Date().toISOString(),
        });
        return;
      }

      if (type === "error") {
        appendMessage({
          role: "assistant",
          agent: "swe",
          content: `Error: ${String(payload.error || "Unknown error")}`,
          created_at: new Date().toISOString(),
        });
        return;
      }

      if (type === "workspace_sync") {
        const count = Array.isArray(payload.changed_files) ? payload.changed_files.length : 0;
        addTerminalLog(`$ [agent:${traceId}] synced ${count} file(s) from sandbox`);
        void reloadFiles();
        return;
      }

      if (type === "preview_ready") {
        const preview_session_key = String(payload.preview_session_key || "");
        const preview_frontend_url = String(payload.preview_frontend_url || "");
        const preview_backend_url = String(payload.preview_backend_url || "");
        const frontend_status = payload.frontend_status ? String(payload.frontend_status) : undefined;
        const backend_status = payload.backend_status ? String(payload.backend_status) : undefined;
        setPreview({ preview_session_key, preview_frontend_url, preview_backend_url, frontend_status, backend_status });
        reloadPreview();
        addTerminalLog(`$ [agent:${traceId}] preview ready: ${preview_frontend_url}`);
        return;
      }

      if (type === "preview_failed") {
        clearPreview();
        reloadPreview();
        addTerminalLog(`$ [agent:${traceId}] preview failed: ${String(payload.reason || "unknown")}`);
        return;
      }
    },
    [addTerminalLog, appendMessage, logAgentTrace, selectedModel, reloadFiles, setPreview, clearPreview, reloadPreview]
  );

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMsg: Message = {
      role: "user",
      content: input.trim(),
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsLoading(true);
    setIsStreaming(true);

    await saveMessage(userMsg);
    const traceId = createTraceId();

    try {
      const controller = new AbortController();
      abortControllerRef.current = controller;
      logAgentTrace(traceId, "send:start", {
        projectId,
        model: selectedModel,
        promptChars: input.trim().length,
      });

      const response = await fetch(`${getAPIBaseURL()}/api/v1/agent/run`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeaders(),
          "X-Trace-Id": traceId,
        },
        body: JSON.stringify({
          prompt: input.trim(),
          agent: "swe",
          model: selectedModel,
          project_id: projectId,
        }),
        signal: controller.signal,
      });

      logAgentTrace(traceId, "send:response", {
        ok: response.ok,
        status: response.status,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Agent request failed with status ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let transcript = "";
      let chunkCount = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        chunkCount += 1;
        if (chunkCount === 1) {
          logAgentTrace(traceId, "stream:first-chunk");
        }
        buffer += decoder.decode(value, { stream: true });
        const parsed = consumeSseBuffer(buffer);
        buffer = parsed.rest;

        for (const parsedEvent of parsed.events) {
          const payload = parsedEvent.payload;
          handleAgentEvent(payload);

          if (payload.type === "assistant" && payload.content) {
            transcript += `${String(payload.content)}\n\n`;
          }
          if (payload.type === "done") {
            if (transcript.trim()) {
              saveMessage({
                role: "assistant",
                content: transcript.trim(),
                agent: "swe",
              model: selectedModel,
            });
          }
            if (parsedEvent.event === "done") {
              logAgentTrace(traceId, "stream:done");
              setIsLoading(false);
              setIsStreaming(false);
            }
          }
          if (payload.type === "error") {
            logAgentTrace(traceId, "stream:error", { error: payload.error });
            setIsLoading(false);
            setIsStreaming(false);
          }
        }
      }

      logAgentTrace(traceId, "stream:complete", { chunks: chunkCount });
      setIsLoading(false);
      setIsStreaming(false);
    } catch (err) {
      logAgentTrace(traceId, "send:exception", {
        message: err instanceof Error ? err.message : "unknown",
      });
      console.error("Chat error:", err);
      appendMessage({
        role: "assistant",
        agent: "swe",
        content:
          err instanceof Error
            ? err.message
            : "Sorry, I encountered an error. Please try again.",
        created_at: new Date().toISOString(),
      });
      setIsLoading(false);
      setIsStreaming(false);
    } finally {
      abortControllerRef.current = null;
    }
  };

  const handleStop = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsLoading(false);
    setIsStreaming(false);
    addTerminalLog("$ agent aborted by user");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height =
        Math.min(textareaRef.current.scrollHeight, 200) + "px";
    }
  }, [input]);

  return (
    <div className="flex flex-col h-full">
      {/* Model Selector */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[#27272A]">
        <Select value={selectedModel} onValueChange={setSelectedModel}>
          <SelectTrigger className="w-[180px] h-8 bg-[#18181B] border-[#27272A] text-xs text-white">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-[#18181B] border-[#27272A]">
            {MODELS.map((m) => (
              <SelectItem
                key={m.id}
                value={m.id}
                className="text-[#FAFAFA] text-xs"
              >
                <span className="mr-1">{m.icon}</span> {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {mode === "team" && (
          <div className="flex items-center gap-1 ml-auto">
            {AGENTS.map((a) => (
              <div
                key={a.id}
                className={`flex items-center gap-1 px-2 py-1 rounded text-[10px] bg-[#18181B] border border-[#27272A] ${AGENT_COLORS[a.id]}`}
              >
                {a.icon}
                <span className="hidden xl:inline">{a.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-[#7C3AED]/20 to-[#A855F7]/20 flex items-center justify-center mb-4">
              <Sparkles className="w-8 h-8 text-[#A855F7]" />
            </div>
            <h3 className="text-lg font-semibold text-white mb-2">
              {mode === "team" ? "Team Mode" : "Engineer Mode"}
            </h3>
            <p className="text-sm text-[#71717A] max-w-sm">
              {mode === "team"
                ? "Multiple AI agents collaborate to build your project. Describe what you want to create."
                : "Alex, your AI engineer, is ready to help. Describe your project or ask a question."}
            </p>
            <p className="text-xs text-[#52525B] mt-3 max-w-xs">
              Code will be automatically written to your project files and previewed in real-time.
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
          >
            <div className="flex-shrink-0">
              {msg.role === "user" ? (
                <div className="w-8 h-8 rounded-lg bg-[#27272A] flex items-center justify-center">
                  {user?.avatar_url ? (
                    <img
                      src={user.avatar_url}
                      alt=""
                      className="w-8 h-8 rounded-lg object-cover"
                    />
                  ) : (
                    <User className="w-4 h-4 text-[#A1A1AA]" />
                  )}
                </div>
              ) : (
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A855F7] flex items-center justify-center">
                  <Bot className="w-4 h-4 text-white" />
                </div>
              )}
            </div>

            <div
              className={`max-w-[85%] ${
                msg.role === "user"
                  ? "bg-[#7C3AED]/20 border border-[#7C3AED]/30 rounded-2xl rounded-tr-md px-4 py-2.5"
                  : "bg-[#18181B] border border-[#27272A] rounded-2xl rounded-tl-md px-4 py-2.5"
              }`}
            >
              {msg.role === "assistant" && msg.agent && (
                <div
                  className={`text-[10px] font-semibold uppercase tracking-wider mb-1 ${
                    AGENT_COLORS[msg.agent] || "text-[#A855F7]"
                  }`}
                >
                  {msg.agent}
                </div>
              )}
              <div className="text-sm text-[#E4E4E7] prose prose-invert prose-sm max-w-none [&_pre]:bg-[#0A0A0C] [&_pre]:border [&_pre]:border-[#27272A] [&_pre]:rounded-lg [&_code]:text-[#A855F7] [&_pre_code]:text-[#E4E4E7]">
                {msg.content ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                ) : null}
                {msg.isStreaming && (
                  <span className="inline-block w-2 h-4 bg-[#A855F7] animate-pulse ml-0.5" />
                )}
              </div>
              {msg.role === "assistant" && msg.thinking ? (
                <ThinkingDisclosure thinking={msg.thinking} />
              ) : null}

              {/* Files written indicator */}
              {msg.filesWritten && msg.filesWritten.length > 0 && (
                <div className="mt-3 pt-3 border-t border-[#27272A]">
                  <div className="flex items-center gap-1.5 text-[10px] text-[#22C55E] font-semibold uppercase tracking-wider mb-1.5">
                    <CheckCircle2 className="w-3 h-3" />
                    Files written to project
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {msg.filesWritten.map((f) => (
                      <span
                        key={f}
                        className="inline-flex items-center gap-1 text-[10px] bg-[#22C55E]/10 text-[#22C55E] px-2 py-0.5 rounded border border-[#22C55E]/20"
                      >
                        <FileCode className="w-2.5 h-2.5" />
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="border-t border-[#27272A] p-3">
        <div className="flex items-end gap-2 bg-[#18181B] border border-[#27272A] rounded-xl px-3 py-2 focus-within:border-[#7C3AED]/50 transition-colors">
          <button className="text-[#71717A] hover:text-[#A1A1AA] p-1 mb-0.5">
            <Paperclip className="w-4 h-4" />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              projectId
                ? "Describe what you want to build..."
                : "Create a project first to start chatting"
            }
            disabled={!projectId || isLoading}
            className="flex-1 bg-transparent text-sm text-white placeholder:text-[#52525B] resize-none outline-none min-h-[24px] max-h-[200px]"
            rows={1}
          />
          {isStreaming ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-red-400 hover:text-red-300 hover:bg-red-400/10 p-1 mb-0.5"
              onClick={handleStop}
            >
              <StopCircle className="w-4 h-4" />
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={handleSend}
              disabled={!input.trim() || isLoading || !projectId}
              className="bg-[#7C3AED] hover:bg-[#6D28D9] text-white p-1 mb-0.5 h-7 w-7"
            >
              {isLoading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Send className="w-3.5 h-3.5" />
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
