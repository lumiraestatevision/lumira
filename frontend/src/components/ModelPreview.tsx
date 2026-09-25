"use client";

import { createElement, useEffect, useState } from "react";

// <model-viewer> ist ein Web Component und greift auf window zu – daher erst im Browser laden.
export function ModelPreview({ src }: { src: string }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    void import("@google/model-viewer").then(() => active && setReady(true));
    return () => {
      active = false;
    };
  }, []);

  if (!ready) return <p className="muted">3D-Vorschau wird geladen …</p>;
  return createElement("model-viewer", {
    src,
    "camera-controls": true,
    // Schräg von vorn (Süden) wie ein Architekturmodell. Die automatische Entfernung
    // rahmt die Kugel um das (flache) Modell – 70 % füllt das Bild deutlich besser.
    "camera-orbit": "15deg 50deg 70%",
    // Gerichtetes Studiolicht statt gleichmäßiger Ausleuchtung: Wände heben sich ab.
    "environment-image": "neutral",
    exposure: "0.9",
    "shadow-intensity": "1",
    "shadow-softness": "0.6",
    "interaction-prompt": "none",
    alt: "3D-Modell des Grundrisses",
  });
}
