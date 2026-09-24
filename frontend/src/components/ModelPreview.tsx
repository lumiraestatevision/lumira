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
    "shadow-intensity": "0.6",
    "camera-orbit": "30deg 55deg auto",
    alt: "3D-Modell des Grundrisses",
  });
}
