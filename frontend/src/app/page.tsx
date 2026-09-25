"use client";

import { useCallback, useEffect, useState } from "react";

import { ModelPreview } from "@/components/ModelPreview";
import {
  PIPELINE_STEPS,
  type Project,
  type ProjectDetail,
  artifactUrl,
  createProject,
  deleteProject,
  getProject,
  listProjects,
} from "@/lib/api";

const POLL_MS = 2000;

const STATUS_LABEL: Record<Project["status"], string> = {
  processing: "in Bearbeitung",
  completed: "fertig",
  failed: "fehlgeschlagen",
};

export default function Home() {
  const [projects, setProjects] = useState<ProjectDetail[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const list = await listProjects();
      setProjects(await Promise.all(list.slice(0, 10).map((p) => getProject(p.id))));
      setError(null);
    } catch (e) {
      setError(`Backend nicht erreichbar: ${(e as Error).message}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    for (const key of ["blv", "images"]) {
      const files = data.getAll(key).filter((f) => f instanceof File && f.size > 0);
      data.delete(key);
      files.forEach((f) => data.append(key, f));
    }
    setSubmitting(true);
    try {
      await createProject(data);
      form.reset();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main>
      <h1>Lumira</h1>
      <p className="subtitle">Grundriss und Leistungsverzeichnis hochladen – die Pipeline erzeugt das 3D-Modell.</p>

      <section className="card">
        <h2>Neues Projekt</h2>
        <form onSubmit={onSubmit}>
          <label>
            Projektname
            <input type="text" name="name" required maxLength={200} placeholder="z. B. Musterhaus, WE 3" />
          </label>
          <label>
            Grundriss <small>PDF oder DXF (DWG folgt)</small>
            <input type="file" name="floor_plan" accept=".pdf,.dxf,.dwg" required />
          </label>
          <label>
            Leistungsverzeichnis <small>optional, PDF</small>
            <input type="file" name="blv" accept=".pdf" />
          </label>
          <label>
            Beispiel- oder Bestandsfotos <small>optional</small>
            <input type="file" name="images" accept="image/*" multiple />
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? "Wird hochgeladen …" : "Projekt anlegen"}
          </button>
          {error && <p className="error">{error}</p>}
        </form>
      </section>

      <section className="card">
        <h2>Projekte</h2>
        {projects.length === 0 && <p className="muted">Noch keine Projekte.</p>}
        {projects.map((project) => (
          <ProjectRow key={project.id} project={project} onDeleted={refresh} />
        ))}
      </section>
    </main>
  );
}

function ProjectRow({ project, onDeleted }: { project: ProjectDetail; onDeleted: () => Promise<void> }) {
  const done = new Set(project.events.map((e) => e.type));
  const hasModel = "model_gltf" in project.artifacts;
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function onDelete() {
    if (!window.confirm(`Projekt „${project.name}“ mit allen Dateien und dem 3D-Modell endgültig löschen?`)) {
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(project.id);
      await onDeleted();
    } catch (e) {
      setDeleteError((e as Error).message);
      setDeleting(false);
    }
  }

  return (
    <article className="project">
      <header>
        <strong>{project.name}</strong>
        <span className="actions">
          <span className={`badge ${project.status}`}>{STATUS_LABEL[project.status]}</span>
          <button
            type="button"
            className="danger"
            onClick={() => void onDelete()}
            disabled={deleting || project.status === "processing"}
            title={
              project.status === "processing"
                ? "Löschen ist möglich, sobald das Projekt fertig oder fehlgeschlagen ist"
                : "Projekt und alle Dateien löschen"
            }
          >
            {deleting ? "Wird gelöscht …" : "Löschen"}
          </button>
        </span>
      </header>
      {deleteError && <p className="error">{deleteError}</p>}
      <ol className="steps">
        {PIPELINE_STEPS.map((step) => (
          <li key={step} className={done.has(step) ? "done" : undefined}>
            {step}
          </li>
        ))}
      </ol>
      {project.error && (
        <p className="error">
          Fehler in {project.error.step ?? "?"}: {project.error.message}
        </p>
      )}
      {hasModel && (
        <>
          <div className="links">
            <a href={artifactUrl(project.id, "model_gltf")}>Modell (GLB)</a>
            <a href={artifactUrl(project.id, "model_fbx")}>Modell (FBX, für Unreal)</a>
            {"blv_result" in project.artifacts && (
              <a href={artifactUrl(project.id, "blv_result")}>Materialien (JSON)</a>
            )}
          </div>
          {project.status === "completed" && <ModelPreview src={artifactUrl(project.id, "model_gltf")} />}
        </>
      )}
    </article>
  );
}
