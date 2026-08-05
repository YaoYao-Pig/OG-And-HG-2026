#!/usr/bin/env python3
"""Build the browser-ready OpenGalaxy graph dataset.

The source CSV files remain the audited export-of-record.  This script derives a
compact, deterministic JSON representation for the visualization: it assigns
weighted label-propagation communities, computes a topology-driven continuous
galactic-disc layout, and replaces string edge endpoints with node-array indexes.

Only Python's standard library is used.  Running the script twice against the
same inputs produces byte-identical output.
"""

from __future__ import annotations

import argparse
import colorsys
import csv
import hashlib
import heapq
import json
import math
import os
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ARTIFACT_NAME = "open-galaxy-github-202508-202607-preview"
ALGORITHM_VERSION = "topology-force-galactic-disc-v3"
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))

COMMON_LANGUAGE_COLORS = {
    "C": "#68a7ff",
    "C#": "#a878ff",
    "C++": "#ff6da8",
    "Dart": "#38c9dd",
    "Go": "#4ed9ef",
    "Java": "#ff8c5a",
    "JavaScript": "#f4d35e",
    "Kotlin": "#c077ff",
    "LLVM": "#be9b7b",
    "Nix": "#8bb8ff",
    "PHP": "#8a91d8",
    "Python": "#66d9a7",
    "Ruby": "#ff6f79",
    "Rust": "#f0a76b",
    "Shell": "#9ee493",
    "Swift": "#ff795b",
    "TypeScript": "#56a8ff",
    "Unknown": "#77849b",
}


def stable_unit(value: str) -> float:
    """Return a stable number in [0, 1) without Python's randomized hash()."""

    raw = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(raw, "big") / 2**64


def stable_color(value: str, *, saturation: float = 0.72, lightness: float = 0.62) -> str:
    hue = stable_unit(value)
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def language_color(language: str) -> str:
    return COMMON_LANGUAGE_COLORS.get(language, stable_color(f"language:{language}"))


def community_color(community_id: int) -> str:
    # A golden-ratio hue walk keeps adjacent community IDs visually distinct.
    hue = (0.56 + community_id * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hls_to_rgb(hue, 0.62, 0.78)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def parse_float(value: str, field: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"Non-finite {field}: {value!r}")
    return number


def parse_int(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def read_nodes(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    required = {
        "id",
        "name",
        "display_label",
        "openrank_sum",
        "contributors",
        "primary_language",
        "topics",
        "description",
        "url",
    }
    nodes: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing node columns: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            node_id = row["id"].strip()
            if not node_id:
                raise ValueError(f"Empty node id at {path}:{row_number}")
            if node_id in index_by_id:
                raise ValueError(f"Duplicate node id {node_id!r} at {path}:{row_number}")

            index_by_id[node_id] = len(nodes)
            nodes.append(
                {
                    "id": node_id,
                    "name": row["name"],
                    "label": row["display_label"],
                    "r": parse_float(row["openrank_sum"], "openrank_sum"),
                    "contributors": parse_int(row["contributors"], "contributors"),
                    "lang": row["primary_language"].strip() or "Unknown",
                    "topics": row["topics"],
                    "description": row["description"],
                    "url": row["url"],
                }
            )

    if not nodes:
        raise ValueError(f"No nodes found in {path}")
    return nodes, index_by_id


def read_edges(
    path: Path,
    index_by_id: dict[str, int],
    node_count: int,
) -> tuple[list[tuple[int, int, float, int, float]], list[list[tuple[int, float]]], list[int]]:
    required = {"source", "target", "weight", "shared_contributors", "collaboration_strength"}
    edges: list[tuple[int, int, float, int, float]] = []
    adjacency: list[list[tuple[int, float]]] = [[] for _ in range(node_count)]
    degree = [0] * node_count

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing edge columns: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            source_id = row["source"]
            target_id = row["target"]
            try:
                source = index_by_id[source_id]
                target = index_by_id[target_id]
            except KeyError as exc:
                raise ValueError(
                    f"Unknown edge endpoint {exc.args[0]!r} at {path}:{row_number}"
                ) from exc
            if source == target:
                raise ValueError(f"Self-loop for {source_id!r} at {path}:{row_number}")

            weight = parse_float(row["weight"], "weight")
            shared = parse_int(row["shared_contributors"], "shared_contributors")
            strength = parse_float(row["collaboration_strength"], "collaboration_strength")
            if weight < 0 or shared < 0 or strength < 0:
                raise ValueError(f"Negative edge metric at {path}:{row_number}")

            # Compress the heavy-tailed metrics for community detection while
            # retaining both signals.  The original values are kept in output.
            affinity = math.log1p(weight) + 0.55 * math.log1p(strength)
            if affinity <= 0:
                affinity = 1e-12

            edges.append((source, target, weight, shared, strength))
            adjacency[source].append((target, affinity))
            adjacency[target].append((source, affinity))
            degree[source] += 1
            degree[target] += 1

    if not edges:
        raise ValueError(f"No edges found in {path}")
    for neighbors in adjacency:
        neighbors.sort(key=lambda item: item[0])
    return edges, adjacency, degree


def weighted_label_communities(
    nodes: list[dict[str, Any]],
    adjacency: list[list[tuple[int, float]]],
    *,
    max_iterations: int,
    resolution: float,
) -> tuple[list[int], int]:
    """Deterministic modularity-adjusted weighted label propagation.

    Plain label propagation tends to collapse a dense graph into one giant
    label.  The community-volume penalty below is the local modularity term
    used by Louvain-style optimization, so strong local affinity can propagate
    a label while oversized communities become progressively harder to join.
    """

    node_count = len(nodes)
    labels = list(range(node_count))
    weighted_degree = [sum(weight for _, weight in neighbors) for neighbors in adjacency]
    community_volume = weighted_degree.copy()
    total_volume = sum(weighted_degree)
    order = sorted(
        range(node_count),
        key=lambda index: (
            -weighted_degree[index],
            stable_unit(nodes[index]["id"]),
            nodes[index]["id"],
        ),
    )

    completed_iterations = 0
    for iteration in range(max_iterations):
        changed = 0
        for node_index in order:
            neighbors = adjacency[node_index]
            if not neighbors:
                continue

            node_volume = weighted_degree[node_index]
            current = labels[node_index]
            community_volume[current] -= node_volume
            scores: dict[int, float] = defaultdict(float)
            for neighbor, affinity in neighbors:
                scores[labels[neighbor]] += affinity

            best_label = current
            best_gain = 0.0
            for candidate, internal_affinity in sorted(scores.items()):
                gain = internal_affinity - (
                    resolution * node_volume * community_volume[candidate] / total_volume
                )
                epsilon = max(1e-12, abs(best_gain) * 1e-12, abs(gain) * 1e-12)
                if gain > best_gain + epsilon or (
                    abs(gain - best_gain) <= epsilon
                    and candidate == current
                    and best_label != current
                ):
                    best_gain = gain
                    best_label = candidate

            community_volume[best_label] += node_volume
            if best_label != current:
                labels[node_index] = best_label
                changed += 1

        completed_iterations = iteration + 1
        if changed == 0:
            break

    # Attach tiny fragments to the neighboring established community with the
    # strongest total affinity.  This removes visual dust without dropping any
    # node and remains stable because all ordering and tie-breaking are fixed.
    # Small disconnected components naturally remain independent.
    for _ in range(5):
        sizes = Counter(labels)
        small_labels = {label for label, size in sizes.items() if size < 12}
        if not small_labels:
            break
        changed = False
        for node_index in sorted(range(node_count), key=lambda index: nodes[index]["id"]):
            if labels[node_index] not in small_labels:
                continue
            scores: dict[int, float] = defaultdict(float)
            for neighbor, affinity in adjacency[node_index]:
                neighbor_label = labels[neighbor]
                if neighbor_label not in small_labels:
                    scores[neighbor_label] += affinity
            if scores:
                best_score = max(scores.values())
                best_label = min(
                    label
                    for label, score in scores.items()
                    if best_score - score <= max(1e-12, abs(best_score) * 1e-12)
                )
                labels[node_index] = best_label
                changed = True
        if not changed:
            break

    groups: dict[int, list[int]] = defaultdict(list)
    for node_index, label in enumerate(labels):
        groups[label].append(node_index)

    ordered_groups = sorted(
        groups.values(),
        key=lambda members: (
            -len(members),
            -sum(nodes[index]["r"] for index in members),
            min(nodes[index]["id"] for index in members),
        ),
    )
    normalized = [0] * node_count
    for community_id, members in enumerate(ordered_groups):
        for node_index in members:
            normalized[node_index] = community_id
    return normalized, completed_iterations


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.size = [1] * size

    def find(self, item: int) -> int:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        if self.size[left_root] < self.size[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]
        return True


def connected_components(
    node_count: int,
    edges: list[tuple[int, int, float, int, float]],
) -> list[list[int]]:
    sets = DisjointSet(node_count)
    for source, target, _, _, _ in edges:
        sets.union(source, target)
    members: dict[int, list[int]] = defaultdict(list)
    for node in range(node_count):
        members[sets.find(node)].append(node)
    return sorted(
        members.values(),
        key=lambda component: (-len(component), min(component)),
    )


def backbone_edge_indexes(
    node_count: int,
    edges: list[tuple[int, int, float, int, float]],
) -> set[int]:
    """Return the union of MSF, per-node Top-2, and global Top-1500 edges."""

    by_weight = sorted(
        range(len(edges)),
        key=lambda edge_index: (
            -edges[edge_index][2],
            -edges[edge_index][4],
            -edges[edge_index][3],
            edges[edge_index][0],
            edges[edge_index][1],
        ),
    )

    backbone: set[int] = set(by_weight[:1500])

    # Kruskal over the full graph yields one maximum spanning tree per
    # connected component, i.e. a maximum spanning forest.
    forest_sets = DisjointSet(node_count)
    for edge_index in by_weight:
        source, target, _, _, _ = edges[edge_index]
        if forest_sets.union(source, target):
            backbone.add(edge_index)

    incident: list[list[int]] = [[] for _ in range(node_count)]
    for edge_index, (source, target, _, _, _) in enumerate(edges):
        incident[source].append(edge_index)
        incident[target].append(edge_index)
    weight_position = {edge_index: position for position, edge_index in enumerate(by_weight)}
    for edge_indexes in incident:
        edge_indexes.sort(key=weight_position.__getitem__)
        backbone.update(edge_indexes[:2])

    return backbone


class QuadCell:
    __slots__ = ("left", "bottom", "size", "mass", "cx", "cy", "indices", "children")

    def __init__(
        self,
        left: float,
        bottom: float,
        size: float,
        mass: float,
        cx: float,
        cy: float,
        indices: tuple[int, ...],
        children: tuple["QuadCell", ...],
    ) -> None:
        self.left = left
        self.bottom = bottom
        self.size = size
        self.mass = mass
        self.cx = cx
        self.cy = cy
        self.indices = indices
        self.children = children


def build_quadtree(
    xs: list[float],
    ys: list[float],
    masses: list[float],
    indices: list[int],
    left: float,
    bottom: float,
    size: float,
    *,
    depth: int = 0,
) -> QuadCell:
    total_mass = sum(masses[index] for index in indices)
    center_x = sum(xs[index] * masses[index] for index in indices) / total_mass
    center_y = sum(ys[index] * masses[index] for index in indices) / total_mass
    if len(indices) <= 4 or depth >= 18:
        return QuadCell(
            left,
            bottom,
            size,
            total_mass,
            center_x,
            center_y,
            tuple(indices),
            (),
        )

    half = size / 2.0
    middle_x = left + half
    middle_y = bottom + half
    buckets: list[list[int]] = [[], [], [], []]
    for index in indices:
        quadrant = (1 if xs[index] >= middle_x else 0) + (2 if ys[index] >= middle_y else 0)
        buckets[quadrant].append(index)

    children: list[QuadCell] = []
    for quadrant, bucket in enumerate(buckets):
        if not bucket:
            continue
        child_left = left + (half if quadrant & 1 else 0.0)
        child_bottom = bottom + (half if quadrant & 2 else 0.0)
        children.append(
            build_quadtree(
                xs,
                ys,
                masses,
                bucket,
                child_left,
                child_bottom,
                half,
                depth=depth + 1,
            )
        )
    return QuadCell(
        left,
        bottom,
        size,
        total_mass,
        center_x,
        center_y,
        (),
        tuple(children),
    )


def add_barnes_hut_repulsion(
    node_index: int,
    root: QuadCell,
    xs: list[float],
    ys: list[float],
    masses: list[float],
    *,
    coefficient: float,
    theta: float,
) -> tuple[float, float]:
    node_x = xs[node_index]
    node_y = ys[node_index]
    force_x = 0.0
    force_y = 0.0
    stack = [root]
    while stack:
        cell = stack.pop()
        if cell.indices:
            for other_index in cell.indices:
                if other_index == node_index:
                    continue
                delta_x = node_x - xs[other_index]
                delta_y = node_y - ys[other_index]
                distance_squared = delta_x * delta_x + delta_y * delta_y + 0.000025
                factor = coefficient * masses[other_index] / distance_squared
                force_x += delta_x * factor
                force_y += delta_y * factor
            continue

        delta_x = node_x - cell.cx
        delta_y = node_y - cell.cy
        distance_squared = delta_x * delta_x + delta_y * delta_y + 0.000025
        distance = math.sqrt(distance_squared)
        contains_node = (
            cell.left <= node_x <= cell.left + cell.size
            and cell.bottom <= node_y <= cell.bottom + cell.size
        )
        if not contains_node and cell.size / distance < theta:
            factor = coefficient * cell.mass / distance_squared
            force_x += delta_x * factor
            force_y += delta_y * factor
        else:
            stack.extend(cell.children)
    return force_x, force_y


def quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    ratio = position - lower
    return ordered[lower] * (1.0 - ratio) + ordered[upper] * ratio


def pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_scale == 0 or right_scale == 0:
        return 0.0
    return numerator / (left_scale * right_scale)


def backbone_core_numbers(
    node_count: int,
    members: list[int],
    neighbors: list[list[tuple[int, float]]],
) -> list[int]:
    """Compute deterministic k-core numbers on the layout backbone."""

    active = [False] * node_count
    current_degree = [0] * node_count
    for index in members:
        active[index] = True
        current_degree[index] = len(neighbors[index])
    heap = [(current_degree[index], index) for index in members]
    heapq.heapify(heap)
    core = [0] * node_count
    while heap:
        degree_value, node_index = heapq.heappop(heap)
        if not active[node_index] or degree_value != current_degree[node_index]:
            continue
        active[node_index] = False
        core[node_index] = degree_value
        for neighbor, _ in neighbors[node_index]:
            if active[neighbor] and current_degree[neighbor] > degree_value:
                current_degree[neighbor] -= 1
                heapq.heappush(heap, (current_degree[neighbor], neighbor))
    return core


def layout_metrics(
    coordinates: list[tuple[float, float]],
    nodes: list[dict[str, Any]],
    communities: list[int],
    components: list[list[int]],
    edges: list[tuple[int, int, float, int, float]],
    backbone_indexes: set[int],
) -> dict[str, Any]:
    main_members = components[0]
    main_set = set(main_members)
    radii = [math.hypot(*coordinates[index]) for index in main_members]
    p99_radius = max(quantile(radii, 0.99), 1e-9)

    radial_bins = 8
    angular_bins = 24
    occupied: set[tuple[int, int]] = set()
    for index in main_members:
        x, y = coordinates[index]
        radius = min(0.999999, math.hypot(x, y) / p99_radius)
        radial_bin = min(radial_bins - 1, int(radius * radius * radial_bins))
        angular_bin = int(((math.atan2(y, x) + math.pi) / math.tau) * angular_bins)
        angular_bin = min(angular_bins - 1, angular_bin)
        occupied.add((radial_bin, angular_bin))

    backbone_lengths = []
    radial_alignment = []
    for edge_index in sorted(backbone_indexes):
        source, target, _, _, _ = edges[edge_index]
        if source not in main_set or target not in main_set:
            continue
        source_x, source_y = coordinates[source]
        target_x, target_y = coordinates[target]
        edge_x = target_x - source_x
        edge_y = target_y - source_y
        edge_length = math.hypot(edge_x, edge_y)
        backbone_lengths.append(edge_length)
        middle_x = (source_x + target_x) / 2.0
        middle_y = (source_y + target_y) / 2.0
        middle_radius = math.hypot(middle_x, middle_y)
        if edge_length > 1e-9 and middle_radius > 0.08:
            radial_alignment.append(
                abs(edge_x * middle_x + edge_y * middle_y)
                / (edge_length * middle_radius)
            )

    community_members: dict[int, list[int]] = defaultdict(list)
    for index in main_members:
        community_members[communities[index]].append(index)
    entropy_total = 0.0
    entropy_weight = 0
    centroid_radii = []
    for members in community_members.values():
        if len(members) < 12:
            continue
        sector_counts = [0] * 16
        for index in members:
            angle = math.atan2(coordinates[index][1], coordinates[index][0])
            sector = min(15, int(((angle + math.pi) / math.tau) * 16))
            sector_counts[sector] += 1
        entropy = -sum(
            (count / len(members)) * math.log(count / len(members))
            for count in sector_counts
            if count
        ) / math.log(16)
        entropy_total += entropy * len(members)
        entropy_weight += len(members)
        center_x = sum(coordinates[index][0] for index in members) / len(members)
        center_y = sum(coordinates[index][1] for index in members) / len(members)
        centroid_radii.append(math.hypot(center_x, center_y))

    rank_values = [math.log1p(nodes[index]["r"]) for index in main_members]
    return {
        "mode": "continuous-topology-disc",
        "mainNodes": len(main_members),
        "satellites": len(components) - 1,
        "mainRadiusP50": round(quantile(radii, 0.5), 5),
        "mainRadiusP95": round(quantile(radii, 0.95), 5),
        "mainRadiusMax": round(max(radii), 5),
        "polarBinCoverage": round(len(occupied) / (radial_bins * angular_bins), 4),
        "backboneMedianLength": round(statistics.median(backbone_lengths), 5),
        "radialBackboneShare": round(
            sum(value >= 0.78 for value in radial_alignment) / len(radial_alignment)
            if radial_alignment
            else 0.0,
            4,
        ),
        "communityAngularEntropy": round(
            entropy_total / entropy_weight if entropy_weight else 0.0,
            4,
        ),
        "communityCentroidRadiusMedian": round(
            statistics.median(centroid_radii) if centroid_radii else 0.0,
            5,
        ),
        "rankRadiusCorrelation": round(pearson(rank_values, radii), 4),
    }


def continuous_disc_layout(
    nodes: list[dict[str, Any]],
    communities: list[int],
    degree: list[int],
    edges: list[tuple[int, int, float, int, float]],
    backbone_indexes: set[int],
    components: list[list[int]],
    *,
    iterations: int,
) -> tuple[list[tuple[float, float]], dict[str, float], dict[str, Any]]:
    """Lay out the graph as one topology-shaped galactic disc.

    The main component starts as an importance-ranked Fibonacci disc.  A
    Barnes-Hut repulsion field and weighted backbone springs then expose the
    network's topology without carving communities into separate circles.
    Radial importance gravity creates a luminous core, and a final continuous
    polar shear bends topology-derived branches into subtle spiral structure.
    """

    node_count = len(nodes)
    main_members = components[0]
    main_set = set(main_members)
    coordinates: list[tuple[float, float]] = [(0.0, 0.0)] * node_count
    xs = [0.0] * node_count
    ys = [0.0] * node_count

    importance_order = sorted(
        main_members,
        key=lambda index: (
            -nodes[index]["r"],
            -degree[index],
            nodes[index]["id"],
        ),
    )
    target_radius = [0.0] * node_count
    for position, node_index in enumerate(importance_order):
        fraction = (position + 0.5) / len(importance_order)
        radius = 0.045 + 0.76 * math.sqrt(fraction)
        angle = (
            position * GOLDEN_ANGLE
            + 0.52 * (stable_unit(nodes[node_index]["id"]) - 0.5)
            + 0.025 * communities[node_index]
        )
        xs[node_index] = radius * math.cos(angle)
        ys[node_index] = radius * math.sin(angle)
        target_radius[node_index] = radius

    masses = [1.0] * node_count
    for node_index in main_members:
        masses[node_index] = (
            0.78
            + 0.12 * math.log1p(degree[node_index])
            + 0.035 * math.log1p(nodes[node_index]["r"])
        )

    layout_edges: list[tuple[int, int, float, float]] = []
    topology_neighbors: list[list[tuple[int, float]]] = [[] for _ in range(node_count)]
    for edge_index in sorted(backbone_indexes):
        source, target, weight, _, strength = edges[edge_index]
        if source not in main_set or target not in main_set:
            continue
        signal = math.log1p(weight) + 0.55 * math.log1p(strength)
        desired_length = 0.027 + 0.082 / (1.0 + 0.24 * signal)
        stiffness = 0.22 * (0.54 + 0.46 * min(1.0, signal / 8.0))
        layout_edges.append((source, target, desired_length, stiffness))
        topology_neighbors[source].append((target, signal))
        topology_neighbors[target].append((source, signal))
    for neighbors in topology_neighbors:
        neighbors.sort(key=lambda item: item[0])
    core_numbers = backbone_core_numbers(node_count, main_members, topology_neighbors)

    main_indices = sorted(main_members)
    for iteration in range(iterations):
        progress = iteration / max(1, iterations - 1)
        minimum_x = min(xs[index] for index in main_members)
        maximum_x = max(xs[index] for index in main_members)
        minimum_y = min(ys[index] for index in main_members)
        maximum_y = max(ys[index] for index in main_members)
        span = max(maximum_x - minimum_x, maximum_y - minimum_y, 1e-6) * 1.002
        left = (minimum_x + maximum_x - span) / 2.0
        bottom = (minimum_y + maximum_y - span) / 2.0
        tree = build_quadtree(xs, ys, masses, main_indices, left, bottom, span)

        force_x = [0.0] * node_count
        force_y = [0.0] * node_count
        repulsion = 0.00013 * (1.0 - 0.16 * progress)
        for node_index in main_members:
            repel_x, repel_y = add_barnes_hut_repulsion(
                node_index,
                tree,
                xs,
                ys,
                masses,
                coefficient=repulsion,
                theta=0.76,
            )
            force_x[node_index] += repel_x
            force_y[node_index] += repel_y

        for source, target, desired_length, stiffness in layout_edges:
            delta_x = xs[target] - xs[source]
            delta_y = ys[target] - ys[source]
            distance = math.hypot(delta_x, delta_y) + 1e-12
            spring = stiffness * (distance - desired_length) / distance
            pull_x = delta_x * spring
            pull_y = delta_y * spring
            force_x[source] += pull_x
            force_y[source] += pull_y
            force_x[target] -= pull_x
            force_y[target] -= pull_y

        for node_index in main_members:
            x = xs[node_index]
            y = ys[node_index]
            radius = math.hypot(x, y) + 1e-12
            radial_x = x / radius
            radial_y = y / radius
            # Global gravity keeps one continuous body; the gentler target
            # radius term keeps important repositories toward the luminous core.
            radial_force = (
                -0.028 * radius
                + 0.0085 * (target_radius[node_index] - radius)
            )
            force_x[node_index] += radial_x * radial_force
            force_y[node_index] += radial_y * radial_force
            # Differential rotation curves radial graph branches into arms.
            swirl = 0.0022 * (0.35 + target_radius[node_index]) * (1.0 - progress)
            force_x[node_index] -= radial_y * swirl
            force_y[node_index] += radial_x * swirl

        temperature = 0.048 * (1.0 - progress) ** 1.35 + 0.0012
        for node_index in main_members:
            move_x = force_x[node_index] * 0.42
            move_y = force_y[node_index] * 0.42
            movement = math.hypot(move_x, move_y)
            if movement > temperature:
                factor = temperature / movement
                move_x *= factor
                move_y *= factor
            xs[node_index] += move_x
            ys[node_index] += move_y

        center_x = sum(xs[index] for index in main_members) / len(main_members)
        center_y = sum(ys[index] for index in main_members) / len(main_members)
        for node_index in main_members:
            xs[node_index] -= center_x
            ys[node_index] -= center_y

    # Robustly scale the continuous main disc, gently compressing only the
    # outermost one percent so a few force-layout outliers do not shrink it.
    raw_radii = [math.hypot(xs[index], ys[index]) for index in main_members]
    p99 = max(quantile(raw_radii, 0.99), 1e-9)
    scale = 0.80 / p99
    force_radii = [0.0] * node_count
    force_angles = [0.0] * node_count
    maximum_core = max(core_numbers[index] for index in main_members)
    maximum_log_degree = max(math.log1p(degree[index]) for index in main_members)
    maximum_log_rank = max(math.log1p(nodes[index]["r"]) for index in main_members)
    for node_index in main_members:
        x = xs[node_index] * scale
        y = ys[node_index] * scale
        radius = math.hypot(x, y)
        if radius > 0.84:
            compressed = 0.84 + 0.034 * (1.0 - math.exp(-(radius - 0.84) / 0.055))
            x *= compressed / radius
            y *= compressed / radius
            radius = compressed
        core_ratio = core_numbers[node_index] / maximum_core if maximum_core else 0.0
        degree_ratio = math.log1p(degree[node_index]) / maximum_log_degree
        rank_ratio = math.log1p(nodes[node_index]["r"]) / maximum_log_rank
        centrality = 0.46 * core_ratio + 0.29 * degree_ratio + 0.25 * rank_ratio
        topology_radius = 0.08 + 0.72 * (1.0 - centrality) ** 1.55
        blend = 0.035 + 0.15 * (1.0 - core_ratio) ** 1.7
        force_radii[node_index] = radius * (1.0 - blend) + topology_radius * blend
        force_angles[node_index] = math.atan2(y, x)

    # Low-coreness branches borrow their direction from strong backbone
    # neighbors.  Repeated circular averaging creates radial filaments while
    # the high-coreness core remains an interpenetrating cloud.
    for _ in range(6):
        next_angles = force_angles.copy()
        for node_index in main_members:
            neighbors = topology_neighbors[node_index]
            if not neighbors:
                continue
            core_ratio = core_numbers[node_index] / maximum_core if maximum_core else 0.0
            branch_factor = (
                0.29
                * (1.0 - core_ratio) ** 1.55
                * min(1.0, 4.0 / max(1, len(neighbors)))
            )
            vector_x = sum(
                math.cos(force_angles[neighbor]) * signal for neighbor, signal in neighbors
            )
            vector_y = sum(
                math.sin(force_angles[neighbor]) * signal for neighbor, signal in neighbors
            )
            if vector_x == 0 and vector_y == 0:
                continue
            neighbor_angle = math.atan2(vector_y, vector_x)
            difference = math.atan2(
                math.sin(neighbor_angle - force_angles[node_index]),
                math.cos(neighbor_angle - force_angles[node_index]),
            )
            next_angles[node_index] = force_angles[node_index] + branch_factor * difference
        force_angles = next_angles

    for node_index in main_members:
        radius = force_radii[node_index]
        angle = force_angles[node_index] + 0.68 * (radius / 0.88) ** 1.45
        xs[node_index] = radius * math.cos(angle)
        ys[node_index] = radius * math.sin(angle)

    # The remaining connected components are tiny.  Blend them into a narrow
    # outer annulus as restrained satellites instead of scaling them into peers.
    satellite_phase = stable_unit(nodes[importance_order[0]]["id"]) * math.tau
    for component_position, component in enumerate(components[1:], start=1):
        component_key = min(nodes[index]["id"] for index in component)
        center_angle = (
            satellite_phase
            + component_position * GOLDEN_ANGLE
            + 0.34 * stable_unit(f"satellite-angle:{component_key}")
        )
        center_radius = 0.842 + 0.072 * stable_unit(f"satellite-radius:{component_key}")
        center_x = center_radius * math.cos(center_angle)
        center_y = center_radius * math.sin(center_angle)
        local_extent = min(0.026, 0.0045 + 0.0034 * math.sqrt(len(component)))
        ordered_component = sorted(
            component,
            key=lambda index: (-nodes[index]["r"], -degree[index], nodes[index]["id"]),
        )
        local_phase = stable_unit(f"satellite-local:{component_key}") * math.tau
        for position, node_index in enumerate(ordered_component):
            local_radius = local_extent * math.sqrt((position + 0.35) / len(component))
            local_angle = local_phase + position * GOLDEN_ANGLE
            xs[node_index] = center_x + local_radius * math.cos(local_angle)
            ys[node_index] = center_y + local_radius * math.sin(local_angle)

    extent = max(max(abs(xs[index]), abs(ys[index])) for index in range(node_count))
    final_scale = 0.98 / extent if extent > 0.98 else 1.0
    for index in range(node_count):
        coordinates[index] = (
            round(xs[index] * final_scale, 5),
            round(ys[index] * final_scale, 5),
        )

    x_values = [coordinate[0] for coordinate in coordinates]
    y_values = [coordinate[1] for coordinate in coordinates]
    bounds = {
        "minX": min(x_values),
        "maxX": max(x_values),
        "minY": min(y_values),
        "maxY": max(y_values),
    }
    metrics = layout_metrics(
        coordinates,
        nodes,
        communities,
        components,
        edges,
        backbone_indexes,
    )
    metrics["iterations"] = iterations
    metrics["maxBackboneCore"] = maximum_core
    metrics["peripheralNodeShare"] = round(
        sum(core_numbers[index] <= 1 for index in main_members) / len(main_members),
        4,
    )
    return coordinates, bounds, metrics


def compact_number(value: float, digits: int) -> int | float:
    rounded = round(value, digits)
    if rounded == 0:
        return 0
    if rounded.is_integer():
        return int(rounded)
    return rounded


def build_payload(
    nodes_path: Path,
    edges_path: Path,
    manifest_path: Path,
    *,
    max_iterations: int,
    resolution: float,
    layout_iterations: int,
) -> dict[str, Any]:
    nodes, index_by_id = read_nodes(nodes_path)
    edges, adjacency, degree = read_edges(edges_path, index_by_id, len(nodes))

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    parameters = manifest["parameters"]
    expected_nodes = manifest["outputs"]["nodes.csv"]["rows"]
    expected_edges = manifest["outputs"]["edges.csv"]["rows"]
    if len(nodes) != expected_nodes or len(edges) != expected_edges:
        raise ValueError(
            "CSV counts do not match manifest: "
            f"nodes={len(nodes)}/{expected_nodes}, edges={len(edges)}/{expected_edges}"
        )

    community_ids, iterations = weighted_label_communities(
        nodes,
        adjacency,
        max_iterations=max_iterations,
        resolution=resolution,
    )
    components = connected_components(len(nodes), edges)
    backbone_indexes = backbone_edge_indexes(len(nodes), edges)
    coordinates, bounds, layout_summary = continuous_disc_layout(
        nodes,
        community_ids,
        degree,
        edges,
        backbone_indexes,
        components,
        iterations=layout_iterations,
    )
    component_count = len(components)
    main_component = len(components[0])

    community_members: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(community_ids):
        community_members[community_id].append(node_index)

    community_summary: list[dict[str, Any]] = []
    for community_id in range(len(community_members)):
        members = community_members[community_id]
        top_node = min(
            members,
            key=lambda index: (-nodes[index]["r"], -degree[index], nodes[index]["id"]),
        )
        top_language = min(
            Counter(nodes[index]["lang"] for index in members).items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        center_x = round(sum(coordinates[index][0] for index in members) / len(members), 5)
        center_y = round(sum(coordinates[index][1] for index in members) / len(members), 5)
        community_summary.append(
            {
                "id": community_id,
                "count": len(members),
                "label": nodes[top_node]["name"],
                "lang": top_language,
                "color": community_color(community_id),
                "x": center_x,
                "y": center_y,
            }
        )

    language_counts = Counter(node["lang"] for node in nodes)
    language_summary = [
        {"name": name, "count": count, "color": language_color(name)}
        for name, count in sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    # Strength-first ordering lets the UI cheaply choose the first 8k, 20k, or
    # all edges as density presets while retaining the most meaningful links.
    sorted_edge_items = sorted(
        enumerate(edges),
        key=lambda item: (-item[1][4], -item[1][2], -item[1][3], item[1][0], item[1][1]),
    )
    _, twin_edge = sorted_edge_items[0]
    twin_source, twin_target, twin_weight, twin_shared, twin_strength = twin_edge

    output_nodes: list[dict[str, Any]] = []
    for node_index, node in enumerate(nodes):
        x, y = coordinates[node_index]
        output_nodes.append(
            {
                "id": node["id"],
                "name": node["name"],
                "label": node["label"],
                "x": x,
                "y": y,
                "r": compact_number(node["r"], 3),
                "c": community_ids[node_index],
                "lang": node["lang"],
                "contributors": node["contributors"],
                "degree": degree[node_index],
                "topics": node["topics"],
                "description": node["description"],
                "url": node["url"],
            }
        )

    output_edges = [
        {
            "s": source,
            "t": target,
            "w": compact_number(weight, 6),
            "shared": shared,
            "strength": compact_number(strength, 6),
            "b": 1 if edge_index in backbone_indexes else 0,
        }
        for edge_index, (source, target, weight, shared, strength) in sorted_edge_items
    ]

    return {
        "meta": {
            "period": {
                "start": str(parameters["start_yyyymm"]),
                "end": str(parameters["end_yyyymm"]),
                "months": parameters["months"],
            },
            "nodeCount": len(output_nodes),
            "edgeCount": len(output_edges),
            "backboneCount": len(backbone_indexes),
            "componentCount": component_count,
            "mainComponent": main_component,
            "twinIds": [twin_source, twin_target],
            "twinNodeIds": [nodes[twin_source]["id"], nodes[twin_target]["id"]],
            "twinNames": [nodes[twin_source]["name"], nodes[twin_target]["name"]],
            "twinEdge": {
                "w": compact_number(twin_weight, 6),
                "shared": twin_shared,
                "strength": compact_number(twin_strength, 6),
            },
            "languages": language_summary,
            "communities": community_summary,
            "bounds": bounds,
            "edgeOrder": "strength-desc",
            "algorithm": ALGORITHM_VERSION,
            "iterations": iterations,
            "resolution": resolution,
            "layout": layout_summary,
        },
        "nodes": output_nodes,
        "edges": output_edges,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    script_path = Path(__file__).resolve()
    repository_root = script_path.parents[2]
    visualization_root = script_path.parents[1]
    default_artifact = repository_root / "artifacts" / ARTIFACT_NAME

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, default=default_artifact)
    parser.add_argument(
        "--output",
        type=Path,
        default=visualization_root / "public" / "data" / "graph.json",
    )
    parser.add_argument("--max-iterations", type=int, default=32)
    parser.add_argument("--resolution", type=float, default=1.15)
    parser.add_argument("--layout-iterations", type=int, default=108)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_iterations < 1:
        raise ValueError("--max-iterations must be positive")
    if not math.isfinite(args.resolution) or args.resolution <= 0:
        raise ValueError("--resolution must be finite and positive")
    if args.layout_iterations < 1:
        raise ValueError("--layout-iterations must be positive")

    artifact_dir = args.artifact_dir.resolve()
    output_path = args.output.resolve()
    payload = build_payload(
        artifact_dir / "nodes.csv",
        artifact_dir / "edges.csv",
        artifact_dir / "manifest.json",
        max_iterations=args.max_iterations,
        resolution=args.resolution,
        layout_iterations=args.layout_iterations,
    )
    write_json_atomic(output_path, payload)

    print(
        json.dumps(
            {
                "output": str(output_path),
                "bytes": output_path.stat().st_size,
                "nodes": payload["meta"]["nodeCount"],
                "edges": payload["meta"]["edgeCount"],
                "communities": len(payload["meta"]["communities"]),
                "languages": len(payload["meta"]["languages"]),
                "iterations": payload["meta"]["iterations"],
                "layout": payload["meta"]["layout"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
