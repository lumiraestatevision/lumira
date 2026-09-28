"""Treppengeländer aus der erkannten Treppe (mm, Grundriss x/y, Höhe z).

Geländer gehören an jede offene Seite: Stufenkanten, hinter denen weder eine Wand noch eine
andere Stufe liegt (Treppenauge, freie Wange zum Flur). Antritt (erste Stufe) und Austritt
(letzte Fläche) bleiben in Laufrichtung frei.

Ausgabe: Stäbe (Fußpunkt auf der Stufe) und Handlauf-Stücke zwischen benachbarten Stabköpfen –
je Treppenseite in Laufrichtung verbunden, so steigt der Handlauf mit der Treppe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from lumira_generator.logic.furnish import Outline, point_in_polygon
from lumira_shared.models import Stair

RAIL_HEIGHT = 900.0  # Handlauf über der Stufenvorderkante
POST_SPACING = 120.0  # lichter Abstand < 12 cm (Kinderschutz)
PROBE = 60.0  # so weit hinter der Kante wird nach Wand/Stufe gesucht
MIN_EDGE = 80.0
LINK_MM = 350.0  # Stäbe dichter als das werden mit dem Handlauf verbunden
ENDS_ALIGNED = 0.7  # Kante zeigt in Laufrichtung (Antritt/Austritt) → frei lassen

Point = tuple[float, float]


@dataclass(slots=True)
class Post:
    x: float
    y: float
    z: float  # Fußpunkt = Stufenoberkante
    along: float  # Position entlang der Lauflinie
    side: int  # -1 links, +1 rechts der Lauflinie


def _ccw(points: list[Point]) -> list[Point]:
    area = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True)
    )
    return points if area > 0 else list(reversed(points))


def _unit(dx: float, dy: float) -> Point:
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def _project(point: Point, line: list[Point]) -> tuple[float, int]:
    """Bogenlänge des nächsten Punkts auf der Lauflinie und Seite (links -1 / rechts +1)."""
    best = (math.inf, 0.0, 1)
    walked = 0.0
    for (ax, ay), (bx, by) in pairwise(line):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length2))
        px, py = ax + t * dx, ay + t * dy
        distance = math.hypot(point[0] - px, point[1] - py)
        if distance < best[0]:
            cross = dx * (point[1] - ay) - dy * (point[0] - ax)
            best = (distance, walked + t * math.sqrt(length2), -1 if cross > 0 else 1)
        walked += math.sqrt(length2)
    return best[1], best[2]


def stair_railing(stair: Stair, walls: list[Outline]) -> dict[str, Any] | None:
    steps = [_ccw([(p.x, p.y) for p in polygon]) for polygon in stair.steps]
    line = [(p.x, p.y) for p in stair.walking_line]
    if len(line) < 2:
        return None
    start_dir = _unit(line[0][0] - line[1][0], line[0][1] - line[1][1])  # vom Lauf weg
    end_dir = _unit(line[-1][0] - line[-2][0], line[-1][1] - line[-2][1])
    blockers = walls + [list(s) for s in steps]

    posts: list[Post] = []
    for k, polygon in enumerate(steps):
        top = (k + 1) * stair.rise_mm
        others = [s for i, s in enumerate(blockers) if i != len(walls) + k]
        for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            length = math.hypot(bx - ax, by - ay)
            if length < MIN_EDGE:
                continue
            ux, uy = (bx - ax) / length, (by - ay) / length
            nx, ny = uy, -ux  # nach außen (Polygon gegen den Uhrzeigersinn)
            if k == 0 and nx * start_dir[0] + ny * start_dir[1] > ENDS_ALIGNED:
                continue  # Antritt
            if k == len(steps) - 1 and nx * end_dir[0] + ny * end_dir[1] > ENDS_ALIGNED:
                continue  # Austritt
            probes = [
                (ax + ux * length * t + nx * PROBE, ay + uy * length * t + ny * PROBE)
                for t in (0.25, 0.5, 0.75)
            ]
            if any(point_in_polygon(p, outline) for p in probes for outline in others):
                continue  # Wand oder Nachbarstufe dahinter
            count = max(1, round(length / POST_SPACING))
            for i in range(count):
                t = (i + 0.5) / count
                point = (ax + ux * length * t, ay + uy * length * t)
                along, side = _project(point, line)
                posts.append(Post(point[0], point[1], top, along, side))
    if not posts:
        return None

    rails: list[tuple[int, int]] = []
    for side in (-1, 1):
        ordered = sorted(
            (i for i, p in enumerate(posts) if p.side == side), key=lambda i: posts[i].along
        )
        for a, b in pairwise(ordered):
            pa, pb = posts[a], posts[b]
            if math.hypot(pa.x - pb.x, pa.y - pb.y) <= LINK_MM:
                rails.append((a, b))
    return {
        "height": RAIL_HEIGHT,
        "posts": [[round(p.x, 1), round(p.y, 1), round(p.z, 1)] for p in posts],
        "rails": [list(r) for r in rails],
    }
