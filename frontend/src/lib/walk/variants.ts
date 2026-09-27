import * as THREE from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";

import { type LightmapInfo, bakeMaterial } from "@/lib/walk/lightmap";

// Ausstattungsvarianten aus dem LV (glTF KHR_materials_variants): je Boden/Stufe ein Material
// pro Variante. three.js lädt die Erweiterung nicht selbst – wir lesen die Zuordnung aus dem
// glTF und tauschen die Materialien. Geometrie und eingebranntes Licht bleiben gleich.

interface Mapping {
  material: number;
  variants: number[];
}

interface VariantMesh {
  mesh: THREE.Mesh;
  original: THREE.Material | THREE.Material[];
  mappings: Mapping[];
}

export class Variants {
  readonly names: string[];
  private readonly meshes: VariantMesh[] = [];
  private readonly cache = new Map<number, Promise<THREE.Material>>();

  constructor(
    private readonly gltf: GLTF,
    private readonly lightmap: LightmapInfo | null,
  ) {
    const json = gltf.parser.json as {
      extensions?: { KHR_materials_variants?: { variants: { name: string }[] } };
      meshes?: { primitives: { extensions?: { KHR_materials_variants?: { mappings: Mapping[] } } }[] }[];
    };
    this.names = (json.extensions?.KHR_materials_variants?.variants ?? []).map((v) => v.name);
    if (!this.names.length) return;
    gltf.scene.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const association = gltf.parser.associations.get(mesh) as
        | { meshes?: number; primitives?: number }
        | undefined;
      if (association?.meshes === undefined) return;
      const primitive = json.meshes?.[association.meshes]?.primitives[association.primitives ?? 0];
      const mappings = primitive?.extensions?.KHR_materials_variants?.mappings;
      if (mappings?.length) this.meshes.push({ mesh, original: mesh.material, mappings });
    });
  }

  private material(index: number): Promise<THREE.Material> {
    let pending = this.cache.get(index);
    if (!pending) {
      pending = this.gltf.parser.getDependency("material", index).then((material: THREE.Material) => {
        if (this.lightmap) bakeMaterial(material, this.lightmap);
        return material;
      });
      this.cache.set(index, pending);
    }
    return pending;
  }

  /** Variante anwenden; unbekannter Name → Materialien aus der Datei (Standard). */
  async select(name: string): Promise<void> {
    const index = this.names.indexOf(name);
    await Promise.all(
      this.meshes.map(async ({ mesh, original, mappings }) => {
        const mapping = mappings.find((m) => m.variants.includes(index));
        mesh.material = mapping ? await this.material(mapping.material) : original;
      }),
    );
  }
}
