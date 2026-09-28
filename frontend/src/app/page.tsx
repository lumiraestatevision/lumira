"use client";

import dynamic from "next/dynamic";
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
  rerunProject,
  listProjects,
  shareProject,
  shareUrl,
  unshareProject,
} from "@/lib/api";

const POLL_MS = 2000;

// three.js und der Rundgang werden erst beim Öffnen geladen (nicht im ersten Seitenaufruf)
const WalkViewer = dynamic(() => import("@/components/WalkViewer"), { ssr: false });

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
  const [busy, setBusy] = useState<"delete" | "rerun" | "share" | null>(null);
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [walking, setWalking] = useState(false);
  const closeWalk = useCallback(() => setWalking(false), []);
  const running = project.status === "processing";

  async function run(
    kind: "delete" | "rerun" | "share",
    question: string | null,
    action: () => Promise<unknown>,
  ) {
    if (question && !window.confirm(question)) return;
    setBusy(kind);
    setActionError(null);
    try {
      await action();
      await onDeleted();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const onRerun = () =>
    run(
      "rerun",
      `Projekt „${project.name}“ mit denselben Dateien neu berechnen? Das bisherige 3D-Modell wird ersetzt.`,
      () => rerunProject(project.id),
    );
  const onDelete = () =>
    run(
      "delete",
      `Projekt „${project.name}“ mit allen Dateien und dem 3D-Modell endgültig löschen?`,
      () => deleteProject(project.id),
    );
  const onShare = () => run("share", null, () => shareProject(project.id));
  const onUnshare = () =>
    run(
      "share",
      "Kunden-Link deaktivieren? Wer den Link hat, sieht das Projekt danach nicht mehr.",
      () => unshareProject(project.id),
    );
  async function copyLink(token: string) {
    try {
      await navigator.clipboard.writeText(shareUrl(token));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("Link kopieren:", shareUrl(token)); // ohne https kein Zugriff auf die Zwischenablage
    }
  }

  return (
    <article className="project">
      <header>
        <strong>{project.name}</strong>
        <span className="actions">
          <span className={`badge ${project.status}`}>{STATUS_LABEL[project.status]}</span>
          {!project.share_token && (
            <button
              type="button"
              className="secondary"
              onClick={() => void onShare()}
              disabled={busy !== null}
              title="Link für Kunden: nur Ansicht und Rundgang, ohne Verwaltung"
            >
              {busy === "share" ? "Wird erstellt …" : "Kunden-Link"}
            </button>
          )}
          <button
            type="button"
            className="secondary"
            onClick={() => void onRerun()}
            disabled={busy !== null || running}
            title={
              running
                ? "Möglich, sobald das Projekt fertig oder fehlgeschlagen ist"
                : "Mit denselben Dateien neu berechnen (z. B. nach einem Lumira-Update)"
            }
          >
            {busy === "rerun" ? "Wird gestartet …" : "Neu berechnen"}
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => void onDelete()}
            disabled={busy !== null || running}
            title={
              running
                ? "Löschen ist möglich, sobald das Projekt fertig oder fehlgeschlagen ist"
                : "Projekt und alle Dateien löschen"
            }
          >
            {busy === "delete" ? "Wird gelöscht …" : "Löschen"}
          </button>
        </span>
      </header>
      {actionError && <p className="error">{actionError}</p>}
      {project.share_token && (
        <div className="share-link">
          <span>Kunden-Link</span>
          <input
            type="text"
            readOnly
            value={shareUrl(project.share_token)}
            onFocus={(event) => event.target.select()}
            aria-label="Kunden-Link"
          />
          <button type="button" className="secondary" onClick={() => void copyLink(project.share_token!)}>
            {copied ? "Kopiert ✓" : "Kopieren"}
          </button>
          <button type="button" className="danger" onClick={() => void onUnshare()} disabled={busy !== null}>
            Deaktivieren
          </button>
        </div>
      )}
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
          {project.status === "completed" && (
            <>
              <button type="button" onClick={() => setWalking(true)}>
                Rundgang starten
              </button>
              <ModelPreview src={artifactUrl(project.id, "model_gltf")} />
            </>
          )}
        </>
      )}
      {walking && <WalkViewer src={artifactUrl(project.id, "model_gltf")} onClose={closeWalk} />}
    </article>
  );
}
