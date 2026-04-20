import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import { client } from "@/lib/api";
import { toast } from "sonner";

export interface ProjectFile {
  id?: number;
  file_path: string;
  file_name: string;
  content: string;
  language: string;
  is_directory: boolean;
}

interface WorkspaceContextType {
  files: ProjectFile[];
  setFiles: React.Dispatch<React.SetStateAction<ProjectFile[]>>;
  writeFile: (filePath: string, content: string) => Promise<void>;
  writeMultipleFiles: (fileUpdates: { path: string; content: string }[]) => Promise<void>;
  projectId: number | null;
  setProjectId: (id: number | null) => void;
  previewHtml: string;
  previewUrl: string;
  setPreviewUrl: (url: string) => void;
  terminalLogs: string[];
  addTerminalLog: (log: string) => void;
  fileVersion: number;
  reloadFiles: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextType>({
  files: [],
  setFiles: () => {},
  writeFile: async () => {},
  writeMultipleFiles: async () => {},
  projectId: null,
  setProjectId: () => {},
  previewHtml: "",
  previewUrl: "",
  setPreviewUrl: () => {},
  terminalLogs: [],
  addTerminalLog: () => {},
  fileVersion: 0,
  reloadFiles: async () => {},
});

function getLanguageFromPath(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    tsx: "typescript", ts: "typescript", jsx: "javascript", js: "javascript",
    css: "css", html: "html", json: "json", md: "markdown", py: "python",
  };
  return map[ext] || "plaintext";
}

function getFileName(filePath: string): string {
  return filePath.split("/").pop() || filePath;
}

/** Build a live preview HTML from the project files */
function buildPreviewHtml(files: ProjectFile[]): string {
  // Find index.html or build from React files
  const indexHtml = files.find(
    (f) => f.file_name === "index.html" && !f.is_directory
  );
  if (indexHtml) return indexHtml.content;

  // Build from React/TSX files
  const appFile = files.find(
    (f) =>
      (f.file_name === "App.tsx" || f.file_name === "App.jsx") &&
      !f.is_directory
  );
  const cssFile = files.find(
    (f) =>
      (f.file_name === "index.css" || f.file_name === "styles.css" || f.file_name === "global.css") &&
      !f.is_directory
  );

  if (!appFile) return "";

  // Strip TypeScript types and JSX for a simple preview
  let code = appFile.content;
  // Remove import statements
  code = code.replace(/^import\s+.*?;\s*$/gm, "");
  // Remove export default
  code = code.replace(/export\s+default\s+/, "");
  // Extract function name
  const funcMatch = code.match(/function\s+(\w+)/);
  const funcName = funcMatch ? funcMatch[1] : "App";

  // Simple JSX-to-HTML conversion for preview
  let jsxContent = "";
  const returnMatch = code.match(/return\s*\(\s*([\s\S]*?)\s*\)\s*;?\s*\}/);
  if (returnMatch) {
    jsxContent = returnMatch[1]
      // Convert className to class
      .replace(/className=/g, "class=")
      // Remove JSX expressions like {variable}
      .replace(/\{[^}]*\}/g, "")
      // Remove self-closing component tags
      .replace(/<[A-Z]\w+[^>]*\/>/g, "")
      // Remove component opening/closing tags
      .replace(/<[A-Z]\w+[^>]*>/g, "<div>")
      .replace(/<\/[A-Z]\w+>/g, "</div>");
  }

  const cssContent = cssFile?.content || "";

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script src="https://cdn.tailwindcss.com"></script>
  <style>${cssContent}</style>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; }
  </style>
</head>
<body>
  <div id="root">${jsxContent}</div>
</body>
</html>`;
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string>("");
  const [terminalLogs, setTerminalLogs] = useState<string[]>([
    "$ atoms init project",
    "✓ Project initialized",
    "$ pnpm install",
    "✓ Dependencies installed (2.3s)",
    "$ pnpm run dev",
    "  VITE v5.4.0  ready in 312ms",
    "",
    "  ➜  Local:   http://localhost:5173/",
    "",
    "✓ Development server running",
  ]);
  const [fileVersion, setFileVersion] = useState(0);
  const projectIdRef = useRef<number | null>(null);
  const reloadingRef = useRef(false);

  // Keep ref in sync
  projectIdRef.current = projectId;

  // Reset previewUrl whenever the active project changes
  useEffect(() => {
    setPreviewUrl("");
  }, [projectId]);

  const addTerminalLog = useCallback((log: string) => {
    setTerminalLogs((prev) => [...prev, log]);
  }, []);

  const reloadFiles = useCallback(async () => {
    const pid = projectIdRef.current;
    if (!pid || reloadingRef.current) return;
    reloadingRef.current = true;
    try {
      const res = await client.entities.project_files.query({
        query: { project_id: pid },
        sort: "file_path",
        limit: 500,
      });
      if (res?.data?.items) {
        setFiles(res.data.items.map((f: Record<string, unknown>) => ({
          id: f.id as number,
          file_path: f.file_path as string,
          file_name: f.file_name as string,
          content: (f.content as string) || "",
          language: (f.language as string) || "plaintext",
          is_directory: Boolean(f.is_directory),
        })));
        setFileVersion(v => v + 1);
      }
    } catch (err) {
      console.error("Failed to reload files:", err);
    } finally {
      reloadingRef.current = false;
    }
  }, [setFiles]);

  const writeFile = useCallback(
    async (filePath: string, content: string) => {
      const fileName = getFileName(filePath);
      const language = getLanguageFromPath(filePath);
      const pid = projectIdRef.current;

      setFiles((prev) => {
        const existing = prev.find((f) => f.file_path === filePath);
        if (existing) {
          return prev.map((f) =>
            f.file_path === filePath ? { ...f, content } : f
          );
        }
        return [
          ...prev,
          {
            file_path: filePath,
            file_name: fileName,
            content,
            language,
            is_directory: false,
          },
        ];
      });

      setFileVersion((v) => v + 1);

      // Persist to DB
      if (pid) {
        try {
          // Try to find existing file
          const res = await client.entities.project_files.query({
            query: { project_id: pid, file_path: filePath },
            limit: 1,
          });
          const now = new Date().toISOString();
          if (res?.data?.items && res.data.items.length > 0) {
            const fileId = res.data.items[0].id as number;
            await client.entities.project_files.update({
              id: String(fileId),
              data: { content, updated_at: now },
            });
          } else {
            await client.entities.project_files.create({
              data: {
                project_id: pid,
                file_path: filePath,
                file_name: fileName,
                content,
                language,
                is_directory: false,
                created_at: now,
                updated_at: now,
              },
            });
          }
        } catch (err) {
          console.error("Failed to persist file:", err);
        }
      }
    },
    []
  );

  const writeMultipleFiles = useCallback(
    async (fileUpdates: { path: string; content: string }[]) => {
      addTerminalLog("");
      addTerminalLog("$ alex writing files...");

      for (const update of fileUpdates) {
        await writeFile(update.path, update.content);
        addTerminalLog(`  ✓ ${update.path}`);
      }

      addTerminalLog(`✓ ${fileUpdates.length} file(s) written`);
      addTerminalLog("");
      addTerminalLog("$ pnpm run dev");
      addTerminalLog("  ✓ HMR update applied");

      toast.success(`${fileUpdates.length} file(s) written to project`);
    },
    [writeFile, addTerminalLog]
  );

  const previewHtml = buildPreviewHtml(files);

  return (
    <WorkspaceContext.Provider
      value={{
        files,
        setFiles,
        writeFile,
        writeMultipleFiles,
        projectId,
        setProjectId,
        previewHtml,
        previewUrl,
        setPreviewUrl,
        terminalLogs,
        addTerminalLog,
        fileVersion,
        reloadFiles,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceContext);
}

/**
 * Parse code blocks from AI response and extract file operations.
 * Supports formats:
 *   ```tsx:src/App.tsx
 *   ```src/App.tsx
 *   // filename: src/App.tsx
 */
export function parseCodeBlocks(
  content: string
): { path: string; content: string }[] {
  const results: { path: string; content: string }[] = [];

  // Pattern 1: ```lang:filepath or ```filepath
  const regex = /```(?:(\w+):)?([^\n`]+\.\w+)\n([\s\S]*?)```/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    const filePath = (match[2] || "").trim();
    const code = (match[3] || "").trim();
    if (filePath && code) {
      results.push({ path: filePath, content: code });
    }
  }

  if (results.length > 0) return results;

  // Pattern 2: "// filename: path" followed by code block
  const regex2 =
    /(?:\/\/|#|<!--)\s*(?:filename|file|path):\s*([^\n]+)\n```\w*\n([\s\S]*?)```/gi;
  while ((match = regex2.exec(content)) !== null) {
    const filePath = (match[1] || "").trim();
    const code = (match[2] || "").trim();
    if (filePath && code) {
      results.push({ path: filePath, content: code });
    }
  }

  return results;
}