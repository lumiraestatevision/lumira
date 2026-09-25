"""Baut aus scene.json ein 3D-Modell und exportiert FBX + glTF (GLB).

Läuft INNERHALB von Blender (eigenes Python, bpy eingebaut) – nicht mit dem Service-Python:

    blender --background --factory-startup --python-exit-code 1 \\
        --python build_scene.py -- --spec scene.json --fbx model.fbx --glb model.glb

Umfang: Wände aus exaktem Grundriss (CAD-Fläche, als Prisma) oder als Quader aus Achse und
Dicke; Öffnungen als Wandstück mit ausgeschnittener Tür/Fenster (bleiben: Sturz, Brüstung).
Böden je Raum mit Materialfarbe aus dem Leistungsverzeichnis. STUB: keine Türblätter, Fensterrahmen,
Decken, Möblierung, Texturen/UVs – das folgt mit der Unreal-Pipeline.
"""

import argparse
import json
import math
import sys

import bmesh
import bpy

MM = 0.001  # Szene in Metern, Eingabe in Millimetern
_materials = {}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--glb", required=True)
    return parser.parse_args(argv)


def srgb_to_linear(channel):
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def hex_to_rgba(hex_color):
    value = (hex_color or "#CCCCCC").lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def material(name, hex_color, roughness=0.6):
    key = (name, hex_color)
    if key not in _materials:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        rgba = hex_to_rgba(hex_color)
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = roughness
        mat.diffuse_color = rgba
        _materials[key] = mat
    return _materials[key]


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


def build_wall(wall, finish):
    if wall.get("footprint"):
        obj = prism(f"Wall_{wall['id']}", wall["footprint"], wall["height"] * MM)
        obj.data.materials.append(material(finish["name"], finish["color"], roughness=0.8))
        return 0
    (x1, y1), (x2, y2) = wall["start"], wall["end"]
    dx, dy = (x2 - x1) * MM, (y2 - y1) * MM
    length = math.hypot(dx, dy)
    angle = math.atan2(dy, dx)
    thickness, height = wall["thickness"] * MM, wall["height"] * MM
    mid = ((x1 + x2) / 2 * MM, (y1 + y2) / 2 * MM)
    ux, uy = math.cos(angle), math.sin(angle)

    obj = box(
        f"Wall_{wall['id']}", (length, thickness, height), (mid[0], mid[1], height / 2), angle
    )

    cutters = []
    for opening in wall["openings"]:
        width, op_height = opening["width"] * MM, opening["height"] * MM
        along = opening["offset"] * MM + width / 2 - length / 2
        # 2 mm breiter: füllt die Öffnung die ganze Wandlänge (CAD-Lücke zwischen zwei
        # Wandflächen), schneidet der Boolean sonst entlang deckungsgleicher Flächen.
        cutter = box(
            f"Cut_{opening['id']}",
            (width + 0.002, thickness * 3, op_height),
            (mid[0] + ux * along, mid[1] + uy * along, opening["sill"] * MM + op_height / 2),
            angle,
        )
        modifier = obj.modifiers.new(name=f"cut_{opening['id']}", type="BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.object = cutter
        cutters.append(cutter)

    if cutters:
        bake_modifiers(obj)
        for cutter in cutters:
            bpy.data.objects.remove(cutter, do_unlink=True)

    obj.data.materials.append(material(finish["name"], finish["color"], roughness=0.8))
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
    obj.data.materials.append(material(floor["name"], floor["color"], roughness=0.45))


def main():
    args = parse_args()
    with open(args.spec, encoding="utf-8") as handle:
        scene_spec = json.load(handle)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0

    openings = sum(build_wall(w, scene_spec["wall_finish"]) for w in scene_spec["walls"])
    for room in scene_spec["rooms"]:
        build_floor(room)

    bpy.ops.export_scene.fbx(
        filepath=args.fbx,
        use_selection=False,
        object_types={"MESH"},
        apply_scale_options="FBX_SCALE_ALL",
        mesh_smooth_type="FACE",
    )
    bpy.ops.export_scene.gltf(filepath=args.glb, export_format="GLB", export_apply=True)

    stats = {
        "walls": len(scene_spec["walls"]),
        "rooms": len(scene_spec["rooms"]),
        "openings": openings,
        "objects": len(bpy.data.objects),
        "blender": bpy.app.version_string,
    }
    print("LUMIRA_RESULT " + json.dumps(stats), flush=True)


if __name__ == "__main__":
    main()
