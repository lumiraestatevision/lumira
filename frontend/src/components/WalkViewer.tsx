"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { applyLightmaps, lightmapInfo } from "@/lib/walk/lightmap";
import { EYE_HEIGHT, NavGrid, type Pose, kindOf } from "@/lib/walk/navgrid";
import { Variants } from "@/lib/walk/variants";

// Begehbares Modell: im Browser frei durch das Haus gehen – am PC (Maus + WASD), am Handy
// (links ziehen = gehen, rechts ziehen = umsehen) und mit VR-Brille (WebXR: Teleport per
// Abzug, linker Stick gehen, rechter Stick drehen). Licht kommt aus der eingebrannten
// Lightmap, lose Möbel lassen sich ausblenden (auch für die Kollision).

const WALK_SPEED = 1.4; // m/s
const RUN_SPEED = 3.0;
const LOOK_SPEED = 0.0022; // rad pro Pixel
const SNAP_TURN = Math.PI / 6;
const MAX_STEP = 0.05; // Kollision in kleinen Schritten prüfen
const EXPOSURE = 0.8;

interface Runtime {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  player: THREE.Group;
  marker: THREE.Mesh;
  grid: NavGrid | null;
  pose: Pose;
  feet: number; // geglättet (Treppen)
  yaw: number;
  pitch: number;
  keys: Set<string>;
  touchMove: THREE.Vector2;
  furniture: THREE.Object3D[];
  turnCooldown: number;
  aiming: Map<THREE.Object3D, Pose | null>;
  vrSync: boolean; // erster VR-Frame: Kopf auf die Position setzen, nicht dorthin gehen
  variants: Variants | null;
}

type TouchRole = { role: "move" | "look"; x: number; y: number; startX: number; startY: number };

export default function WalkViewer({ src, onClose }: { src: string; onClose: () => void }) {
  const container = useRef<HTMLDivElement | null>(null);
  const runtime = useRef<Runtime | null>(null);
  const [progress, setProgress] = useState(0);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [furniture, setFurniture] = useState(true);
  const [hasFurniture, setHasFurniture] = useState(false);
  const [vrSupported, setVrSupported] = useState(false);
  const [inVr, setInVr] = useState(false);
  const [variantNames, setVariantNames] = useState<string[]>([]);
  const [variant, setVariant] = useState("");

  useEffect(() => {
    const host = container.current;
    if (!host) return;
    let disposed = false;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(host.clientWidth, host.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // Khronos PBR Neutral: farbtreu (Holz bleibt warm, Weiß bleibt weiß) – AgX wirkt hier fahl
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = EXPOSURE;
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType("local-floor");
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#dfe7f0"); // Himmel hinter den Fenstern
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = environment; // nur für Möbel/Türen – die Raumhülle hat die Lightmap

    const camera = new THREE.PerspectiveCamera(70, host.clientWidth / host.clientHeight, 0.05, 300);
    camera.rotation.order = "YXZ";
    camera.position.set(0, EYE_HEIGHT, 0);
    const player = new THREE.Group();
    player.add(camera);
    scene.add(player);

    const marker = new THREE.Mesh(
      new THREE.RingGeometry(0.18, 0.25, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: "#2f9e7a", transparent: true, opacity: 0.85 }),
    );
    marker.visible = false;
    scene.add(marker);

    const rt: Runtime = {
      renderer,
      scene,
      camera,
      player,
      marker,
      grid: null,
      pose: { x: 0, z: 0, feet: 0 },
      feet: 0,
      yaw: 0,
      pitch: 0,
      keys: new Set(),
      touchMove: new THREE.Vector2(),
      furniture: [],
      turnCooldown: 0,
      aiming: new Map(),
      vrSync: false,
      variants: null,
    };
    runtime.current = rt;
    if (process.env.NODE_ENV === "development") {
      (window as unknown as { lumiraWalk?: Runtime }).lumiraWalk = rt; // Fehlersuche im Browser
    }

    // ------------------------------------------------------------ Modell laden
    new GLTFLoader().load(
      src,
      (gltf) => {
        if (disposed) return;
        const root = gltf.scene;
        const info = lightmapInfo(root);
        if (info) applyLightmaps(root, info);
        root.traverse((object) => {
          if ((object as THREE.Mesh).isMesh && kindOf(object) === "loose") rt.furniture.push(object);
        });
        scene.add(root);
        rt.variants = new Variants(gltf, info);
        setVariantNames(rt.variants.names);
        setVariant(rt.variants.names[0] ?? "");
        const grid = NavGrid.fromScene(root);
        const start = grid.spawn();
        rt.grid = grid;
        rt.pose = { x: start.x, z: start.z, feet: start.feet };
        rt.feet = start.feet;
        rt.yaw = start.yaw;
        setHasFurniture(rt.furniture.length > 0);
        setReady(true);
      },
      (event) => event.total && setProgress(event.loaded / event.total),
      () => setError("Das Modell konnte nicht geladen werden."),
    );

    // ------------------------------------------------------------ Steuerung PC
    const onKey = (event: KeyboardEvent) => {
      if (event.type === "keydown") rt.keys.add(event.code);
      else rt.keys.delete(event.code);
    };
    const onMouse = (event: MouseEvent) => {
      if (document.pointerLockElement !== renderer.domElement) return;
      rt.yaw -= event.movementX * LOOK_SPEED;
      rt.pitch = THREE.MathUtils.clamp(rt.pitch - event.movementY * LOOK_SPEED, -1.4, 1.4);
    };
    const onLock = () => {
      const isLocked = document.pointerLockElement === renderer.domElement;
      setLocked(isLocked);
      if (!isLocked) rt.keys.clear();
    };
    const onClick = () => {
      if (!renderer.xr.isPresenting) void renderer.domElement.requestPointerLock();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    document.addEventListener("mousemove", onMouse);
    document.addEventListener("pointerlockchange", onLock);
    renderer.domElement.addEventListener("click", onClick);

    // ------------------------------------------------------------ Steuerung Touch
    const touches = new Map<number, TouchRole>();
    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType !== "touch") return;
      const role = event.clientX < host.clientWidth / 2 ? "move" : "look";
      touches.set(event.pointerId, { role, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY });
    };
    const onPointerMove = (event: PointerEvent) => {
      const touch = touches.get(event.pointerId);
      if (!touch) return;
      if (touch.role === "look") {
        rt.yaw -= (event.clientX - touch.x) * LOOK_SPEED * 1.5;
        rt.pitch = THREE.MathUtils.clamp(rt.pitch - (event.clientY - touch.y) * LOOK_SPEED * 1.5, -1.4, 1.4);
      } else {
        const radius = 60;
        rt.touchMove.set(
          THREE.MathUtils.clamp((event.clientX - touch.startX) / radius, -1, 1),
          THREE.MathUtils.clamp((event.clientY - touch.startY) / radius, -1, 1),
        );
      }
      touch.x = event.clientX;
      touch.y = event.clientY;
    };
    const onPointerUp = (event: PointerEvent) => {
      if (touches.get(event.pointerId)?.role === "move") rt.touchMove.set(0, 0);
      touches.delete(event.pointerId);
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerup", onPointerUp);
    renderer.domElement.addEventListener("pointercancel", onPointerUp);

    // ------------------------------------------------------------ VR-Controller
    const rayGeometry = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, -4),
    ]);
    const rayMaterial = new THREE.LineBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.6 });
    for (const index of [0, 1]) {
      const controller = renderer.xr.getController(index);
      controller.add(new THREE.Line(rayGeometry, rayMaterial));
      controller.addEventListener("selectstart", () => rt.aiming.set(controller, null));
      controller.addEventListener("selectend", () => {
        const target = rt.aiming.get(controller);
        rt.aiming.delete(controller);
        if (target) teleport(rt, target);
      });
      player.add(controller);
    }
    renderer.xr.addEventListener("sessionstart", () => {
      setInVr(true);
      camera.position.set(0, 0, 0); // Kopfhöhe kommt jetzt von der Brille
      rt.vrSync = true;
    });
    renderer.xr.addEventListener("sessionend", () => {
      setInVr(false);
      camera.position.set(0, EYE_HEIGHT, 0);
      camera.rotation.set(rt.pitch, 0, 0);
      marker.visible = false;
    });
    void navigator.xr?.isSessionSupported("immersive-vr").then((ok) => !disposed && setVrSupported(ok));

    // ------------------------------------------------------------ Schleife
    const clock = new THREE.Clock();
    renderer.setAnimationLoop(() => {
      const dt = Math.min(clock.getDelta(), 0.1);
      if (rt.grid) {
        if (renderer.xr.isPresenting) tickVr(rt, dt);
        else tickDesktop(rt, dt);
      }
      renderer.render(scene, camera);
    });

    const resize = new ResizeObserver(() => {
      if (renderer.xr.isPresenting) return;
      renderer.setSize(host.clientWidth, host.clientHeight);
      camera.aspect = host.clientWidth / host.clientHeight;
      camera.updateProjectionMatrix();
    });
    resize.observe(host);

    return () => {
      disposed = true;
      resize.disconnect();
      renderer.setAnimationLoop(null);
      void renderer.xr.getSession()?.end();
      if (document.pointerLockElement === renderer.domElement) document.exitPointerLock();
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      document.removeEventListener("mousemove", onMouse);
      document.removeEventListener("pointerlockchange", onLock);
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (!mesh.isMesh) return;
        mesh.geometry.dispose();
        for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
          for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
          material.dispose();
        }
      });
      environment.dispose();
      pmrem.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      runtime.current = null;
    };
  }, [src]);

  // Esc beendet die Mausführung (Browser); ein zweites Esc schließt den Rundgang.
  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.code === "Escape" && !document.pointerLockElement) onClose();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [onClose]);

  const toggleFurniture = useCallback(() => {
    const rt = runtime.current;
    if (!rt) return;
    const next = !furniture;
    for (const object of rt.furniture) object.visible = next;
    if (rt.grid) rt.grid.furniture = next;
    setFurniture(next);
  }, [furniture]);

  const chooseVariant = useCallback((name: string) => {
    setVariant(name);
    void runtime.current?.variants?.select(name);
  }, []);

  const enterVr = useCallback(async () => {
    const rt = runtime.current;
    if (!rt || !navigator.xr) return;
    const session = await navigator.xr.requestSession("immersive-vr", {
      optionalFeatures: ["local-floor", "bounded-floor"],
    });
    await rt.renderer.xr.setSession(session);
  }, []);

  return (
    <div className="walk">
      <div className="walk-canvas" ref={container} />
      {!ready && !error && (
        <div className="walk-overlay">
          <p>Rundgang wird vorbereitet … {Math.round(progress * 100)} %</p>
        </div>
      )}
      {error && (
        <div className="walk-overlay">
          <p>{error}</p>
        </div>
      )}
      {ready && !locked && !inVr && (
        <div className="walk-hint">
          <strong>Klicken zum Umsehen</strong>
          <span>W A S D oder Pfeiltasten gehen · Shift schneller · Esc beendet</span>
          <span>Handy: links ziehen = gehen, rechts ziehen = umsehen</span>
        </div>
      )}
      <div className="walk-controls">
        {variantNames.length > 1 && (
          <select
            className="variant"
            aria-label="Ausstattungsvariante"
            value={variant}
            onChange={(event) => chooseVariant(event.target.value)}
          >
            {variantNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}
        {hasFurniture && (
          <button type="button" className="toggle" aria-pressed={furniture} onClick={toggleFurniture}>
            {furniture ? "Möbel ausblenden" : "Möbel einblenden"}
          </button>
        )}
        {vrSupported && ready && (
          <button type="button" className="toggle" onClick={() => void enterVr()}>
            VR starten
          </button>
        )}
        <button type="button" className="secondary" onClick={onClose}>
          Schließen
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Bewegung
function walk(rt: Runtime, dx: number, dz: number) {
  const grid = rt.grid;
  if (!grid) return;
  const steps = Math.max(1, Math.ceil(Math.hypot(dx, dz) / MAX_STEP));
  for (let i = 0; i < steps; i++) rt.pose = grid.move(rt.pose, dx / steps, dz / steps);
}

function tickDesktop(rt: Runtime, dt: number) {
  const { keys } = rt;
  let forward = 0;
  let right = 0;
  if (keys.has("KeyW") || keys.has("ArrowUp")) forward += 1;
  if (keys.has("KeyS") || keys.has("ArrowDown")) forward -= 1;
  if (keys.has("KeyD") || keys.has("ArrowRight")) right += 1;
  if (keys.has("KeyA") || keys.has("ArrowLeft")) right -= 1;
  forward -= rt.touchMove.y;
  right += rt.touchMove.x;
  const length = Math.hypot(forward, right);
  if (length > 0) {
    const speed = (keys.has("ShiftLeft") || keys.has("ShiftRight") ? RUN_SPEED : WALK_SPEED) * dt;
    const f = (forward / Math.max(length, 1)) * speed;
    const r = (right / Math.max(length, 1)) * speed;
    const sin = Math.sin(rt.yaw);
    const cos = Math.cos(rt.yaw);
    walk(rt, -sin * f + cos * r, -cos * f - sin * r);
  }
  rt.feet += (rt.pose.feet - rt.feet) * Math.min(1, dt * 12);
  rt.player.position.set(rt.pose.x, rt.feet, rt.pose.z);
  rt.player.rotation.set(0, rt.yaw, 0);
  rt.camera.rotation.set(rt.pitch, 0, 0);
}

const head = new THREE.Vector3();
const direction = new THREE.Vector3();
const origin = new THREE.Vector3();

function tickVr(rt: Runtime, dt: number) {
  const session = rt.renderer.xr.getSession();
  rt.turnCooldown = Math.max(0, rt.turnCooldown - dt);
  // Echte Schritte im Raum zählen als Bewegung – mit Kollision (vor Wänden bleibt man stehen)
  rt.camera.getWorldPosition(head);
  if (rt.vrSync) rt.vrSync = false;
  else walk(rt, head.x - rt.pose.x, head.z - rt.pose.z);
  for (const source of session?.inputSources ?? []) {
    const axes = source.gamepad?.axes;
    if (!axes || axes.length < 4) continue;
    const [x, y] = [axes[2], axes[3]];
    if (source.handedness === "left" && Math.hypot(x, y) > 0.2) {
      // gehen in Blickrichtung
      rt.camera.getWorldDirection(direction);
      direction.y = 0;
      direction.normalize();
      const speed = WALK_SPEED * dt;
      walk(rt, (-y * direction.x - x * direction.z) * speed, (-y * direction.z + x * direction.x) * speed);
    }
    if (source.handedness === "right" && Math.abs(x) > 0.7 && rt.turnCooldown === 0) {
      turnAroundHead(rt, x > 0 ? -SNAP_TURN : SNAP_TURN);
      rt.turnCooldown = 0.35;
    }
  }
  // Teleport zielen: Strahl des Controllers gegen die Laufflächen
  rt.marker.visible = false;
  for (const controller of rt.aiming.keys()) {
    controller.getWorldPosition(origin);
    direction.set(0, 0, -1).applyQuaternion(controller.getWorldQuaternion(new THREE.Quaternion()));
    const target = rt.grid?.raycast(origin, direction) ?? null;
    rt.aiming.set(controller, target);
    if (target) {
      rt.marker.position.set(target.x, target.feet + 0.01, target.z);
      rt.marker.visible = true;
    }
  }
  rt.feet += (rt.pose.feet - rt.feet) * Math.min(1, dt * 12);
  // Spielerursprung so setzen, dass der Kopf (nicht der Raumursprung) an der Pose steht
  rt.camera.getWorldPosition(head);
  rt.player.position.x += rt.pose.x - head.x;
  rt.player.position.z += rt.pose.z - head.z;
  rt.player.position.y = rt.feet;
}

function turnAroundHead(rt: Runtime, angle: number) {
  rt.camera.getWorldPosition(head);
  rt.player.rotation.y += angle;
  rt.player.updateMatrixWorld(true);
  const after = new THREE.Vector3();
  rt.camera.getWorldPosition(after);
  rt.player.position.x += head.x - after.x;
  rt.player.position.z += head.z - after.z;
}

function teleport(rt: Runtime, target: Pose) {
  rt.pose = target;
  rt.feet = target.feet;
  rt.vrSync = true; // Sprung, kein Weg dorthin
}
