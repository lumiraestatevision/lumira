"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ModelPreview } from "@/components/ModelPreview";
import { type SharedProject, getShared, sharedModelUrl } from "@/lib/api";

// Kundenansicht über den geteilten Link: nur Projektname, 3D-Vorschau (mit Varianten und
// Möbel-Schalter) und Rundgang – keine Verwaltung, keine Dateien.

const WalkViewer = dynamic(() => import("@/components/WalkViewer"), { ssr: false });
const POLL_MS = 10_000; // solange das Modell noch berechnet wird

export default function SharedView() {
  const { token } = useParams<{ token: string }>();
  const [project, setProject] = useState<SharedProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [walking, setWalking] = useState(false);
  const closeWalk = useCallback(() => setWalking(false), []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const shared = await getShared(token);
        if (!active) return;
        setProject(shared);
        setError(null);
        document.title = `${shared.name} – Lumira`;
        if (!shared.has_model && shared.status === "processing") timer = setTimeout(load, POLL_MS);
      } catch {
        if (active) setError("Dieser Link ist ungültig oder wurde deaktiviert.");
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [token]);

  const model = sharedModelUrl(token);
  return (
    <main className="share">
      <header className="share-header">
        <span className="brand">Lumira</span>
        {project && <h1>{project.name}</h1>}
      </header>
      {error && <p className="error">{error}</p>}
      {project && !project.has_model && (
        <p className="muted">
          {project.status === "failed"
            ? "Für dieses Projekt ist noch kein 3D-Modell verfügbar."
            : "Das 3D-Modell wird gerade berechnet – diese Seite aktualisiert sich von selbst."}
        </p>
      )}
      {project?.has_model && (
        <section className="share-model">
          <div className="share-intro">
            <p className="muted">
              Ziehen zum Drehen, zwei Finger oder Mausrad zum Zoomen. Im Rundgang gehen Sie frei
              durch das Haus – am Computer, am Handy oder mit VR-Brille.
            </p>
            <button type="button" onClick={() => setWalking(true)}>
              Rundgang starten
            </button>
          </div>
          <ModelPreview src={model} />
        </section>
      )}
      {walking && <WalkViewer src={model} onClose={closeWalk} />}
    </main>
  );
}
