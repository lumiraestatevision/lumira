"""Baut aus scene.json ein 3D-Modell und exportiert FBX + glTF (GLB).

Läuft INNERHALB von Blender (eigenes Python, bpy eingebaut) – nicht mit dem Service-Python:

    blender --background --factory-startup --python-exit-code 1 \\
        --python build_scene.py -- --spec scene.json --fbx model.fbx --glb model.glb \\
        [--textures /pfad/zu/assets/textures]

Umfang:
- Wände aus exaktem Grundriss (CAD-Fläche, als Prisma) oder als Quader aus Achse und Dicke;
  Öffnungen als Wandstück mit ausgeschnittener Tür/Fenster (bleiben: Sturz, Brüstung)
- Oberflächen mit realem Maßstab (UV in Metern): Holzböden als Fototextur (Poly Haven, CC0),
  Fliesen im LV-Format mit Fuge, Putz mit feiner Struktur, Teppich – erzeugt mit numpy
- Türen: Zarge + geöffnetes Türblatt (begehbar); Fenster: Rahmen, Pfosten, Glas
- Wandkronen dunkel wie im Architekturmodell
- Decken + Deckenleuchte je Raum (Materialien „Decke“/„Leuchte“ – im Viewer ausblendbar)
- Einrichtung aus ``fixtures`` (lumira_generator.logic.furnish): Küche und Sanitär fest,
  lose Möbel mit Materialnamen „Moebel: …“ (der Viewer blendet sie auf Knopfdruck aus);
  Pflanze als Modell (Poly Haven, CC0), alles andere modern und schlicht selbst gebaut
STUB: keine Lichtberechnung (Baking), Treppen, Texturen auf Möbeln – folgen.
"""

import argparse
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

MM = 0.001  # Szene in Metern, Eingabe in Millimetern
EXPORT_TEXTURE_PX = 1024  # Texturen im GLB (Browser/VR-Brille) – Quelle ist 2K
WALL_CROWN_COLOR = "#4A4A4A"  # Wandkrone dunkel wie im Architekturmodell
GLASS_COLOR = "#C9DCE3"
DOOR_LEAF_MM = 40.0
DOOR_FRAME_MM = 30.0
WINDOW_PROFILE_MM = 70.0
WINDOW_DEPTH_MM = 80.0

_materials = {}
_images = {}
TEXTURE_DIR = None
MODEL_DIR = None


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--glb", required=True)
    parser.add_argument("--textures", default=None)
    parser.add_argument("--models", default=None)
    return parser.parse_args(argv)


# ------------------------------------------------------------------ Farben und Bilder
def srgb_to_linear(channel):
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def hex_to_srgb(hex_color):
    value = (hex_color or "#CCCCCC").lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


def hex_to_rgba(hex_color):
    r, g, b = hex_to_srgb(hex_color)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def _periodic_noise(size, scale, seed):
    """Kachelbares Rauschen (FFT-Tiefpass) in 0..1 – nahtlos wiederholbar."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(size)
    fy = np.fft.fftfreq(size[0])[:, None]
    fx = np.fft.fftfreq(size[1])[None, :]
    spectrum = np.fft.fft2(white) * np.exp(-((fx**2 + fy**2) * (scale**2)))
    noise = np.real(np.fft.ifft2(spectrum))
    noise -= noise.min()
    return noise / max(float(noise.max()), 1e-9)


def _normal_from_height(height, strength):
    """Höhenkarte → Tangenten-Normal-Map (0..1, OpenGL-Konvention wie glTF)."""
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * strength
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * strength
    normal = np.dstack((-dx, -dy, np.ones_like(height)))
    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
    return normal * 0.5 + 0.5


def _new_image(name, rgb, non_color=False):
    """numpy (H, W, 3) 0..1 → Blender-Bild (Zeile 0 = unten)."""
    h, w, _ = rgb.shape
    image = bpy.data.images.new(name, width=w, height=h, alpha=False)
    rgba = np.dstack((rgb, np.ones((h, w)))).astype(np.float32)
    image.pixels.foreach_set(rgba.ravel())
    if non_color:
        image.colorspace_settings.name = "Non-Color"
    image.pack()
    return image


def tile_images(color, tile_mm, grout_mm):
    """Eine Fliese samt halber Fuge ringsum: Farbe, Normal-Map (Fuge vertieft)."""
    key = ("tiles", color, tuple(tile_mm), grout_mm)
    if key in _images:
        return _images[key]
    width_mm, height_mm = tile_mm
    ppm = 512.0 / max(width_mm, height_mm)
    w, h = max(64, round(width_mm * ppm)), max(64, round(height_mm * ppm))
    half = max(1, round(grout_mm / 2 * ppm))
    base = np.array(hex_to_srgb(color))
    mottling = _periodic_noise((h, w), 12.0, seed=len(color)) - 0.5
    fine = np.random.default_rng(1).normal(0, 0.012, (h, w))
    shade = 1 + mottling * 0.06 + fine
    rgb = np.clip(base[None, None, :] * shade[:, :, None], 0, 1)
    grout_mask = np.zeros((h, w), dtype=bool)
    grout_mask[:half, :] = grout_mask[-half:, :] = True
    grout_mask[:, :half] = grout_mask[:, -half:] = True
    luminance = float(base @ np.array([0.299, 0.587, 0.114]))
    grout = np.full(3, 0.55 if luminance > 0.55 else min(0.85, luminance + 0.25))
    rgb[grout_mask] = grout
    height = np.where(grout_mask, 0.0, 1.0)
    normal = _normal_from_height(height, strength=2.0)
    images = (
        _new_image(f"Fliese_{color}_{width_mm:g}x{height_mm:g}", rgb),
        _new_image(f"Fliese_{color}_{width_mm:g}x{height_mm:g}_normal", normal, non_color=True),
    )
    _images[key] = images
    return images


def plaster_normal():
    """Feine Putzstruktur (Normal-Map), 512 px ≙ 0,5 m."""
    if "plaster" not in _images:
        height = (
            _periodic_noise((512, 512), 3.0, seed=7) * 0.6
            + _periodic_noise((512, 512), 1.0, seed=8) * 0.4
        )
        _images["plaster"] = _new_image("Putzstruktur", _normal_from_height(height, 1.2), True)
    return _images["plaster"]


def carpet_images(color):
    key = ("carpet", color)
    if key not in _images:
        fibres = _periodic_noise((512, 512), 0.8, seed=3)
        base = np.array(hex_to_srgb(color))
        rgb = np.clip(base[None, None, :] * (0.9 + fibres[:, :, None] * 0.2), 0, 1)
        _images[key] = (
            _new_image(f"Teppich_{color}", rgb),
            _new_image(f"Teppich_{color}_normal", _normal_from_height(fibres, 3.0), True),
        )
    return _images[key]


def texture_images(texture_id):
    """Fototextur aus assets/textures/<id>/ – None, wenn nicht heruntergeladen."""
    if texture_id in _images:
        return _images[texture_id]
    result = None
    folder = os.path.join(TEXTURE_DIR or "", texture_id)
    if TEXTURE_DIR and os.path.isdir(folder):
        files = {name.split("_2k")[0].rsplit("_", 1)[-1]: name for name in os.listdir(folder)}
        paths = {k: os.path.join(folder, files[k]) for k in ("diff", "gl", "rough") if k in files}
        if "diff" in paths:
            loaded = {}
            for kind, path in paths.items():
                image = bpy.data.images.load(path, check_existing=True)
                if kind != "diff":
                    image.colorspace_settings.name = "Non-Color"
                if max(image.size) > EXPORT_TEXTURE_PX:  # Browser/VR: 1K reicht, spart ~75 %
                    image.scale(EXPORT_TEXTURE_PX, EXPORT_TEXTURE_PX)
                loaded[kind] = image
            result = (loaded["diff"], loaded.get("gl"), loaded.get("rough"))
    _images[texture_id] = result
    return result


# ------------------------------------------------------------------ Materialien
def _principled(name, rgba, roughness):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = rgba
    return mat, bsdf


def _image_node(mat, image):
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    return node


def _normal_node(mat, bsdf, image, strength=1.0):
    if image is None:
        return
    links = mat.node_tree.links
    tex = _image_node(mat, image)
    normal = mat.node_tree.nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = strength
    links.new(tex.outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])


def material(name, hex_color, roughness=0.6):
    """Einfarbiges Material (Türen, Rahmen, Wandkrone, Rückfall)."""
    key = ("plain", name, hex_color, roughness)
    if key not in _materials:
        _materials[key] = _principled(name, hex_to_rgba(hex_color), roughness)[0]
    return _materials[key]


def glass_material():
    if "glass" not in _materials:
        mat, bsdf = _principled("Glas", hex_to_rgba(GLASS_COLOR), 0.05)
        bsdf.inputs["Alpha"].default_value = 0.25  # glTF: alphaMode BLEND
        mat.surface_render_method = "BLENDED"
        _materials["glass"] = mat
    return _materials["glass"]


def surface_material(surface):
    """Material aus der Oberflächenbeschreibung (siehe lumira_generator.logic.surfaces)."""
    key = json.dumps(surface, sort_keys=True)
    if key in _materials:
        return _materials[key]
    kind = surface.get("kind", "plain")
    name, color = surface["name"], surface["color"]
    links = None
    mat = None
    if kind == "texture" and texture_images(surface["texture"]):
        diff, normal, rough = texture_images(surface["texture"])
        mat, bsdf = _principled(name, hex_to_rgba(color), 0.45)
        links = mat.node_tree.links
        links.new(_image_node(mat, diff).outputs["Color"], bsdf.inputs["Base Color"])
        if rough is not None:
            links.new(_image_node(mat, rough).outputs["Color"], bsdf.inputs["Roughness"])
        _normal_node(mat, bsdf, normal)
    elif kind == "tiles":
        color_img, normal = tile_images(color, surface["tile_mm"], surface["grout_mm"])
        mat, bsdf = _principled(name, hex_to_rgba(color), 0.3)
        mat.node_tree.links.new(
            _image_node(mat, color_img).outputs["Color"], bsdf.inputs["Base Color"]
        )
        _normal_node(mat, bsdf, normal, strength=0.8)
    elif kind == "plaster":
        mat, bsdf = _principled(name, hex_to_rgba(color), 0.9)
        _normal_node(mat, bsdf, plaster_normal(), strength=0.35)
    elif kind == "carpet":
        color_img, normal = carpet_images(color)
        mat, bsdf = _principled(name, hex_to_rgba(color), 1.0)
        mat.node_tree.links.new(
            _image_node(mat, color_img).outputs["Color"], bsdf.inputs["Base Color"]
        )
        _normal_node(mat, bsdf, normal, strength=0.6)
    else:
        mat = _principled(name, hex_to_rgba(color), 0.7)[0]
    _materials[key] = mat
    return mat


def uv_size_m(surface):
    """Kantenlänge, die ein Texturdurchlauf in der Welt abdeckt (x, y) in Metern."""
    kind = surface.get("kind")
    if kind == "texture":
        size = surface["size_mm"] * MM
        return size, size
    if kind == "tiles":
        width, height = surface["tile_mm"]
        return width * MM, height * MM
    if kind == "carpet":
        return 0.4, 0.4
    return 0.5, 0.5  # Putz


# ------------------------------------------------------------------ Geometrie
def box(name, size, location, rotation_z):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.scale = size
    obj.location = location
    obj.rotation_euler = (0.0, 0.0, rotation_z)
    return obj


def bake_modifiers(obj):
    """Modifier anwenden ohne bpy.ops (funktioniert zuverlässig im Hintergrundmodus)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    obj.modifiers.clear()
    obj.data = mesh


def apply_transform(obj):
    """Skalierung/Drehung in die Punkte übernehmen – UVs und Normalen in Weltkoordinaten."""
    obj.data.transform(obj.matrix_basis)
    obj.matrix_basis.identity()


def box_uv(obj, size_xy, rotate=False):
    """Würfelprojektion in Weltmaßstab: jede Fläche projiziert auf ihre Hauptachse."""
    mesh = obj.data
    layer = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    su, sv = size_xy
    for polygon in mesh.polygons:
        axis = max(range(3), key=lambda i: abs(polygon.normal[i]))
        for loop_index in polygon.loop_indices:
            co = mesh.vertices[mesh.loops[loop_index].vertex_index].co
            if axis == 0:
                u, v = co.y, co.z
            elif axis == 1:
                u, v = co.x, co.z
            else:
                u, v = (co.y, co.x) if rotate else (co.x, co.y)
            layer.data[loop_index].uv = (u / su, v / sv)


def prism(name, footprint, height):
    """Senkrechtes Prisma aus einem Grundriss-Polygon (Wand mit Gehrung, L-Form …)."""
    points = [(x * MM, y * MM) for x, y in footprint]
    twice_area = sum(
        ax * by - bx * ay
        for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1], strict=True)
    )
    if twice_area < 0:
        points.reverse()  # gegen den Uhrzeigersinn → Deckfläche zeigt nach oben
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bottom = bm.faces.new([bm.verts.new((x, y, 0.0)) for x, y in points])
    extruded = bmesh.ops.extrude_face_region(bm, geom=[bottom])
    top_verts = [v for v in extruded["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0.0, 0.0, height), verts=top_verts)
    bottom.normal_flip()  # Bodenfläche zeigt nach unten
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def apply_wall_materials(obj, finish):
    """Wandseiten in der Wandoberfläche, die Oberseite (Wandkrone) dunkel: von oben ist der
    Grundriss so sofort lesbar – auch in Viewern ohne Schatten/Umgebungsverdeckung."""
    mesh = obj.data
    box_uv(obj, uv_size_m(finish))
    mesh.materials.append(surface_material(finish))
    mesh.materials.append(material("Wandkrone", WALL_CROWN_COLOR, roughness=0.9))
    top = max(v.co.z for v in mesh.vertices)
    for polygon in mesh.polygons:
        if polygon.normal.z > 0.99 and abs(polygon.center.z - top) < 1e-5:
            polygon.material_index = 1


def _part(name, size_m, centre, angle, mat):
    obj = box(name, size_m, centre, angle)
    obj.data.materials.append(mat)
    return obj


class WallFrame:
    """Lokales Koordinatensystem einer Wand: u entlang, n nach links, Maße in Metern."""

    def __init__(self, wall):
        (x1, y1), (x2, y2) = wall["start"], wall["end"]
        self.origin = (x1 * MM, y1 * MM)
        dx, dy = (x2 - x1) * MM, (y2 - y1) * MM
        self.length = math.hypot(dx, dy)
        self.angle = math.atan2(dy, dx)
        self.u = (math.cos(self.angle), math.sin(self.angle))
        self.n = (-self.u[1], self.u[0])
        self.thickness = wall["thickness"] * MM

    def point(self, along, across, z):
        return (
            self.origin[0] + self.u[0] * along + self.n[0] * across,
            self.origin[1] + self.u[1] * along + self.n[1] * across,
            z,
        )


def build_window(frame, opening, frame_mat):
    """Rahmen (Blendrahmen 70 mm), Pfosten bei breiten Fenstern, Glas – mittig in der Wand."""
    p, d = WINDOW_PROFILE_MM * MM, WINDOW_DEPTH_MM * MM
    start = opening["offset"] * MM
    width, height = opening["width"] * MM, opening["height"] * MM
    sill = opening["sill"] * MM
    mid = start + width / 2
    oid = opening["id"]
    parts = [
        ("L", (p, d, height), frame.point(start + p / 2, 0, sill + height / 2)),
        ("R", (p, d, height), frame.point(start + width - p / 2, 0, sill + height / 2)),
        ("U", (width, d, p), frame.point(mid, 0, sill + p / 2)),
        ("O", (width, d, p), frame.point(mid, 0, sill + height - p / 2)),
    ]
    if width > 1.3:  # zweiflügelig
        parts.append(("M", (p, d, height), frame.point(mid, 0, sill + height / 2)))
    for tag, size, centre in parts:
        _part(f"Fenster_{oid}_{tag}", size, centre, frame.angle, frame_mat)
    _part(
        f"Glas_{oid}",
        (width - 2 * p, 0.012, height - 2 * p),
        frame.point(mid, 0, sill + height / 2),
        frame.angle,
        glass_material(),
    )


def build_door(frame, opening, door_mat):
    """Zarge (Futter + Bekleidung vereinfacht) und um 90° geöffnetes Türblatt."""
    f, leaf = DOOR_FRAME_MM * MM, DOOR_LEAF_MM * MM
    start = opening["offset"] * MM
    width, height = opening["width"] * MM, opening["height"] * MM
    depth = frame.thickness + 0.02
    oid = opening["id"]
    for tag, along, size, z in (
        ("L", start + f / 2, (f, depth, height), height / 2),
        ("R", start + width - f / 2, (f, depth, height), height / 2),
        ("O", start + width / 2, (width, depth, f), height - f / 2),
    ):
        _part(f"Zarge_{oid}_{tag}", size, frame.point(along, 0, z), frame.angle, door_mat)

    leaf_width = width - 2 * f - 0.006
    leaf_height = height - f - 0.01
    hinge_at_start = opening.get("swing") != "right"
    side = -1.0 if opening.get("opens_to") == "right" else 1.0
    hinge_along = start + f + leaf / 2 if hinge_at_start else start + width - f - leaf / 2
    # Blatt steht senkrecht zur Wand und ragt ab der Wandoberfläche in den Raum.
    centre_across = side * (frame.thickness / 2 + leaf_width / 2)
    leaf_angle = frame.angle + math.pi / 2
    _part(
        f"Tuerblatt_{oid}",
        (leaf_width, leaf, leaf_height),
        frame.point(hinge_along, centre_across, 0.005 + leaf_height / 2),
        leaf_angle,
        door_mat,
    )


def build_wall(wall, scene_spec):
    finish = scene_spec["wall_finish"]
    if wall.get("footprint"):
        obj = prism(f"Wall_{wall['id']}", wall["footprint"], wall["height"] * MM)
        apply_wall_materials(obj, finish)
        return 0
    frame = WallFrame(wall)
    height = wall["height"] * MM
    centre = frame.point(frame.length / 2, 0, height / 2)
    obj = box(f"Wall_{wall['id']}", (frame.length, frame.thickness, height), centre, frame.angle)

    cutters = []
    for opening in wall["openings"]:
        width, op_height = opening["width"] * MM, opening["height"] * MM
        along = opening["offset"] * MM + width / 2
        # 2 mm breiter: füllt die Öffnung die ganze Wandlänge (CAD-Lücke zwischen zwei
        # Wandflächen), schneidet der Boolean sonst entlang deckungsgleicher Flächen.
        cutter = box(
            f"Cut_{opening['id']}",
            (width + 0.002, frame.thickness * 3, op_height),
            frame.point(along, 0, opening["sill"] * MM + op_height / 2),
            frame.angle,
        )
        modifier = obj.modifiers.new(name=f"cut_{opening['id']}", type="BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.object = cutter
        cutters.append(cutter)

    if cutters:
        bake_modifiers(obj)
        for cutter in cutters:
            bpy.data.objects.remove(cutter, do_unlink=True)
    apply_transform(obj)
    apply_wall_materials(obj, finish)

    window_mat = material("Fensterrahmen", scene_spec["window_frame"]["color"], roughness=0.35)
    door_mat = material("Tür", scene_spec["door_finish"]["color"], roughness=0.35)
    for opening in wall["openings"]:
        if opening["type"] == "window":
            build_window(frame, opening, window_mat)
        elif opening["type"] == "door":
            build_door(frame, opening, door_mat)
    return len(cutters)


def build_floor(room):
    mesh = bpy.data.meshes.new(f"Floor_{room['id']}")
    bm = bmesh.new()
    verts = [bm.verts.new((x * MM, y * MM, 0.001)) for x, y in room["polygon"]]
    face = bm.faces.new(verts)
    if face.normal.z < 0:
        face.normal_flip()
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(f"Floor_{room['id']}_{room['type']}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj["lumira_room_type"] = room["type"]
    obj["lumira_label"] = room.get("label") or ""
    floor = room["floor"]
    xs = [x for x, _ in room["polygon"]]
    ys = [y for _, y in room["polygon"]]
    # Dielen laufen in Längsrichtung des Raums (Textur: Dielen liegen waagerecht im Bild).
    rotate = floor.get("kind") == "texture" and (max(ys) - min(ys)) > (max(xs) - min(xs))
    box_uv(obj, uv_size_m(floor), rotate=rotate)
    obj.data.materials.append(surface_material(floor))


# ------------------------------------------------------------------ Decken und Licht
CEILING_COLOR = "#F7F6F3"
LAMP_COLOR = "#FFF4E2"


def emissive_material(name, hex_color, strength):
    key = ("emissive", name, hex_color, strength)
    if key not in _materials:
        mat, bsdf = _principled(name, hex_to_rgba(hex_color), 0.4)
        bsdf.inputs["Emission Color"].default_value = hex_to_rgba(hex_color)
        bsdf.inputs["Emission Strength"].default_value = strength
        _materials[key] = mat
    return _materials[key]


def _room_centre(polygon_m):
    """Mittelpunkt für die Deckenleuchte – bei L-förmigen Räumen der Mittelpunkt des Umrisses,
    falls der Schwerpunkt außerhalb liegt."""
    xs = [x for x, _ in polygon_m]
    ys = [y for _, y in polygon_m]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon_m, polygon_m[1:] + polygon_m[:1], strict=True):
        if (y1 > cy) != (y2 > cy) and cx < x1 + (cy - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return (cx, cy) if inside else ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def build_ceiling(room, height):
    """Decke (nach unten gerichtet) und eine Deckenleuchte je Raum. Im Viewer werden Decke und
    Leuchte in der Draufsicht ausgeblendet (Materialnamen „Decke“, „Leuchte“)."""
    polygon = [(x * MM, y * MM) for x, y in room["polygon"]]
    mesh = bpy.data.meshes.new(f"Ceiling_{room['id']}")
    bm = bmesh.new()
    face = bm.faces.new([bm.verts.new((x, y, height)) for x, y in polygon])
    if face.normal.z > 0:
        face.normal_flip()
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(f"Ceiling_{room['id']}", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(material("Decke", CEILING_COLOR, roughness=0.95))

    cx, cy = _room_centre(polygon)
    lamp = Builder()
    lamp.cylinder(0.18, 0.04, (0, 0, height - 0.02), emissive_material("Leuchte", LAMP_COLOR, 4.0))
    lamp.finish(f"Leuchte_{room['id']}", cx, cy, math.pi / 2)
    # Echte Lichtquelle für das spätere Einbrennen (wird nicht ins GLB exportiert)
    light = bpy.data.objects.new(f"Licht_{room['id']}", bpy.data.lights.new(room["id"], "POINT"))
    light.data.energy = 120.0
    light.data.shadow_soft_size = 0.2
    light.location = (cx, cy, height - 0.25)
    bpy.context.scene.collection.objects.link(light)


# ------------------------------------------------------------------ Einrichtung
class Builder:
    """Ein Möbelstück aus mehreren Quadern/Zylindern in einem Mesh (ein Objekt je Stück).
    Lokales System: x = Breite, y = Tiefe (+y = Front, zeigt in den Raum), z = Höhe,
    Ursprung Mitte Unterseite. Maße in Metern."""

    def __init__(self):
        self.bm = bmesh.new()
        self.mats = []

    def _index(self, mat):
        if mat not in self.mats:
            self.mats.append(mat)
        return self.mats.index(mat)

    def _assign(self, verts, mat):
        index = self._index(mat)
        for face in {f for v in verts for f in v.link_faces}:
            face.material_index = index

    def box(self, size, centre, mat, bevel=0.0):
        sx, sy, sz = (max(s, 0.001) for s in size)
        matrix = Matrix.Translation(centre) @ Matrix.Diagonal((sx, sy, sz, 1.0))
        verts = bmesh.ops.create_cube(self.bm, size=1.0, matrix=matrix)["verts"]
        self._assign(verts, mat)
        if bevel > 0:
            edges = list({e for v in verts for e in v.link_edges})
            bmesh.ops.bevel(
                self.bm,
                geom=edges,
                offset=min(bevel, min(sx, sy, sz) / 2.2),
                segments=3,
                profile=0.5,
                affect="EDGES",
            )

    def cylinder(self, radius, height, centre, mat, axis="Z", segments=24):
        matrix = Matrix.Translation(centre)
        if axis == "Y":
            matrix = matrix @ Matrix.Rotation(math.pi / 2, 4, "X")
        verts = bmesh.ops.create_cone(
            self.bm,
            cap_ends=True,
            segments=segments,
            radius1=radius,
            radius2=radius,
            depth=height,
            matrix=matrix,
        )["verts"]
        self._assign(verts, mat)

    def sphere(self, radius, centre, mat):
        matrix = Matrix.Translation(centre)
        verts = bmesh.ops.create_uvsphere(
            self.bm, u_segments=16, v_segments=10, radius=radius, matrix=matrix
        )["verts"]
        self._assign(verts, mat)

    def finish(self, name, x, y, angle):
        mesh = bpy.data.meshes.new(name)
        self.bm.to_mesh(mesh)
        self.bm.free()
        for mat in self.mats:
            mesh.materials.append(mat)
        for polygon in mesh.polygons:
            polygon.use_smooth = False
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (x, y, 0.0)
        obj.rotation_euler = (0.0, 0.0, angle - math.pi / 2)  # lokales +y → Front-Richtung
        return obj


def loose(name, color, roughness=0.6):
    """Material loser Möbel – der Viewer blendet alles mit „Moebel:“ auf Knopfdruck aus."""
    return material(f"Moebel: {name}", color, roughness)


def metal(name, color, roughness):
    key = ("metal", name, color, roughness)
    if key not in _materials:
        mat, bsdf = _principled(name, hex_to_rgba(color), roughness)
        bsdf.inputs["Metallic"].default_value = 1.0
        _materials[key] = mat
    return _materials[key]


OAK = "#B88A5A"
FABRIC = "#8C8E91"
BLACK = "#262626"
WHITE_LACQUER = "#EEECE7"
CERAMIC = "#F8F8F6"


def _legs(b, w, d, height, mat, size=0.035, inset=0.05):
    for sx in (-1, 1):
        for sy in (-1, 1):
            b.box(
                (size, size, height),
                (sx * (w / 2 - inset), sy * (d / 2 - inset), height / 2),
                mat,
            )


def _sofa(b, w, d, h, seats, fabric):
    arm = 0.18
    b.box((w, d, 0.30), (0, 0, 0.08 + 0.15), fabric, bevel=0.03)
    b.box((w, 0.20, h - 0.08), (0, -d / 2 + 0.10, 0.08 + (h - 0.08) / 2), fabric, bevel=0.04)
    for side in (-1, 1):
        b.box((arm, d, 0.56), (side * (w / 2 - arm / 2), 0, 0.08 + 0.28), fabric, bevel=0.05)
    inner = w - 2 * arm
    cushion = inner / seats
    for i in range(seats):
        x = -inner / 2 + cushion * (i + 0.5)
        b.box((cushion - 0.02, d - 0.26, 0.13), (x, 0.06, 0.38 + 0.065), fabric, bevel=0.05)
        b.box((cushion - 0.02, 0.20, 0.36), (x, -d / 2 + 0.30, 0.44 + 0.18), fabric, bevel=0.06)
    _legs(b, w, d, 0.08, loose("Metall schwarz", BLACK, 0.5), size=0.04, inset=0.08)


def _furniture(b, kind, w, d, h, fixture):
    oak = loose("Eiche", OAK, 0.5)
    black = loose("Metall schwarz", BLACK, 0.5)
    lacquer = loose("Lack weiß", WHITE_LACQUER, 0.35)
    if kind == "sofa":
        _sofa(b, w, d, h, 3 if w >= 2.0 else 2, loose("Stoff grau", FABRIC, 0.9))
    elif kind == "armchair":
        _sofa(b, w, d, h, 1, loose("Stoff sand", "#B7A68D", 0.9))
    elif kind == "coffee_table":
        b.box((w, d, 0.04), (0, 0, h - 0.02), oak, bevel=0.006)
        _legs(b, w, d, h - 0.04, black, size=0.03, inset=0.07)
    elif kind == "dining_table":
        b.box((w, d, 0.04), (0, 0, h - 0.02), oak, bevel=0.006)
        _legs(b, w, d, h - 0.04, oak, size=0.06, inset=0.09)
    elif kind == "chair":
        b.box((w, d * 0.95, 0.04), (0, 0.01, 0.45), oak, bevel=0.008)
        _legs(b, w, d, 0.43, black, size=0.025, inset=0.04)
        for side in (-1, 1):
            b.box((0.025, 0.025, 0.42), (side * (w / 2 - 0.04), -d / 2 + 0.04, 0.47 + 0.21), black)
        b.box((w - 0.02, 0.025, 0.18), (0, -d / 2 + 0.04, 0.86 - 0.09), oak, bevel=0.006)
    elif kind == "sideboard":
        b.box((w, d, h - 0.12), (0, 0, 0.12 + (h - 0.12) / 2), lacquer, bevel=0.004)
        _legs(b, w, d, 0.12, oak, size=0.03, inset=0.06)
        doors = max(2, round(w / 0.6))
        for i in range(1, doors):
            b.box(
                (0.004, 0.004, h - 0.16),
                (-w / 2 + w * i / doors, d / 2, 0.12 + (h - 0.12) / 2),
                black,
            )
    elif kind in ("bed_double", "bed_single"):
        b.box((w, d, 0.28), (0, 0, 0.03 + 0.14), oak, bevel=0.01)
        b.box(
            (w - 0.06, d - 0.12, 0.20),
            (0, 0.03, 0.31 + 0.10),
            loose("Matratze", "#F3F1EC", 0.9),
            bevel=0.04,
        )
        b.box(
            (w - 0.02, d * 0.62, 0.07),
            (0, d / 2 - d * 0.31 - 0.02, 0.51 + 0.035),
            loose("Bettdecke", "#D8D5CF", 0.95),
            bevel=0.03,
        )
        pillows = 2 if kind == "bed_double" else 1
        for i in range(pillows):
            x = 0.0 if pillows == 1 else (-1 + 2 * i) * w / 4
            b.box(
                (0.62 if pillows == 2 else 0.7, 0.38, 0.13),
                (x, -d / 2 + 0.34, 0.51 + 0.065),
                loose("Kissen", "#F5F4F0", 0.95),
                bevel=0.05,
            )
        b.box(
            (w + 0.04, 0.08, h),
            (0, -d / 2 + 0.04, h / 2),
            loose("Polster", "#8B8781", 0.9),
            bevel=0.02,
        )
    elif kind == "nightstand":
        b.box((w, d, h - 0.10), (0, 0, 0.10 + (h - 0.10) / 2), oak, bevel=0.004)
        _legs(b, w, d, 0.10, black, size=0.02, inset=0.04)
        b.box((w - 0.06, 0.004, 0.004), (0, d / 2, h * 0.62), black)
    elif kind == "wardrobe":
        b.box((w, d, h), (0, 0, h / 2), lacquer, bevel=0.003)
        doors = max(2, round(w / 0.5))
        for i in range(1, doors):
            b.box((0.003, 0.003, h - 0.04), (-w / 2 + w * i / doors, d / 2, h / 2), black)
        for i in range(doors):
            x = -w / 2 + w * (i + 0.5) / doors + (0.18 if i % 2 else -0.18) * (w / doors) / 0.5
            b.box((0.012, 0.02, 0.28), (x, d / 2 + 0.01, 1.05), black)
    elif kind == "desk":
        b.box((w, d, 0.03), (0, 0, h - 0.015), lacquer, bevel=0.004)
        _legs(b, w, d, h - 0.03, black, size=0.03, inset=0.05)
    elif kind == "office_chair":
        fabric = loose("Stoff anthrazit", "#3C3D40", 0.9)
        b.box((0.50, 0.48, 0.08), (0, 0.02, 0.48), fabric, bevel=0.03)
        b.box((0.46, 0.06, 0.48), (0, -0.21, 0.56 + 0.24), fabric, bevel=0.03)
        b.cylinder(0.025, 0.36, (0, 0, 0.08 + 0.18), black)
        b.box((0.62, 0.05, 0.04), (0, 0, 0.06), black)
        b.box((0.05, 0.62, 0.04), (0, 0, 0.06), black)
    elif kind == "shelf":
        for side in (-1, 1):
            b.box((0.02, d, h), (side * (w / 2 - 0.01), 0, h / 2), oak)
        for i in range(5):
            b.box((w - 0.04, d, 0.02), (0, 0, 0.05 + i * (h - 0.07) / 4), oak)
    elif kind == "plant":
        b.cylinder(0.2, 0.42, (0, 0, 0.21), loose("Topf", "#3A3A3A", 0.8))
        b.cylinder(0.185, 0.01, (0, 0, 0.415), loose("Erde", "#2E241C", 1.0))
        if plant_template() is None:  # Ersatzform ohne heruntergeladenes Modell
            b.sphere(0.32, (0, 0, 0.78), loose("Blätter", "#3F6B3A", 0.7))


def _kitchen(b, w, d, fixture):
    front = material("Küchenfront", fixture.get("color") or "#F2F1EC", 0.35)
    plinth = material("Küchensockel", "#2B2B2B", 0.6)
    worktop = material("Arbeitsplatte", "#3B3936", 0.35)
    steel = metal("Edelstahl", "#B9BBBD", 0.25)
    glass = material("Kochfeld", "#101010", 0.08)
    tall = 0.6 if w >= 2.4 else 0.0
    base_w = w - tall
    base_x = -w / 2 + base_w / 2
    b.box((base_w, d - 0.06, 0.10), (base_x, -0.03, 0.05), plinth)
    b.box((base_w, d - 0.02, 0.76), (base_x, -0.01, 0.10 + 0.38), front, bevel=0.002)
    b.box((base_w, d, 0.04), (base_x, 0, 0.88), worktop, bevel=0.003)
    doors = max(1, round(base_w / 0.6))
    for i in range(1, doors):
        b.box((0.003, 0.003, 0.74), (-w / 2 + base_w * i / doors, d / 2 - 0.01, 0.48), plinth)
    b.box((0.50, 0.42, 0.012), (-w / 2 + base_w * 0.3, 0.02, 0.906), steel)
    b.cylinder(0.015, 0.30, (-w / 2 + base_w * 0.3, -d / 2 + 0.08, 1.05), steel)
    if base_w >= 1.8:
        b.box((0.58, 0.51, 0.006), (-w / 2 + base_w * 0.75, 0.02, 0.903), glass)
    if tall:
        b.box((tall, d, 2.15), (w / 2 - tall / 2, 0, 0.10 + 1.075), front, bevel=0.002)
        b.box((tall, d - 0.06, 0.10), (w / 2 - tall / 2, -0.03, 0.05), plinth)
    if fixture.get("upper", True):
        b.box((base_w, 0.35, 0.72), (base_x, -d / 2 + 0.175, 1.45 + 0.36), front, bevel=0.002)


def _sanitary(b, kind, w, d, h):
    ceramic = material("Keramik weiß", CERAMIC, 0.15)
    chrome = metal("Chrom", "#D9DBDD", 0.08)
    mirror = metal("Spiegel", "#E8ECEE", 0.02)
    if kind == "wc":
        b.box((0.36, 0.52, 0.30), (0, 0.01, 0.10 + 0.15), ceramic, bevel=0.12)
        b.box((0.36, 0.46, 0.02), (0, 0.03, 0.415), ceramic, bevel=0.01)
        b.box((0.24, 0.012, 0.16), (0, -d / 2 + 0.006, 1.0), ceramic, bevel=0.004)
    elif kind in ("washbasin", "handbasin"):
        b.box((w, d, 0.14), (0, 0, 0.71 + 0.07), ceramic, bevel=0.03)
        b.box((w - 0.10, d - 0.14, 0.02), (0, 0.02, 0.845), material("Becken", "#E6E6E4", 0.2))
        b.cylinder(0.014, 0.16, (0, -d / 2 + 0.06, 0.93), chrome)
        if kind == "washbasin":
            b.box(
                (w - 0.02, d - 0.06, 0.46),
                (0, -0.03, 0.24 + 0.23),
                material("Waschtischunterschrank", OAK, 0.5),
                bevel=0.004,
            )
        b.box((w, 0.015, 0.75 if kind == "washbasin" else 0.55), (0, -d / 2 + 0.008, 1.45), mirror)
    elif kind == "shower":
        b.box((w, d, 0.04), (0, 0, 0.02), ceramic, bevel=0.01)
        b.box((w - 0.02, 0.008, 1.96), (0, d / 2 - 0.004, 0.04 + 0.98), glass_material())
        b.box((0.02, 0.02, 1.2), (0, -d / 2 + 0.03, 0.9 + 0.6), chrome)
        b.cylinder(0.11, 0.015, (0, -d / 2 + 0.16, 2.05), chrome)
    elif kind == "bathtub":
        wall = 0.07
        for side in (-1, 1):
            b.box((w, wall, h), (0, side * (d / 2 - wall / 2), h / 2), ceramic, bevel=0.015)
            b.box(
                (wall, d - 2 * wall, h), (side * (w / 2 - wall / 2), 0, h / 2), ceramic, bevel=0.015
            )
        b.box((w - 2 * wall, d - 2 * wall, 0.05), (0, 0, 0.12), ceramic)
        b.cylinder(0.015, 0.18, (w / 2 - 0.2, -d / 2 + 0.05, h + 0.09), chrome)
    elif kind == "washing_machine":
        b.box((w, d, h), (0, 0, h / 2), ceramic, bevel=0.01)
        b.cylinder(0.19, 0.02, (0, d / 2, h * 0.55), material("Bullauge", "#50565C", 0.1), axis="Y")


_plant = {"loaded": False, "template": None}


def plant_template():
    """Zimmerpflanze (Poly Haven, CC0): größte der 5 Varianten, auf den Ursprung zentriert."""
    if _plant["loaded"]:
        return _plant["template"]
    _plant["loaded"] = True
    path = os.path.join(MODEL_DIR or "", "calathea_orbifolia_01", "calathea_orbifolia_01_1k.gltf")
    if not (MODEL_DIR and os.path.isfile(path)):
        return None
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        return None

    def volume(obj):
        dims = obj.dimensions
        return dims.x * dims.y * dims.z

    template = max(meshes, key=volume)
    for obj in new:
        if obj is not template:
            bpy.data.objects.remove(obj, do_unlink=True)
    template.parent = None
    template.data.transform(template.matrix_world)
    template.matrix_world.identity()
    xs = [v.co.x for v in template.data.vertices]
    ys = [v.co.y for v in template.data.vertices]
    zs = [v.co.z for v in template.data.vertices]
    template.data.transform(
        Matrix.Translation((-(min(xs) + max(xs)) / 2, -(min(ys) + max(ys)) / 2, -min(zs)))
    )
    height = max(zs) - min(zs)
    template.scale = [0.75 / height] * 3 if height > 0 else [1.0] * 3
    for slot in template.material_slots:
        if slot.material and not slot.material.name.startswith("Moebel:"):
            slot.material.name = f"Moebel: Pflanze {slot.material.name}"
    template.location = (0, 0, -100)  # Vorlage außer Sicht; entfernt, falls unbenutzt
    _plant["template"] = template
    _plant["used"] = False
    return template


def build_fixture(fixture):
    kind = fixture["kind"]
    w, d, h = fixture["w"] * MM, fixture["d"] * MM, fixture["h"] * MM
    x, y = fixture["x"] * MM, fixture["y"] * MM
    prefix = "Moebel" if fixture["loose"] else "Ausstattung"
    b = Builder()
    if kind == "kitchen":
        _kitchen(b, w, d, fixture)
    elif kind in ("wc", "washbasin", "handbasin", "shower", "bathtub", "washing_machine"):
        _sanitary(b, kind, w, d, h)
    else:
        _furniture(b, kind, w, d, h, fixture)
    obj = b.finish(f"{prefix}_{fixture['id']}", x, y, fixture["angle"])
    if kind == "plant" and plant_template() is not None:
        template = plant_template()
        plant = template if not _plant["used"] else template.copy()
        if plant is not template:
            bpy.context.scene.collection.objects.link(plant)
        _plant["used"] = True
        plant.name = f"Moebel_{fixture['id']}_Blaetter"
        plant.location = (x, y, 0.42)
        plant.rotation_euler = (0.0, 0.0, fixture["angle"])
    return obj


def main():
    global TEXTURE_DIR, MODEL_DIR
    args = parse_args()
    TEXTURE_DIR = args.textures
    MODEL_DIR = args.models
    with open(args.spec, encoding="utf-8") as handle:
        scene_spec = json.load(handle)
    scene_spec.setdefault("door_finish", {"name": "Tür", "color": "#F2F1EC"})
    scene_spec.setdefault("window_frame", {"name": "Fensterrahmen", "color": "#F4F4F2"})

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    openings = sum(build_wall(w, scene_spec) for w in scene_spec["walls"])
    ceiling = max((w["height"] for w in scene_spec["walls"]), default=2500.0) * MM
    for room in scene_spec["rooms"]:
        build_floor(room)
        build_ceiling(room, ceiling)
    fixtures = scene_spec.get("fixtures", [])
    for fixture in fixtures:
        build_fixture(fixture)
    if _plant.get("template") is not None and not _plant.get("used"):
        bpy.data.objects.remove(_plant["template"], do_unlink=True)

    bpy.ops.export_scene.fbx(
        filepath=args.fbx,
        use_selection=False,
        object_types={"MESH"},
        apply_scale_options="FBX_SCALE_ALL",
        mesh_smooth_type="FACE",
    )
    bpy.ops.export_scene.gltf(
        filepath=args.glb,
        export_format="GLB",
        export_apply=True,
        export_image_format="JPEG",
        export_jpeg_quality=85,
    )

    textured = sorted(k for k, v in _images.items() if isinstance(k, str) and v)
    stats = {
        "walls": len(scene_spec["walls"]),
        "rooms": len(scene_spec["rooms"]),
        "openings": openings,
        "fixtures": len(fixtures),
        "loose": sum(1 for f in fixtures if f["loose"]),
        "objects": len(bpy.data.objects),
        "materials": len(bpy.data.materials),
        "textures": textured,
        "blender": bpy.app.version_string,
    }
    print("LUMIRA_RESULT " + json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
