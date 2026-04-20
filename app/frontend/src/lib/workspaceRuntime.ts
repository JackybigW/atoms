import { buildAuthHeaders } from "./authToken";

export interface WorkspacePreviewBundle {
  project_id?: number;
  status?: string;
  preview_session_key: string;
  preview_expires_at?: string | null;
  preview_frontend_url: string;
  preview_backend_url: string;
  frontend_port?: number | null;
  backend_port?: number | null;
  frontend_status?: string | null;
  backend_status?: string | null;
}

/** @deprecated Use WorkspacePreviewBundle */
export interface WorkspaceRuntimeStatus {
  project_id: number;
  status: string;
  preview_url: string;
  frontend_port?: number | null;
  backend_port?: number | null;
}

export function buildPreviewUrl(previewUrl: string): string {
  return previewUrl;
}

export async function ensureWorkspaceRuntime(projectId: number): Promise<WorkspacePreviewBundle> {
  const response = await fetch(`/api/v1/workspace-runtime/projects/${projectId}/ensure`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...buildAuthHeaders(),
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to ensure runtime: ${response.status}`);
  }
  return response.json();
}
