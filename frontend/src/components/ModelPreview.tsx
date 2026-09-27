"use client";

import { createElement, useCallback, useEffect, useRef, useState } from "react";

// Minimaler Ausschnitt der <model-viewer>-API, den wir hier brauchen.
interface ViewerMaterial {
  name: string;
  getAlphaMode(): string;
  setAlphaMode(mode: "OPAQUE" | "MASK" | "BLEND"): void;
  setAlphaCutoff(cutoff: number): void;
  pbrMetallicRoughness: {
    baseColorFactor: number[];
    setBaseColorFactor(color: number[]): void;
  };
  occlusionTexture?: { texture: unknown };
}
interface ViewerElement extends HTMLElement {
  model?: { materials: ViewerMaterial[] };
  exposure: number;
  // Ausstattungsvarianten aus dem LV (glTF KHR_materials_variants)
  availableVariants: string[];
  variantName: string | null;
}

// Eingebranntes Licht (Lightmap als Occlusion-Textur) dunkelt die Umgebungsbeleuchtung ab –
// mittlere Bodenhelligkeit ≈ 0,5. Mit höherer Belichtung bleiben Räume hell, behalten aber
// Licht und Schatten.
const EXPOSURE = 0.9;
const EXPOSURE_BAKED = 1.35;

// Materialnamen aus dem Generator (build_scene.py):
//   „Moebel: …“  lose Möbel – per Knopf ein-/ausblendbar
//   „Decke“, „Leuchte“ – verdecken in der Draufsicht die Räume, daher immer aus
const isFurniture = (m: ViewerMaterial) => m.name.startsWith("Moebel:");
const isCeiling = (m: ViewerMaterial) => m.name === "Decke" || m.name === "Leuchte";

// Ausblenden ohne zweites Modell: Alpha 0 + Maskierung verwirft jedes Pixel.
function setVisible(material: ViewerMaterial, visible: boolean, original: Map<ViewerMaterial, [string, number[]]>) {
  if (!original.has(material)) {
    original.set(material, [material.getAlphaMode(), [...material.pbrMetallicRoughness.baseColorFactor]]);
  }
  const [mode, color] = original.get(material)!;
  if (visible) {
    material.setAlphaMode(mode as "OPAQUE" | "MASK" | "BLEND");
    material.pbrMetallicRoughness.setBaseColorFactor(color);
  } else {
    material.setAlphaMode("MASK");
    material.setAlphaCutoff(1);
    material.pbrMetallicRoughness.setBaseColorFactor([color[0], color[1], color[2], 0]);
  }
}

// <model-viewer> ist ein Web Component und greift auf window zu – daher erst im Browser laden.
export function ModelPreview({ src }: { src: string }) {
  const [ready, setReady] = useState(false);
  const [furniture, setFurniture] = useState(true);
  const [hasFurniture, setHasFurniture] = useState(false);
  const [variants, setVariants] = useState<string[]>([]);
  const [variant, setVariant] = useState("");
  const viewer = useRef<ViewerElement | null>(null);
  const original = useRef(new Map<ViewerMaterial, [string, number[]]>());
  const furnitureOn = useRef(true); // aktueller Stand für den load-Listener

  useEffect(() => {
    let active = true;
    void import("@google/model-viewer").then(() => active && setReady(true));
    return () => {
      active = false;
    };
  }, []);

  const apply = useCallback((showFurniture: boolean) => {
    const materials = viewer.current?.model?.materials ?? [];
    for (const material of materials) {
      if (isCeiling(material)) setVisible(material, false, original.current);
      else if (isFurniture(material)) setVisible(material, showFurniture, original.current);
    }
    setHasFurniture(materials.some(isFurniture));
  }, []);

  // Nach jedem Laden (auch bei neuem src) Decken aus- und den Möbelzustand anwenden.
  const attach = useCallback(
    (element: ViewerElement | null) => {
      viewer.current = element;
      if (!element) return;
      element.addEventListener("load", () => {
        original.current = new Map();
        apply(furnitureOn.current);
        const baked = (element.model?.materials ?? []).some((m) => m.occlusionTexture?.texture);
        element.exposure = baked ? EXPOSURE_BAKED : EXPOSURE;
        // Die Datei enthält die Standardvariante als Grundmaterial – sie steht vorn in der Liste
        const names = element.availableVariants ?? [];
        setVariants(names);
        setVariant(element.variantName ?? names[0] ?? "");
      });
    },
    [apply],
  );

  function toggle() {
    const next = !furniture;
    furnitureOn.current = next;
    setFurniture(next);
    apply(next);
  }

  function chooseVariant(name: string) {
    setVariant(name);
    if (viewer.current) viewer.current.variantName = name;
  }

  if (!ready) return <p className="muted">3D-Vorschau wird geladen …</p>;
  return (
    <div className="viewer">
      {(hasFurniture || variants.length > 1) && (
        <div className="viewer-controls">
          {variants.length > 1 && (
            <select
              className="variant"
              aria-label="Ausstattungsvariante"
              value={variant}
              onChange={(event) => chooseVariant(event.target.value)}
            >
              {variants.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          )}
          {hasFurniture && (
            <button type="button" className="toggle" aria-pressed={furniture} onClick={toggle}>
              {furniture ? "Möbel ausblenden" : "Möbel einblenden"}
            </button>
          )}
        </div>
      )}
      {createElement("model-viewer", {
        ref: attach,
        src,
        "camera-controls": true,
        // Schräg von vorn (Süden) wie ein Architekturmodell. Die automatische Entfernung
        // rahmt die Kugel um das (flache) Modell – 70 % füllt das Bild deutlich besser.
        "camera-orbit": "15deg 50deg 70%",
        // Gerichtetes Studiolicht statt gleichmäßiger Ausleuchtung: Wände heben sich ab.
        "environment-image": "neutral",
        exposure: String(EXPOSURE),
        "shadow-intensity": "1",
        "shadow-softness": "0.6",
        "interaction-prompt": "none",
        alt: "3D-Modell des Grundrisses",
      })}
    </div>
  );
}
