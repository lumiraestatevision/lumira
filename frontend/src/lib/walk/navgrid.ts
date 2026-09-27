import * as THREE from "three";

// Begehbarkeit aus dem 3D-Modell – einmal nach dem Laden berechnet, danach nur Nachschlagen.
//
// Raster 5 cm in der Grundfläche (x/z, y = oben):
//   • Laufflächen: waagerechte, nach oben zeigende Flächen der Raumhülle (Boden, Stufen,
//     Deckenplatte an der Treppe) – bis zu LEVELS Höhen je Zelle
//   • Hindernisse: alles Senkrechte (Wände, Türblätter, Glas, Treppenwangen) sowie Möbel und
//     Einbauten komplett – je Zelle das belegte Höhenintervall. Lose Möbel in eigener Ebene,
//     damit „Möbel ausblenden“ auch den Weg freimacht.
// Gehen: Stufen bis STEP_UP hinauf, bis MAX_DROP hinab (keine Absturzkanten), Körper frei
// zwischen Knie- und Kopfhöhe im Radius BODY_RADIUS.

export const CELL = 0.05;
export const EYE_HEIGHT = 1.62;
export const BODY_RADIUS = 0.22;
const LEVELS = 4;
const STEP_UP = 0.3;
const MAX_DROP = 0.6;
const KNEE = 0.35;
const HEAD = 1.8;
const MARGIN = 1;

export type Kind = "shell" | "fixture" | "loose";

export interface Pose {
  x: number;
  z: number;
  feet: number;
}

export function kindOf(mesh: THREE.Object3D): Kind {
  const materials = (mesh as THREE.Mesh).material;
  const names = (Array.isArray(materials) ? materials : [materials]).map((m) => m?.name ?? "");
  if (names.some((n) => n.startsWith("Moebel:"))) return "loose";
  for (let node: THREE.Object3D | null = mesh; node; node = node.parent) {
    if (node.name.startsWith("Moebel_")) return "loose";
    if (node.name.startsWith("Ausstattung_")) return "fixture";
  }
  return "shell";
}

/** Belegte Höhenbereiche je Zelle – mehrere getrennt, sonst wird aus Stufe (0–0,17 m) und
 *  Deckenkante darüber (2,50–2,75 m) eine Wand von 0 bis 2,75 m. */
const SPANS = 3;

class Intervals {
  private readonly lo: Float32Array;
  private readonly hi: Float32Array;
  constructor(size: number) {
    this.lo = new Float32Array(size * SPANS).fill(Infinity);
    this.hi = new Float32Array(size * SPANS).fill(-Infinity);
  }
  add(cell: number, lo: number, hi: number) {
    const base = cell * SPANS;
    // mit überlappenden/angrenzenden Bereichen verschmelzen, sonst freien Platz nehmen
    for (let k = 0; k < SPANS; k++) {
      const i = base + k;
      if (this.lo[i] === Infinity || (this.lo[i] <= hi + 0.05 && this.hi[i] >= lo - 0.05)) {
        this.lo[i] = Math.min(this.lo[i], lo);
        this.hi[i] = Math.max(this.hi[i], hi);
        return;
      }
    }
    // alles belegt: in den nächstgelegenen Bereich aufnehmen (sicher: eher zu viel blockiert)
    let nearest = base;
    let gap = Infinity;
    for (let k = 0; k < SPANS; k++) {
      const i = base + k;
      const d = Math.max(this.lo[i] - hi, lo - this.hi[i]);
      if (d < gap) {
        gap = d;
        nearest = i;
      }
    }
    this.lo[nearest] = Math.min(this.lo[nearest], lo);
    this.hi[nearest] = Math.max(this.hi[nearest], hi);
  }
  hits(cell: number, lo: number, hi: number) {
    const base = cell * SPANS;
    for (let k = 0; k < SPANS; k++) {
      if (this.lo[base + k] <= hi && this.hi[base + k] >= lo) return true;
    }
    return false;
  }
}

export class NavGrid {
  furniture = true;
  private readonly levels: Float32Array;
  private readonly fixed: Intervals;
  private readonly loose: Intervals;

  private constructor(
    readonly minX: number,
    readonly minZ: number,
    readonly cols: number,
    readonly rows: number,
  ) {
    const size = cols * rows;
    this.levels = new Float32Array(size * LEVELS).fill(Number.NaN);
    this.fixed = new Intervals(size);
    this.loose = new Intervals(size);
  }

  static fromScene(root: THREE.Object3D): NavGrid {
    root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(root);
    const cols = Math.ceil((box.max.x - box.min.x + 2 * MARGIN) / CELL);
    const rows = Math.ceil((box.max.z - box.min.z + 2 * MARGIN) / CELL);
    const grid = new NavGrid(box.min.x - MARGIN, box.min.z - MARGIN, cols, rows);
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    const normal = new THREE.Vector3();
    root.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      const kind = kindOf(mesh);
      const position = mesh.geometry.getAttribute("position");
      const index = mesh.geometry.getIndex();
      const count = index ? index.count : position.count;
      for (let i = 0; i < count; i += 3) {
        const ia = index ? index.getX(i) : i;
        const ib = index ? index.getX(i + 1) : i + 1;
        const ic = index ? index.getX(i + 2) : i + 2;
        a.fromBufferAttribute(position, ia).applyMatrix4(mesh.matrixWorld);
        b.fromBufferAttribute(position, ib).applyMatrix4(mesh.matrixWorld);
        c.fromBufferAttribute(position, ic).applyMatrix4(mesh.matrixWorld);
        normal.subVectors(c, b).cross(a.clone().sub(b)).normalize();
        grid.addTriangle(a, b, c, normal.y, kind);
      }
    });
    return grid;
  }

  // ---------------------------------------------------------------- Aufbau
  private cellOf(x: number, z: number): number {
    const col = Math.floor((x - this.minX) / CELL);
    const row = Math.floor((z - this.minZ) / CELL);
    if (col < 0 || row < 0 || col >= this.cols || row >= this.rows) return -1;
    return row * this.cols + col;
  }

  private addLevel(cell: number, height: number) {
    const base = cell * LEVELS;
    for (let k = 0; k < LEVELS; k++) {
      const existing = this.levels[base + k];
      if (Number.isNaN(existing)) {
        this.levels[base + k] = height;
        return;
      }
      if (Math.abs(existing - height) < 0.03) {
        this.levels[base + k] = Math.max(existing, height);
        return;
      }
    }
  }

  private addTriangle(a: THREE.Vector3, b: THREE.Vector3, c: THREE.Vector3, ny: number, kind: Kind) {
    const lo = Math.min(a.y, b.y, c.y);
    const hi = Math.max(a.y, b.y, c.y);
    const target = kind === "loose" ? this.loose : this.fixed;
    if (Math.abs(ny) < 0.3) {
      // senkrecht: Grundriss ist eine Linie – die längste Kante des Dreiecks
      const edges: [THREE.Vector3, THREE.Vector3][] = [[a, b], [b, c], [c, a]];
      const [p, q] = edges.reduce((best, e) =>
        Math.hypot(e[0].x - e[1].x, e[0].z - e[1].z) > Math.hypot(best[0].x - best[1].x, best[0].z - best[1].z)
          ? e
          : best,
      );
      const steps = Math.max(1, Math.ceil(Math.hypot(q.x - p.x, q.z - p.z) / (CELL / 2)));
      for (let s = 0; s <= steps; s++) {
        const t = s / steps;
        const cell = this.cellOf(p.x + (q.x - p.x) * t, p.z + (q.z - p.z) * t);
        if (cell >= 0) target.add(cell, lo, hi);
      }
      return;
    }
    // Waagerechte Flächen der Raumhülle sind Laufflächen – unabhängig vom Umlaufsinn (ältere
    // Modelle haben nach unten zeigende Böden). Decken-Unterseiten stören nicht: von unten
    // unerreichbar hoch, von oben (Treppenaustritt) wie ein Geschossboden.
    const walkable = Math.abs(ny) > 0.85 && kind === "shell";
    // Fläche: alle Zellmittelpunkte im Dreieck (Grundriss-Projektion)
    const minCol = Math.floor((Math.min(a.x, b.x, c.x) - this.minX) / CELL);
    const maxCol = Math.floor((Math.max(a.x, b.x, c.x) - this.minX) / CELL);
    const minRow = Math.floor((Math.min(a.z, b.z, c.z) - this.minZ) / CELL);
    const maxRow = Math.floor((Math.max(a.z, b.z, c.z) - this.minZ) / CELL);
    const det = (b.z - c.z) * (a.x - c.x) + (c.x - b.x) * (a.z - c.z);
    if (Math.abs(det) < 1e-9) return;
    for (let row = Math.max(minRow, 0); row <= Math.min(maxRow, this.rows - 1); row++) {
      const z = this.minZ + (row + 0.5) * CELL;
      for (let col = Math.max(minCol, 0); col <= Math.min(maxCol, this.cols - 1); col++) {
        const x = this.minX + (col + 0.5) * CELL;
        const l1 = ((b.z - c.z) * (x - c.x) + (c.x - b.x) * (z - c.z)) / det;
        const l2 = ((c.z - a.z) * (x - c.x) + (a.x - c.x) * (z - c.z)) / det;
        const l3 = 1 - l1 - l2;
        if (l1 < -1e-6 || l2 < -1e-6 || l3 < -1e-6) continue;
        const cell = row * this.cols + col;
        const y = l1 * a.y + l2 * b.y + l3 * c.y;
        if (walkable) this.addLevel(cell, y);
        else target.add(cell, Math.min(y, lo), Math.max(y, hi));
      }
    }
  }

  // ---------------------------------------------------------------- Abfragen
  /** Höchste Lauffläche, die man von `feet` aus erreicht (Stufe hinauf/hinab) – sonst null. */
  ground(x: number, z: number, feet: number): number | null {
    const cell = this.cellOf(x, z);
    if (cell < 0) return null;
    let best = -Infinity;
    for (let k = 0; k < LEVELS; k++) {
      const h = this.levels[cell * LEVELS + k];
      if (!Number.isNaN(h) && h <= feet + STEP_UP && h > best) best = h;
    }
    return best >= feet - MAX_DROP ? best : null;
  }

  /** Körper (Knie bis Kopf) an dieser Stelle frei? */
  private free(x: number, z: number, g: number): boolean {
    const cell = this.cellOf(x, z);
    if (cell < 0) return false;
    const lo = g + KNEE;
    const hi = g + HEAD;
    if (this.fixed.hits(cell, lo, hi)) return false;
    return !(this.furniture && this.loose.hits(cell, lo, hi));
  }

  /** Standhöhe, wenn man hier stehen kann (Boden erreichbar, Körperumfang frei). */
  stand(x: number, z: number, feet: number): number | null {
    const g = this.ground(x, z, feet);
    if (g === null || !this.free(x, z, g)) return null;
    for (let k = 0; k < 8; k++) {
      const angle = (k / 8) * Math.PI * 2;
      if (!this.free(x + Math.cos(angle) * BODY_RADIUS, z + Math.sin(angle) * BODY_RADIUS, g)) {
        return null;
      }
    }
    return g;
  }

  /** Bewegen mit Gleiten an Hindernissen; liefert die neue Position (Füße auf Bodenhöhe). */
  move(pose: Pose, dx: number, dz: number): Pose {
    for (const [mx, mz] of [
      [dx, dz],
      [dx, 0],
      [0, dz],
    ]) {
      if (mx === 0 && mz === 0) continue;
      const g = this.stand(pose.x + mx, pose.z + mz, pose.feet);
      if (g !== null) return { x: pose.x + mx, z: pose.z + mz, feet: g };
    }
    return pose;
  }

  /** Teleport-Ziel entlang eines Strahls: erste Lauffläche, auf der man stehen kann; Wände
   *  halten den Strahl auf. */
  raycast(origin: THREE.Vector3, direction: THREE.Vector3, maxDistance = 12): Pose | null {
    const step = CELL;
    let previousY = origin.y;
    for (let t = step; t <= maxDistance; t += step) {
      const x = origin.x + direction.x * t;
      const y = origin.y + direction.y * t;
      const z = origin.z + direction.z * t;
      const cell = this.cellOf(x, z);
      if (cell < 0) return null;
      if (this.fixed.hits(cell, Math.min(y, previousY), Math.max(y, previousY))) return null;
      for (let k = 0; k < LEVELS; k++) {
        const h = this.levels[cell * LEVELS + k];
        if (!Number.isNaN(h) && previousY >= h && y <= h + 0.02) {
          const g = this.stand(x, z, h);
          return g === null ? null : { x, z, feet: g };
        }
      }
      previousY = y;
    }
    return null;
  }

  /** Startpunkt: freiester Punkt im Erdgeschoss, Blick in die längste freie Richtung. */
  spawn(): Pose & { yaw: number } {
    const size = this.cols * this.rows;
    const distance = new Int32Array(size).fill(-1);
    const queue = new Int32Array(size);
    let head = 0;
    let tail = 0;
    for (let cell = 0; cell < size; cell++) {
      const g = this.levelNear(cell, 0);
      const x = this.minX + ((cell % this.cols) + 0.5) * CELL;
      const z = this.minZ + (Math.floor(cell / this.cols) + 0.5) * CELL;
      if (g === null || !this.free(x, z, g)) {
        distance[cell] = 0;
        queue[tail++] = cell;
      }
    }
    while (head < tail) {
      const cell = queue[head++];
      const col = cell % this.cols;
      for (const next of [cell - 1, cell + 1, cell - this.cols, cell + this.cols]) {
        if (next < 0 || next >= size || distance[next] >= 0) continue;
        if ((next === cell - 1 && col === 0) || (next === cell + 1 && col === this.cols - 1)) continue;
        distance[next] = distance[cell] + 1;
        queue[tail++] = next;
      }
    }
    let best = 0;
    for (let cell = 1; cell < size; cell++) if (distance[cell] > distance[best]) best = cell;
    const x = this.minX + ((best % this.cols) + 0.5) * CELL;
    const z = this.minZ + (Math.floor(best / this.cols) + 0.5) * CELL;
    const feet = this.levelNear(best, 0) ?? 0;
    let yaw = 0;
    let longest = -1;
    for (let k = 0; k < 16; k++) {
      const angle = (k / 16) * Math.PI * 2;
      let reach = 0;
      while (reach < 15 && this.stand(x - Math.sin(angle) * reach, z - Math.cos(angle) * reach, feet) !== null) {
        reach += 0.1;
      }
      if (reach > longest) {
        longest = reach;
        yaw = angle;
      }
    }
    return { x, z, feet, yaw };
  }

  private levelNear(cell: number, height: number): number | null {
    for (let k = 0; k < LEVELS; k++) {
      const h = this.levels[cell * LEVELS + k];
      if (!Number.isNaN(h) && Math.abs(h - height) < 0.1) return h;
    }
    return null;
  }
}
