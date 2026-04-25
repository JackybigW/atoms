import { useState, useRef, useEffect } from "react";
import {
  MoreHorizontal,
  Globe,
  Lock,
  Eye,
  Trash2,
  ExternalLink,
  Clock,
  Code2,
  Pencil,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface Project {
  id: number;
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

interface ProjectCardProps {
  project: Project;
  onOpen: (id: number) => void;
  onDelete: (id: number) => void;
  onRename: (id: number, newName: string) => void;
}

export default function ProjectCard({
  project,
  onOpen,
  onDelete,
  onRename,
}: ProjectCardProps) {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(project.name);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [isRenaming]);

  const commitRename = () => {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== project.name) onRename(project.id, trimmed);
    setIsRenaming(false);
  };

  const timeAgo = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return date.toLocaleDateString();
  };

  const visibilityIcon =
    project.visibility === "public" ? (
      <Globe className="w-3 h-3" />
    ) : project.visibility === "secret" ? (
      <Eye className="w-3 h-3" />
    ) : (
      <Lock className="w-3 h-3" />
    );

  return (
    <>
      <div
        className="group bg-[#18181B] border border-[#27272A] rounded-xl overflow-hidden hover:border-[#7C3AED]/40 transition-all duration-300 cursor-pointer"
        onClick={() => onOpen(project.id)}
      >
        {/* Thumbnail */}
        <div className="aspect-video bg-[#0D0D0F] relative overflow-hidden">
          {project.thumbnail_url ? (
            <img
              src={project.thumbnail_url}
              alt={project.name}
              className="w-full h-full object-cover opacity-80 group-hover:opacity-100 group-hover:scale-105 transition-all duration-500"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Code2 className="w-12 h-12 text-[#27272A]" />
            </div>
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-[#18181B] via-transparent to-transparent opacity-60" />

          {/* Status badge */}
          {project.deploy_url && (
            <div className="absolute top-3 right-3 flex items-center gap-1 bg-[#22C55E]/20 text-[#22C55E] px-2 py-0.5 rounded-full text-[10px] font-medium">
              <div className="w-1.5 h-1.5 rounded-full bg-[#22C55E]" />
              Live
            </div>
          )}
        </div>

        {/* Info */}
        <div className="p-4">
          <div className="flex items-start justify-between mb-2">
            <div className="flex-1 min-w-0">
              {isRenaming ? (
                <input
                  ref={renameInputRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") { setRenameValue(project.name); setIsRenaming(false); }
                  }}
                  onClick={(e) => e.stopPropagation()}
                  className="w-full text-sm font-semibold text-white bg-[#27272A] border border-[#7C3AED]/60 rounded px-1.5 py-0.5 outline-none"
                />
              ) : (
                <h3 className="text-sm font-semibold text-white truncate">
                  {project.name}
                </h3>
              )}
              {project.description && (
                <p className="text-xs text-[#71717A] truncate mt-0.5">
                  {project.description}
                </p>
              )}
            </div>
            <DropdownMenu>
              <DropdownMenuTrigger
                className="outline-none"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="p-1 rounded hover:bg-[#27272A] text-[#71717A] hover:text-white transition-colors">
                  <MoreHorizontal className="w-4 h-4" />
                </div>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="bg-[#18181B] border-[#27272A]">
                <DropdownMenuItem
                  className="text-[#FAFAFA] cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(project.id);
                  }}
                >
                  <Code2 className="w-4 h-4 mr-2" />
                  Open Workspace
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="text-[#FAFAFA] cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRenameValue(project.name);
                    setIsRenaming(true);
                  }}
                >
                  <Pencil className="w-4 h-4 mr-2" />
                  Rename
                </DropdownMenuItem>
                {project.deploy_url && (
                  <DropdownMenuItem
                    className="text-[#FAFAFA] cursor-pointer"
                    onClick={(e) => {
                      e.stopPropagation();
                      window.open(project.deploy_url, "_blank");
                    }}
                  >
                    <ExternalLink className="w-4 h-4 mr-2" />
                    View Deployment
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator className="bg-[#27272A]" />
                <DropdownMenuItem
                  className="text-red-400 cursor-pointer focus:text-red-400"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowDeleteDialog(true);
                  }}
                >
                  <Trash2 className="w-4 h-4 mr-2" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="flex items-center gap-3 text-[10px] text-[#52525B]">
            <span className="flex items-center gap-1">
              {visibilityIcon}
              {project.visibility}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {timeAgo(project.updated_at || project.created_at)}
            </span>
            <span className="px-1.5 py-0.5 bg-[#27272A] rounded text-[#A1A1AA]">
              {project.framework}
            </span>
          </div>
        </div>
      </div>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent className="bg-[#18181B] border-[#27272A]">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-white">
              Delete Project
            </AlertDialogTitle>
            <AlertDialogDescription className="text-[#A1A1AA]">
              Are you sure you want to delete &quot;{project.name}&quot;? This
              action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="bg-[#27272A] border-[#3F3F46] text-white hover:bg-[#3F3F46]">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 text-white hover:bg-red-700"
              onClick={() => onDelete(project.id)}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}