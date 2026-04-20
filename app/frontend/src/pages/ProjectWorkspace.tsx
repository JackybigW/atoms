import { useState, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { client } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { WorkspaceProvider, useWorkspace } from "@/contexts/WorkspaceContext";
import { ensureWorkspaceRuntime } from "@/lib/workspaceRuntime";
import ChatPanel from "@/components/ChatPanel";
import CodeEditor from "@/components/CodeEditor";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  MessageSquare,
  FileCode,
  Eye,
  Terminal as TerminalIcon,
  PanelLeftClose,
  PanelLeftOpen,
  ArrowLeft,
  Share2,
  Rocket,
  Monitor,
  Tablet,
  Smartphone,
  Loader2,
  Copy,
  Check,
  Globe,
  Lock,
  EyeIcon,
  RefreshCw,
  Coins,
} from "lucide-react";
import { toast } from "sonner";

type TabType = "chat" | "editor" | "preview" | "terminal";

interface Project {
  id: number;
  name: string;
  description?: string;
  visibility: string;
  framework: string;
  deploy_url?: string;
}

/** Inner workspace that uses the context */
function WorkspaceInner() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, isAuthenticated } = useAuth();
  const {
    setProjectId,
    previewHtml,
    previewUrl,
    setPreviewUrl,
    terminalLogs,
    fileVersion,
  } = useWorkspace();
  const projectId = id ? parseInt(id) : null;

  const [project, setProject] = useState<Project | null>(null);
  const [leftTab, setLeftTab] = useState<TabType>("chat");
  const [rightTab, setRightTab] = useState<TabType>("editor");
  const [showLeft, setShowLeft] = useState(true);
  const [mode, setMode] = useState<"engineer" | "team">("engineer");
  const [previewDevice, setPreviewDevice] = useState<
    "desktop" | "tablet" | "mobile"
  >("desktop");
  const [showShare, setShowShare] = useState(false);
  const [showPublish, setShowPublish] = useState(false);
  const [publishUrl, setPublishUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [previewKey, setPreviewKey] = useState(0);

  // Sync projectId to context
  useEffect(() => {
    setProjectId(projectId);
  }, [projectId, setProjectId]);

  // Ensure workspace runtime is ready
  useEffect(() => {
    if (!projectId) return;
    ensureWorkspaceRuntime(projectId)
      .then((status) => {
        if (status.preview_url) setPreviewUrl(status.preview_url);
      })
      .catch((err) => {
        console.error("Failed to ensure workspace runtime:", err);
      });
  }, [projectId, setPreviewUrl]);

  // Refresh preview when files change
  useEffect(() => {
    setPreviewKey((k) => k + 1);
  }, [fileVersion]);

  // Load project
  useEffect(() => {
    if (!projectId || !isAuthenticated) return;
    const loadProject = async () => {
      try {
        const res = await client.entities.projects.get({
          id: String(projectId),
        });
        if (res?.data) {
          setProject({
            id: res.data.id as number,
            name: res.data.name as string,
            description: res.data.description as string,
            visibility: (res.data.visibility as string) || "private",
            framework: (res.data.framework as string) || "react",
            deploy_url: res.data.deploy_url as string,
          });
          setPublishUrl(
            (res.data.deploy_url as string) ||
              `atoms.dev/${(res.data.name as string)
                ?.toLowerCase()
                .replace(/\s+/g, "-")}`
          );
        }
      } catch (err) {
        console.error("Failed to load project:", err);
        toast.error("Project not found");
        navigate("/dashboard");
      }
    };
    loadProject();
  }, [projectId, isAuthenticated, navigate]);

  const handlePublish = async () => {
    if (!projectId) return;
    setIsPublishing(true);
    setTimeout(async () => {
      try {
        await client.entities.projects.update({
          id: String(projectId),
          data: {
            deploy_url: `https://${publishUrl}`,
            updated_at: new Date().toISOString(),
          },
        });
        setProject((prev) =>
          prev ? { ...prev, deploy_url: `https://${publishUrl}` } : null
        );
        toast.success("Project published!");
      } catch {
        toast.error("Failed to publish");
      }
      setIsPublishing(false);
      setShowPublish(false);
    }, 2000);
  };

  const handleCopyLink = () => {
    navigator.clipboard.writeText(`https://${publishUrl}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
    toast.success("Link copied!");
  };

  const leftTabs: { id: TabType; label: string; icon: React.ReactNode }[] = [
    {
      id: "chat",
      label: "Chat",
      icon: <MessageSquare className="w-4 h-4" />,
    },
    { id: "editor", label: "Editor", icon: <FileCode className="w-4 h-4" /> },
  ];

  const rightTabs: { id: TabType; label: string; icon: React.ReactNode }[] = [
    { id: "editor", label: "Editor", icon: <FileCode className="w-4 h-4" /> },
    { id: "preview", label: "App Viewer", icon: <Eye className="w-4 h-4" /> },
    {
      id: "terminal",
      label: "Terminal",
      icon: <TerminalIcon className="w-4 h-4" />,
    },
  ];

  const previewWidths = {
    desktop: "100%",
    tablet: "768px",
    mobile: "375px",
  };

  const renderPanel = (tab: TabType) => {
    switch (tab) {
      case "chat":
        return <ChatPanel mode={mode} />;
      case "editor":
        return <CodeEditor />;
      case "preview":
        return (
          <div className="h-full flex flex-col">
            <div className="flex items-center gap-2 px-3 py-2 border-b border-[#27272A]">
              <div className="flex items-center bg-[#18181B] border border-[#27272A] rounded-lg p-0.5">
                <button
                  onClick={() => setPreviewDevice("desktop")}
                  className={`p-1.5 rounded ${
                    previewDevice === "desktop"
                      ? "bg-[#27272A] text-white"
                      : "text-[#71717A]"
                  }`}
                >
                  <Monitor className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setPreviewDevice("tablet")}
                  className={`p-1.5 rounded ${
                    previewDevice === "tablet"
                      ? "bg-[#27272A] text-white"
                      : "text-[#71717A]"
                  }`}
                >
                  <Tablet className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setPreviewDevice("mobile")}
                  className={`p-1.5 rounded ${
                    previewDevice === "mobile"
                      ? "bg-[#27272A] text-white"
                      : "text-[#71717A]"
                  }`}
                >
                  <Smartphone className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex-1 flex items-center bg-[#18181B] border border-[#27272A] rounded-lg px-3 py-1.5">
                <Globe className="w-3.5 h-3.5 text-[#52525B] mr-2" />
                <span className="text-xs text-[#71717A]">{previewUrl || "localhost:5173"}</span>
              </div>
              <button
                className="text-[#71717A] hover:text-white p-1.5"
                onClick={() => setPreviewKey((k) => k + 1)}
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex-1 flex items-start justify-center bg-[#0A0A0C] p-4 overflow-auto">
              <div
                className="bg-white rounded-lg overflow-hidden shadow-2xl transition-all duration-300"
                style={{
                  width: previewWidths[previewDevice],
                  maxWidth: "100%",
                  height: "100%",
                }}
              >
                {previewUrl || previewHtml ? (
                  <iframe
                    key={previewKey}
                    src={previewUrl || undefined}
                    srcDoc={!previewUrl ? previewHtml : undefined}
                    title="App Preview"
                    className="w-full h-full border-0"
                    sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-gray-50 to-gray-100">
                    <div className="text-center p-8">
                      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-[#7C3AED] to-[#A855F7] flex items-center justify-center mx-auto mb-4">
                        <Eye className="w-8 h-8 text-white" />
                      </div>
                      <h3 className="text-lg font-semibold text-gray-900 mb-2">
                        Live Preview
                      </h3>
                      <p className="text-sm text-gray-500 max-w-xs">
                        Your app preview will appear here as you build. Start
                        chatting with the AI to generate code.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      case "terminal":
        return (
          <div className="h-full bg-[#0A0A0C] font-mono text-xs p-4 overflow-auto">
            {terminalLogs.map((line, i) => (
              <div
                key={i}
                className={`leading-6 ${
                  line.startsWith("$")
                    ? "text-[#22C55E]"
                    : line.startsWith("✓") || line.startsWith("  ✓")
                    ? "text-[#22C55E]"
                    : line.startsWith("  ➜")
                    ? "text-[#3B82F6]"
                    : line.startsWith("  ✗") || line.startsWith("Error")
                    ? "text-red-400"
                    : "text-[#A1A1AA]"
                }`}
              >
                {line || "\u00A0"}
              </div>
            ))}
            <div className="flex items-center text-[#22C55E] mt-1">
              <span>$</span>
              <span className="w-2 h-4 bg-[#22C55E] animate-pulse ml-1" />
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  if (!project && projectId) {
    return (
      <div className="h-screen bg-[#09090B] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-[#A855F7] animate-spin" />
      </div>
    );
  }

  return (
    <div className="h-screen bg-[#09090B] text-white flex flex-col">
      {/* Workspace Header */}
      <div className="h-12 border-b border-[#27272A] flex items-center px-4 flex-shrink-0">
        <Link
          to="/dashboard"
          className="text-[#71717A] hover:text-white mr-3 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </Link>

        <button
          onClick={() => setShowLeft(!showLeft)}
          className="text-[#71717A] hover:text-white mr-3"
        >
          {showLeft ? (
            <PanelLeftClose className="w-4 h-4" />
          ) : (
            <PanelLeftOpen className="w-4 h-4" />
          )}
        </button>

        <div className="flex items-center gap-2 bg-[#18181B] rounded-lg px-3 py-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-gradient-to-br from-[#7C3AED] to-[#A855F7]" />
          <span className="text-sm text-white font-medium">
            {project?.name || "Untitled"}
          </span>
        </div>

        <div className="ml-4 flex items-center bg-[#18181B] border border-[#27272A] rounded-lg p-0.5">
          <button
            onClick={() => setMode("engineer")}
            className={`px-3 py-1 rounded text-xs transition-all ${
              mode === "engineer"
                ? "bg-[#27272A] text-white"
                : "text-[#71717A] hover:text-[#A1A1AA]"
            }`}
          >
            Engineer
          </button>
          <button
            onClick={() => setMode("team")}
            className={`px-3 py-1 rounded text-xs transition-all ${
              mode === "team"
                ? "bg-[#27272A] text-white"
                : "text-[#71717A] hover:text-[#A1A1AA]"
            }`}
          >
            Team
          </button>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {user && (
            <div className="flex items-center gap-1.5 bg-[#18181B] border border-[#27272A] rounded-full px-3 py-1">
              <Coins className="w-3.5 h-3.5 text-amber-400" />
              <span className="text-xs font-medium text-white">
                {user.credits}
              </span>
            </div>
          )}

          <Button
            size="sm"
            variant="ghost"
            className="text-[#A1A1AA] hover:text-white hover:bg-[#18181B] h-8 text-xs"
            onClick={() => setShowShare(true)}
          >
            <Share2 className="w-3.5 h-3.5 mr-1.5" />
            Share
          </Button>

          <Button
            size="sm"
            className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0 h-8 text-xs"
            onClick={() => setShowPublish(true)}
          >
            <Rocket className="w-3.5 h-3.5 mr-1.5" />
            Publish
          </Button>

          <div className="flex items-center gap-1.5 ml-2">
            <div className="w-2 h-2 rounded-full bg-[#22C55E] animate-pulse" />
            <span className="text-[10px] text-[#52525B]">Ready</span>
          </div>
        </div>
      </div>

      {/* Main Workspace Area */}
      <div className="flex-1 flex overflow-hidden">
        {showLeft && (
          <div className="w-1/2 border-r border-[#27272A] flex flex-col">
            <div className="h-10 border-b border-[#27272A] flex items-center px-2 gap-1">
              {leftTabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setLeftTab(tab.id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition-all ${
                    leftTab === tab.id
                      ? "bg-[#27272A] text-white"
                      : "text-[#71717A] hover:text-[#A1A1AA]"
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="flex-1 overflow-hidden">
              {renderPanel(leftTab)}
            </div>
          </div>
        )}

        <div className="flex-1 flex flex-col">
          <div className="h-10 border-b border-[#27272A] flex items-center px-2 gap-1">
            {rightTabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setRightTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs transition-all ${
                  rightTab === tab.id
                    ? "bg-[#27272A] text-white"
                    : "text-[#71717A] hover:text-[#A1A1AA]"
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-hidden">
            {renderPanel(rightTab)}
          </div>
        </div>
      </div>

      {/* Share Dialog */}
      <Dialog open={showShare} onOpenChange={setShowShare}>
        <DialogContent className="bg-[#18181B] border-[#27272A] text-white">
          <DialogHeader>
            <DialogTitle>Share Project</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-[#A1A1AA] mb-2 block">
                Visibility
              </label>
              <Select
                value={project?.visibility || "private"}
                onValueChange={async (val) => {
                  if (!projectId) return;
                  try {
                    await client.entities.projects.update({
                      id: String(projectId),
                      data: { visibility: val },
                    });
                    setProject((prev) =>
                      prev ? { ...prev, visibility: val } : null
                    );
                    toast.success("Visibility updated");
                  } catch {
                    toast.error("Failed to update");
                  }
                }}
              >
                <SelectTrigger className="bg-[#09090B] border-[#27272A] text-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-[#18181B] border-[#27272A]">
                  <SelectItem value="public" className="text-[#FAFAFA]">
                    <div className="flex items-center gap-2">
                      <Globe className="w-4 h-4" /> Public — Anyone can view
                    </div>
                  </SelectItem>
                  <SelectItem value="secret" className="text-[#FAFAFA]">
                    <div className="flex items-center gap-2">
                      <EyeIcon className="w-4 h-4" /> Secret — Only with link
                    </div>
                  </SelectItem>
                  <SelectItem value="private" className="text-[#FAFAFA]">
                    <div className="flex items-center gap-2">
                      <Lock className="w-4 h-4" /> Private — Only you
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            {project?.deploy_url && (
              <div>
                <label className="text-xs text-[#A1A1AA] mb-2 block">
                  Share Link
                </label>
                <div className="flex items-center gap-2">
                  <Input
                    value={project.deploy_url}
                    readOnly
                    className="bg-[#09090B] border-[#27272A] text-white text-sm"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-[#27272A] text-white hover:bg-[#27272A] bg-transparent"
                    onClick={handleCopyLink}
                  >
                    {copied ? (
                      <Check className="w-4 h-4" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </Button>
                </div>
              </div>
            )}
            <div className="flex items-center gap-2 pt-2">
              {["X", "LinkedIn", "Instagram"].map((platform) => (
                <Button
                  key={platform}
                  size="sm"
                  variant="outline"
                  className="border-[#27272A] text-[#A1A1AA] hover:text-white hover:bg-[#27272A] bg-transparent flex-1"
                >
                  {platform}
                </Button>
              ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Publish Dialog */}
      <Dialog open={showPublish} onOpenChange={setShowPublish}>
        <DialogContent className="bg-[#18181B] border-[#27272A] text-white">
          <DialogHeader>
            <DialogTitle>Publish Project</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-[#A1A1AA] mb-2 block">
                Deployment URL
              </label>
              <div className="flex items-center gap-2">
                <span className="text-sm text-[#52525B]">https://</span>
                <Input
                  value={publishUrl}
                  onChange={(e) => setPublishUrl(e.target.value)}
                  className="bg-[#09090B] border-[#27272A] text-white text-sm"
                />
              </div>
            </div>
            <div className="bg-[#09090B] border border-[#27272A] rounded-lg p-3">
              <div className="flex items-center gap-2 text-xs text-[#A1A1AA]">
                <Rocket className="w-4 h-4 text-[#A855F7]" />
                <span>
                  Your project will be deployed to a global CDN with SSL.
                </span>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setShowPublish(false)}
              className="text-[#A1A1AA] hover:text-white hover:bg-[#27272A]"
            >
              Cancel
            </Button>
            <Button
              onClick={handlePublish}
              disabled={isPublishing}
              className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0"
            >
              {isPublishing ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : (
                <Rocket className="w-4 h-4 mr-2" />
              )}
              Publish
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Wrapper that provides the WorkspaceContext */
export default function ProjectWorkspacePage() {
  return (
    <WorkspaceProvider>
      <WorkspaceInner />
    </WorkspaceProvider>
  );
}