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
  created_at: string;
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

export async function deleteProject(id: string): Promise<void> {
  const response = await fetch(`${API_URL}/projects/${id}`, { method: "DELETE" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
}

export const artifactUrl = (projectId: string, name: string) =>
  `${API_URL}/projects/${projectId}/artifacts/${name}`;
