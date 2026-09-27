import * as THREE from "three";

// Eingebranntes Licht aus dem Generator (build_scene.py, bake_lighting):
// Die Lightmap liegt als glTF-Occlusion-Textur auf TEXCOORD_1 (three.js: aoMap, channel 1).
// Kodierung „reinhard“: enc = L / (L_lum + k), k = mittlere Bodenhelligkeit
//   → Licht L = enc · k / (1 − Helligkeit(enc))
// Im Viewer wird daraus eine echte Lightmap: Oberfläche = Materialfarbe × Licht, ohne Echtzeitlicht.
export interface LightmapInfo {
  encoding: "reinhard";
  key: number;
  uv: number;
}

export function lightmapInfo(root: THREE.Object3D): LightmapInfo | null {
  const info = root.userData?.lumira_lightmap as Partial<LightmapInfo> | undefined;
  if (!info || info.encoding !== "reinhard" || typeof info.key !== "number") return null;
  return { encoding: "reinhard", key: info.key, uv: info.uv ?? 1 };
}

const DECODE = "vec3 lightMapIrradiance = lightMapTexel.rgb * lightMapIntensity;";
const DECODED = `
    float lumiraLum = dot( lightMapTexel.rgb, vec3( 0.2126, 0.7152, 0.0722 ) );
    vec3 lightMapIrradiance = lightMapTexel.rgb * ( lumiraKey / max( 1.0 - lumiraLum, 0.02 ) )
      * lightMapIntensity;`;

/** Materialien mit Lightmap auf eingebranntes Licht umstellen. Liefert die Anzahl. */
export function applyLightmaps(root: THREE.Object3D, info: LightmapInfo): number {
  const chunk = THREE.ShaderChunk.lights_fragment_maps;
  if (!chunk.includes(DECODE)) {
    console.warn("three.js-Shader geändert – Lightmap bleibt Umgebungsverdeckung");
    return 0;
  }
  const patched = chunk.replace(DECODE, DECODED);
  const done = new Set<THREE.Material>();
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      const standard = material as THREE.MeshStandardMaterial;
      if (done.has(material) || !standard.isMeshStandardMaterial || !standard.aoMap) continue;
      done.add(material);
      standard.lightMap = standard.aoMap;
      // three.js teilt die Lightmap-Irradianz durch π (Lambert) – das Einbrennen nicht
      standard.lightMapIntensity = Math.PI;
      standard.aoMap = null;
      standard.envMapIntensity = 0; // Licht steckt komplett in der Lightmap
      standard.userData.baked = true;
      standard.onBeforeCompile = (shader) => {
        shader.uniforms.lumiraKey = { value: info.key };
        shader.fragmentShader = `uniform float lumiraKey;\n${shader.fragmentShader}`.replace(
          "#include <lights_fragment_maps>",
          patched,
        );
      };
      standard.customProgramCacheKey = () => "lumira-lightmap";
      standard.needsUpdate = true;
    }
  });
  return done.size;
}
