// Typen und Aufrufe gegen das Lumira-backend (FastAPI, Port 8000).

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ProjectStatus = "processing" | "completed" | "failed";

export interface PipelineEvent {
  event_id: string;
  type: string;
  producer: string;
  occurred_at: string;
}

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  current_step: string | null;
  artifacts: Record<string, string>;
  error: { step?: string; message?: string } | null;
  share_token: string | null;
  created_at: string;
  updated_at: string;
}

// Kunden-Link: nur Name, Status und 3D-Modell
export interface SharedProject {
  name: string;
  status: ProjectStatus;
  has_model: boolean;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  events: PipelineEvent[];
}

export const PIPELINE_STEPS = [
  "project.created",
  "plan.parsed",
  "plan.recognized",
  "rooms.classified",
  "blv.processed",
  "model.generated",
  "vr.exported",
  "project.completed",
] as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const listProjects = () => request<Project[]>("/projects");

export const getProject = (id: string) => request<ProjectDetail>(`/projects/${id}`);

export const createProject = (form: FormData) =>
  request<Project>("/projects", { method: "POST", body: form });

export const rerunProject = (id: string) => request<Project>(`/projects/${id}/rerun`, { method: "POST" });

async function send(path: string, method: "DELETE"): Promise<void> {
  const response = await fetch(`${API_URL}${path}`, { method });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
}

export const deleteProject = (id: string) => send(`/projects/${id}`, "DELETE");

export const shareProject = (id: string) => request<Project>(`/projects/${id}/share`, { method: "POST" });

export const unshareProject = (id: string) => send(`/projects/${id}/share`, "DELETE");

export const getShared = (token: string) => request<SharedProject>(`/share/${encodeURIComponent(token)}`);

export const sharedModelUrl = (token: string) => `${API_URL}/share/${encodeURIComponent(token)}/model`;

/** Öffentliche Adresse der Kundenansicht (gleiche Seite, aus der der Link erzeugt wird). */
export const shareUrl = (token: string) => `${window.location.origin}/share/${encodeURIComponent(token)}`;

export const artifactUrl = (projectId: string, name: string) =>
  `${API_URL}/projects/${projectId}/artifacts/${name}`;
