import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { client } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import Navbar from "@/components/Navbar";
import ProjectCard from "@/components/ProjectCard";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Plus,
  Search,
  LayoutGrid,
  List,
  Loader2,
  Sparkles,
  FolderOpen,
} from "lucide-react";
import { toast } from "sonner";

interface Project {
  id: number;
  project_number: number;
  name: string;
  description?: string;
  status: string;
  visibility: string;
  framework: string;
  thumbnail_url?: string;
  deploy_url?: string;
  created_at: string;
  updated_at?: string;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user, isAuthenticated, isLoading: authLoading } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [creating, setCreating] = useState(false);

  // Create form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newFramework, setNewFramework] = useState("react");
  const [newVisibility, setNewVisibility] = useState("private");

  // Load projects
  useEffect(() => {
    if (!isAuthenticated) {
      setIsLoading(false);
      return;
    }
    const loadProjects = async () => {
      try {
        const res = await client.entities.projects.query({
          query: { status: "active" },
          sort: "-updated_at",
          limit: 50,
        });
        if (res?.data?.items) {
          setProjects(
            res.data.items.map((p: Record<string, unknown>) => ({
              id: p.id as number,
              project_number: (p.project_number as number) || 0,
              name: p.name as string,
              description: p.description as string,
              status: (p.status as string) || "active",
              visibility: (p.visibility as string) || "private",
              framework: (p.framework as string) || "react",
              thumbnail_url: p.thumbnail_url as string,
              deploy_url: p.deploy_url as string,
              created_at: p.created_at as string,
              updated_at: p.updated_at as string,
            }))
          );
        }
      } catch (err) {
        console.error("Failed to load projects:", err);
      } finally {
        setIsLoading(false);
      }
    };
    loadProjects();
  }, [isAuthenticated]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const now = new Date().toISOString();
      const res = await client.entities.projects.create({
        data: {
          name: newName.trim(),
          description: newDesc.trim(),
          status: "active",
          visibility: newVisibility,
          framework: newFramework,
          created_at: now,
          updated_at: now,
        },
      });
      if (res?.data) {
        toast.success("Project created!");
        setShowCreate(false);
        setNewName("");
        setNewDesc("");
        navigate(`/workspace/${res.data.project_number}`);
      }
    } catch (err) {
      console.error("Failed to create project:", err);
      toast.error("Failed to create project");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await client.entities.projects.update({
        id: String(id),
        data: { status: "deleted", updated_at: new Date().toISOString() },
      });
      setProjects((prev) => prev.filter((p) => p.id !== id));
      toast.success("Project deleted");
    } catch (err) {
      console.error("Failed to delete project:", err);
      toast.error("Failed to delete project");
    }
  };

  const handleRename = async (id: number, newName: string) => {
    try {
      await client.entities.projects.update({
        id: String(id),
        data: { name: newName, updated_at: new Date().toISOString() },
      });
      setProjects((prev) => prev.map((p) => p.id === id ? { ...p, name: newName } : p));
      toast.success("Project renamed");
    } catch (err) {
      console.error("Failed to rename project:", err);
      toast.error("Failed to rename project");
    }
  };

  const handleOpen = (projectNumber: number) => {
    navigate(`/workspace/${projectNumber}`);
  };

  const filtered = projects.filter(
    (p) =>
      p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      p.description?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  if (authLoading) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-[#A855F7] animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    navigate("/login");
    return null;
  }

  return (
    <div className="min-h-screen bg-[#09090B] text-white">
      <Navbar />

      <div className="max-w-7xl mx-auto px-6 pt-24 pb-12">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold">
              Welcome back{user?.display_name ? `, ${user.display_name}` : ""}
            </h1>
            <p className="text-[#71717A] text-sm mt-1">
              {projects.length} project{projects.length !== 1 ? "s" : ""}
            </p>
          </div>
          <Button
            className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0"
            onClick={() => {
              const nextNum = projects.length > 0 ? Math.max(...projects.map(p => p.project_number)) + 1 : 1;
              setNewName(`Workspace ${nextNum}`);
              setNewDesc("");
              setShowCreate(true);
            }}
          >
            <Plus className="w-4 h-4 mr-2" />
            New Project
          </Button>
        </div>

        {/* Search & Filters */}
        <div className="flex items-center gap-3 mb-6">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#52525B]" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search projects..."
              className="pl-10 bg-[#18181B] border-[#27272A] text-white placeholder:text-[#52525B] h-9"
            />
          </div>
          <div className="flex items-center bg-[#18181B] border border-[#27272A] rounded-lg p-0.5">
            <button
              onClick={() => setViewMode("grid")}
              className={`p-1.5 rounded ${
                viewMode === "grid"
                  ? "bg-[#27272A] text-white"
                  : "text-[#71717A]"
              }`}
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={`p-1.5 rounded ${
                viewMode === "list"
                  ? "bg-[#27272A] text-white"
                  : "text-[#71717A]"
              }`}
            >
              <List className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Projects Grid */}
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 text-[#A855F7] animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20">
            <FolderOpen className="w-16 h-16 text-[#27272A] mb-4" />
            <h3 className="text-lg font-semibold text-[#A1A1AA] mb-2">
              {searchQuery ? "No projects found" : "No projects yet"}
            </h3>
            <p className="text-sm text-[#52525B] mb-6">
              {searchQuery
                ? "Try a different search term"
                : "Create your first project to get started"}
            </p>
            {!searchQuery && (
              <Button
                className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0"
                onClick={() => {
                  const nextNum = projects.length > 0 ? Math.max(...projects.map(p => p.project_number)) + 1 : 1;
                  setNewName(`Workspace ${nextNum}`);
                  setNewDesc("");
                  setShowCreate(true);
                }}
              >
                <Plus className="w-4 h-4 mr-2" />
                Create Project
              </Button>
            )}
          </div>
        ) : viewMode === "grid" ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onOpen={handleOpen}
                onDelete={handleDelete}
                onRename={handleRename}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((project) => (
              <div
                key={project.id}
                onClick={() => handleOpen(project.project_number)}
                className="flex items-center gap-4 bg-[#18181B] border border-[#27272A] rounded-lg px-4 py-3 hover:border-[#7C3AED]/30 cursor-pointer transition-all"
              >
                <div className="w-10 h-10 rounded-lg bg-[#0D0D0F] flex items-center justify-center flex-shrink-0">
                  <FolderOpen className="w-5 h-5 text-[#7C3AED]" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-medium text-white truncate">
                    {project.name}
                  </h3>
                  <p className="text-xs text-[#52525B] truncate">
                    {project.description || "No description"}
                  </p>
                </div>
                <span className="text-[10px] text-[#52525B] px-2 py-0.5 bg-[#27272A] rounded">
                  {project.framework}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create Project Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="bg-[#18181B] border-[#27272A] text-white">
          <DialogHeader>
            <DialogTitle>Create New Project</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label className="text-[#A1A1AA] text-xs">Project Name</Label>
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="My Awesome App"
                className="mt-1.5 bg-[#09090B] border-[#27272A] text-white"
              />
            </div>
            <div>
              <Label className="text-[#A1A1AA] text-xs">Description</Label>
              <Input
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="A brief description of your project"
                className="mt-1.5 bg-[#09090B] border-[#27272A] text-white"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-[#A1A1AA] text-xs">Framework</Label>
                <Select value={newFramework} onValueChange={setNewFramework}>
                  <SelectTrigger className="mt-1.5 bg-[#09090B] border-[#27272A] text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#18181B] border-[#27272A]">
                    <SelectItem value="react" className="text-[#FAFAFA]">
                      React
                    </SelectItem>
                    <SelectItem value="vue" className="text-[#FAFAFA]">
                      Vue
                    </SelectItem>
                    <SelectItem value="html" className="text-[#FAFAFA]">
                      HTML
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-[#A1A1AA] text-xs">Visibility</Label>
                <Select
                  value={newVisibility}
                  onValueChange={setNewVisibility}
                >
                  <SelectTrigger className="mt-1.5 bg-[#09090B] border-[#27272A] text-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-[#18181B] border-[#27272A]">
                    <SelectItem value="private" className="text-[#FAFAFA]">
                      🔒 Private
                    </SelectItem>
                    <SelectItem value="public" className="text-[#FAFAFA]">
                      🌐 Public
                    </SelectItem>
                    <SelectItem value="secret" className="text-[#FAFAFA]">
                      👁 Secret
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setShowCreate(false)}
              className="text-[#A1A1AA] hover:text-white hover:bg-[#27272A]"
            >
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!newName.trim() || creating}
              className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0"
            >
              {creating ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : (
                <Plus className="w-4 h-4 mr-2" />
              )}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}