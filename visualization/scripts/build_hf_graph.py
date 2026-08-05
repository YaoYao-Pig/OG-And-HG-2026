#!/usr/bin/env python3
"""Build a compact Hugging Face Hub graph for the OpenGalaxy visualization.

The canonical provenance is ``cfahlgren1/hub-stats``.  Its full snapshot is
roughly 2.5 GB, so this builder deliberately does not download the Parquet
files.  Instead it records the snapshot revision from the official dataset API
and reads deterministic, contiguous samples from the Dataset Viewer ``/rows``
endpoint for the ``models``, ``datasets``, and ``spaces`` configs.

Only Python's standard library is required.  All ordering, tie-breaking, graph
construction, community detection, and layout inputs are deterministic.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from build_graph_data import (
    ALGORITHM_VERSION,
    backbone_edge_indexes,
    clustered_island_layout,
    community_geometry,
    community_color,
    compact_number,
    connected_components,
    stable_color,
    stable_unit,
    weighted_label_communities,
)


SOURCE_DATASET = "cfahlgren1/hub-stats"
SOURCE_URL = f"https://huggingface.co/datasets/{SOURCE_DATASET}"
BUILD_VERSION = "hf-semantic-spine-v5-24k-compact"
USER_AGENT = "OpenGalaxy-HuggingFace-Atlas/1.0 (+https://huggingface.co)"
DATASET_VIEWER_URL = "https://datasets-server.huggingface.co"
VIEWER_PAGE_SIZE = 100
VIEWER_REQUEST_INTERVAL_SECONDS = 0.2
DEFAULT_MODEL_COUNT = 12_000
DEFAULT_DATASET_COUNT = 7_000
DEFAULT_SPACE_COUNT = 5_000

TYPE_ORDER = {"model": 0, "dataset": 1, "space": 2}
TYPE_LABEL = {"model": "Model", "dataset": "Dataset", "space": "Space"}
TYPE_COLORS = {"Model": "#8c91ff", "Dataset": "#4bc9b0", "Space": "#e6b86a"}

# Recognized task labels are used only as a fallback when a first-class
# pipeline_tag/task_categories field is absent.
KNOWN_TASKS = {
    "audio-classification",
    "audio-to-audio",
    "automatic-speech-recognition",
    "depth-estimation",
    "document-question-answering",
    "feature-extraction",
    "fill-mask",
    "image-classification",
    "image-feature-extraction",
    "image-segmentation",
    "image-text-to-text",
    "image-to-image",
    "image-to-text",
    "image-to-video",
    "mask-generation",
    "object-detection",
    "question-answering",
    "reinforcement-learning",
    "sentence-similarity",
    "summarization",
    "tabular-classification",
    "tabular-regression",
    "text-classification",
    "text-generation",
    "text-to-3d",
    "text-to-audio",
    "text-to-image",
    "text-to-speech",
    "text-to-video",
    "token-classification",
    "translation",
    "video-classification",
    "visual-question-answering",
    "zero-shot-classification",
    "zero-shot-image-classification",
}

EDGE_PRIORITY = {
    "base-model": 100,
    "declared-dataset": 95,
    "uses-model": 90,
    "uses-dataset": 89,
    "same-author": 75,
    "inferred-task": 20,
    "inferred-taxonomy": 10,
}


class APIRequestError(RuntimeError):
    """A non-transient remote API error."""


class TransientAPIError(APIRequestError):
    """A retryable remote API error that may safely use a local fallback."""


def _request_json(url: str, *, timeout: float, retries: int = 7) -> tuple[Any, str | None]:
    """Return decoded JSON and the Link header with bounded transient retries."""

    for attempt in range(retries):
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    if attempt + 1 == retries:
                        raise TransientAPIError(
                            f"Hugging Face API returned invalid JSON: {url}"
                        ) from exc
                    time.sleep(0.8 * (attempt + 1))
                    continue
                return payload, response.headers.get("Link")
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable:
                detail = exc.read(1024).decode("utf-8", errors="replace")
                raise APIRequestError(
                    f"Hugging Face API returned HTTP {exc.code}: {detail}"
                ) from exc
            if attempt + 1 == retries:
                raise TransientAPIError(
                    f"Hugging Face API remained unavailable with HTTP {exc.code}: {url}"
                ) from exc
            retry_after = exc.headers.get("Retry-After")
            try:
                retry_delay = float(retry_after) if retry_after is not None else 0.0
            except ValueError:
                retry_delay = 0.0
            if exc.code == 429:
                time.sleep(max(retry_delay, min(30.0, 1.5 * (2**attempt))))
                continue
        except (URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise TransientAPIError(f"Hugging Face API request failed: {url}") from exc
        time.sleep(0.8 * (attempt + 1))
    raise AssertionError("unreachable")


def _merge_card_data(record: dict[str, Any]) -> dict[str, Any]:
    """Merge JSON-encoded Hub card data without replacing first-class fields."""

    merged = dict(record)
    value = record.get("cardData")
    if isinstance(value, str) and value.strip():
        try:
            card_data = json.loads(value)
        except json.JSONDecodeError:
            card_data = None
    elif isinstance(value, dict):
        card_data = value
    else:
        card_data = None
    if isinstance(card_data, dict):
        for key, card_value in card_data.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = card_value

    # Card metadata occasionally uses singular values.  Normalize relationship
    # fields so graph construction never iterates through characters in a str.
    for key in ("models", "datasets"):
        relationship = merged.get(key)
        if isinstance(relationship, str):
            merged[key] = [relationship]
        elif not isinstance(relationship, list):
            merged[key] = []
    return merged


def fetch_viewer_rows(
    viewer_base: str,
    config: str,
    *,
    target: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a deterministic contiguous sample via Dataset Viewer ``/rows``."""

    viewer_base = viewer_base.rstrip("/")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    page_count = 0
    total_rows: int | None = None
    truncated_cells = 0

    while len(records) < target:
        page_length = min(VIEWER_PAGE_SIZE, target - len(records))
        query = urlencode(
            {
                "dataset": SOURCE_DATASET,
                "config": config,
                "split": "train",
                "offset": offset,
                "length": page_length,
            }
        )
        payload, _ = _request_json(f"{viewer_base}/rows?{query}", timeout=timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError(f"Unexpected Dataset Viewer response for config {config!r}")
        page_count += 1
        rows = payload["rows"]
        payload_total = payload.get("num_rows_total")
        if isinstance(payload_total, int):
            if total_rows is not None and total_rows != payload_total:
                raise ValueError(f"Dataset Viewer row count changed while reading {config!r}")
            total_rows = payload_total
        if not rows:
            break

        for expected_index, item in enumerate(rows, start=offset):
            if not isinstance(item, dict) or not isinstance(item.get("row"), dict):
                continue
            row_index = item.get("row_idx")
            if isinstance(row_index, int) and row_index != expected_index:
                raise ValueError(
                    f"Dataset Viewer returned non-contiguous rows for {config!r}: "
                    f"expected {expected_index}, got {row_index}"
                )
            record = _merge_card_data(item["row"])
            repo_id = record.get("id")
            if not isinstance(repo_id, str) or not repo_id or repo_id in seen:
                continue
            seen.add(repo_id)
            records.append(record)
            if len(records) == target:
                break
        for item in rows:
            if isinstance(item, dict) and isinstance(item.get("truncated_cells"), list):
                truncated_cells += len(item["truncated_cells"])
        offset += len(rows)
        if page_count % 20 == 0 or len(records) >= target:
            print(
                f"Dataset Viewer {config}: {len(records):,}/{target:,} rows "
                f"({page_count} pages)",
                file=sys.stderr,
                flush=True,
            )
        if len(records) < target:
            time.sleep(VIEWER_REQUEST_INTERVAL_SECONDS)

    if len(records) < target:
        raise ValueError(f"Only received {len(records)} of {target} requested {config} rows")
    sampling = {
        "config": config,
        "split": "train",
        "offset": 0,
        "endOffsetExclusive": offset,
        "count": len(records),
        "pageSize": VIEWER_PAGE_SIZE,
        "pageCount": page_count,
        "sampling": "contiguous-dataset-order",
        "totalRows": total_rows,
        "truncatedCells": truncated_cells,
    }
    return records, sampling


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return max(0.0, float(value))
    return 0.0


def _tags(record: dict[str, Any]) -> list[str]:
    values = record.get("tags")
    if not isinstance(values, list):
        return []
    return sorted({value.strip() for value in values if isinstance(value, str) and value.strip()})


def _prefixed_values(tags: list[str], prefix: str) -> list[str]:
    return sorted({tag[len(prefix) :] for tag in tags if tag.startswith(prefix) and len(tag) > len(prefix)})


def _clean_text(value: Any, *, limit: int = 220) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _author(record: dict[str, Any], repo_id: str) -> str:
    value = record.get("author")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return repo_id.split("/", 1)[0] if "/" in repo_id else "Unknown"


def _task_candidates(kind: str, record: dict[str, Any], tags: list[str]) -> list[str]:
    tasks: list[str] = []
    if kind == "model":
        pipeline = record.get("pipeline_tag")
        if isinstance(pipeline, str) and pipeline.strip():
            tasks.append(pipeline.strip())
    tasks.extend(_prefixed_values(tags, "task_categories:"))
    tasks.extend(tag for tag in tags if tag in KNOWN_TASKS)
    return list(dict.fromkeys(tasks))


def _library(kind: str, record: dict[str, Any], tags: list[str]) -> str:
    if kind == "model":
        value = record.get("library_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
        ignored = KNOWN_TASKS | {"pytorch", "safetensors", "onnx", "tf", "jax", "region:us"}
        for tag in tags:
            if ":" not in tag and tag not in ignored:
                return tag
        return "model-hub"
    if kind == "dataset":
        libraries = _prefixed_values(tags, "library:")
        return libraries[0] if libraries else "datasets"
    value = record.get("sdk")
    return value.strip() if isinstance(value, str) and value.strip() else "static"


def _domain(task: str) -> str:
    value = task.lower()
    if any(token in value for token in ("multimodal", "image-text", "visual-question", "document-question")):
        return "Multimodal"
    if any(token in value for token in ("image", "video", "vision", "object", "depth", "segment", "3d")):
        return "Vision & Video"
    if any(token in value for token in ("audio", "speech", "voice")):
        return "Audio & Speech"
    if any(token in value for token in ("embedding", "similarity", "retrieval", "feature-extraction")):
        return "Embeddings & Retrieval"
    if any(token in value for token in ("tabular", "graph", "time-series")):
        return "Structured Data"
    if any(token in value for token in ("reinforcement", "robot")):
        return "Agents & Robotics"
    if any(
        token in value
        for token in (
            "text",
            "question",
            "translation",
            "summar",
            "token",
            "mask",
            "conversational",
        )
    ):
        return "Language"
    if "dataset" in value or "classification" in value or "evaluation" in value:
        return "Data & Evaluation"
    if "interactive" in value or "space" in value:
        return "Interactive Apps"
    return "Other AI"


def _display_topics(tags: list[str]) -> list[str]:
    excluded_prefixes = (
        "base_model:",
        "dataset:",
        "license:",
        "region:",
        "arxiv:",
        "doi:",
        "size_categories:",
    )
    filtered = [tag for tag in tags if not tag.startswith(excluded_prefixes)]
    return filtered[:3]


def prepare_nodes(
    model_records: list[dict[str, Any]],
    dataset_records: list[dict[str, Any]],
    space_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], int]]:
    """Normalize projected Hub records into Galaxy nodes plus private evidence."""

    records_by_kind = {
        "model": model_records,
        "dataset": dataset_records,
        "space": space_records,
    }
    prepared: list[dict[str, Any]] = []

    # Models and datasets establish task metadata used to classify linked Spaces.
    tasks_by_repo: dict[tuple[str, str], list[str]] = {}
    for kind in ("model", "dataset"):
        for record in records_by_kind[kind]:
            repo_id = str(record["id"])
            tags = _tags(record)
            tasks_by_repo[(kind, repo_id)] = _task_candidates(kind, record, tags)

    for kind in ("model", "dataset", "space"):
        for record in records_by_kind[kind]:
            repo_id = str(record["id"])
            tags = _tags(record)
            tasks = _task_candidates(kind, record, tags)
            if kind == "space" and not tasks:
                linked_tasks: list[str] = []
                for model_id in record.get("models") or ():
                    if isinstance(model_id, str):
                        linked_tasks.extend(tasks_by_repo.get(("model", model_id), ()))
                for dataset_id in record.get("datasets") or ():
                    if isinstance(dataset_id, str):
                        linked_tasks.extend(tasks_by_repo.get(("dataset", dataset_id), ()))
                if linked_tasks:
                    task_counts = Counter(linked_tasks)
                    tasks = [min(task_counts, key=lambda task: (-task_counts[task], task))]

            if not tasks:
                tasks = [
                    "unspecified-model"
                    if kind == "model"
                    else "unspecified-dataset"
                    if kind == "dataset"
                    else "interactive-demo"
                ]

            primary_task = tasks[0]
            library = _library(kind, record, tags)
            downloads = int(_number(record.get("downloads")))
            likes = int(_number(record.get("likes")))
            trending = _number(record.get("trendingScore"))
            rank = (
                math.log1p(downloads)
                + 1.65 * math.log1p(likes)
                + 1.9 * math.log1p(trending)
                + {"model": 0.35, "dataset": 0.22, "space": 0.12}[kind]
            )
            description = _clean_text(record.get("description"), limit=96)

            linked_models = sorted(
                {value for value in (record.get("models") or ()) if isinstance(value, str)}
            )
            linked_datasets = sorted(
                {value for value in (record.get("datasets") or ()) if isinstance(value, str)}
            )
            base_models: list[tuple[str, str]] = []
            base_value = record.get("baseModels")
            if isinstance(base_value, dict):
                relation = str(base_value.get("relation") or "derived")
                for item in base_value.get("models") or ():
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        base_models.append((item["id"], relation))

            prepared.append(
                {
                    "id": f"hf:{kind}:{repo_id}",
                    "repoId": repo_id,
                    "name": repo_id,
                    "label": repo_id,
                    "r": max(rank, 0.1),
                    "lang": library,
                    "contributors": 0,
                    "topics": _display_topics(tags),
                    "description": description,
                    "url": (
                        f"https://huggingface.co/{repo_id}"
                        if kind == "model"
                        else f"https://huggingface.co/datasets/{repo_id}"
                        if kind == "dataset"
                        else f"https://huggingface.co/spaces/{repo_id}"
                    ),
                    "sourceType": kind,
                    "author": _author(record, repo_id),
                    "likes": likes,
                    "downloads": downloads,
                    "trending": compact_number(trending, 3),
                    "updatedAt": str(record.get("lastModified") or ""),
                    "areas": {
                        "type": {
                            "primary": TYPE_LABEL[kind],
                            "source": "hub-stats-dataset-viewer",
                            "confidence": 1,
                        },
                        "task": {
                            "primary": primary_task,
                            "tags": tasks,
                            "source": "hub-stats-card-metadata",
                            "confidence": 1 if primary_task not in {"unspecified-model", "unspecified-dataset", "interactive-demo"} else 0.5,
                            "isAi": True,
                        },
                        "library": {
                            "primary": library,
                            "source": "hub-stats-card-metadata",
                            "confidence": 0.95,
                        },
                    },
                    "_task": primary_task,
                    "_domain": _domain(primary_task),
                    "_tags": tags,
                    "_baseModels": sorted(base_models),
                    "_linkedModels": linked_models,
                    "_linkedDatasets": linked_datasets,
                }
            )

    prepared.sort(key=lambda node: (TYPE_ORDER[node["sourceType"]], node["repoId"]))
    index_by_repo = {
        (node["sourceType"], node["repoId"]): index for index, node in enumerate(prepared)
    }
    return prepared, index_by_repo


def build_edges(
    nodes: list[dict[str, Any]],
    index_by_repo: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    """Build observed edges first, then a sparse and explicit inferred spine."""

    edges_by_pair: dict[tuple[int, int], dict[str, Any]] = {}

    def add_edge(source: int, target: int, weight: float, kind: str, inferred: int) -> None:
        if source == target:
            return
        pair = (min(source, target), max(source, target))
        candidate = {
            "s": source,
            "t": target,
            "w": weight,
            "shared": 1,
            "strength": weight,
            "kind": kind,
            "inferred": inferred,
        }
        current = edges_by_pair.get(pair)
        if current is None:
            edges_by_pair[pair] = candidate
            return
        current_priority = EDGE_PRIORITY[current["kind"]]
        candidate_priority = EDGE_PRIORITY[kind]
        if candidate_priority > current_priority or (
            candidate_priority == current_priority and weight > current["w"]
        ):
            edges_by_pair[pair] = candidate
        elif candidate_priority == current_priority and kind == current["kind"]:
            current["shared"] += 1

    # First-class relationships declared by Hub metadata.
    for source, node in enumerate(nodes):
        if node["sourceType"] == "model":
            for base_id, relation in node["_baseModels"]:
                target = index_by_repo.get(("model", base_id))
                if target is not None:
                    relation_boost = 1.0 if relation in {"finetune", "adapter"} else 0.5
                    add_edge(source, target, 9.0 + relation_boost, "base-model", 0)
            declared = _prefixed_values(node["_tags"], "dataset:")
            for dataset_id in declared[:8]:
                target = index_by_repo.get(("dataset", dataset_id))
                if target is not None:
                    add_edge(source, target, 7.5, "declared-dataset", 0)
        elif node["sourceType"] == "space":
            for model_id in node["_linkedModels"][:12]:
                target = index_by_repo.get(("model", model_id))
                if target is not None:
                    add_edge(source, target, 8.0, "uses-model", 0)
            for dataset_id in node["_linkedDatasets"][:12]:
                target = index_by_repo.get(("dataset", dataset_id))
                if target is not None:
                    add_edge(source, target, 7.7, "uses-dataset", 0)

    # Ownership is observed, but use one importance-ranked hub per author to
    # avoid quadratic cliques for prolific organizations.
    by_author: dict[str, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        by_author[node["author"]].append(index)
    for author in sorted(by_author):
        members = sorted(
            by_author[author],
            key=lambda index: (-nodes[index]["r"], nodes[index]["id"]),
        )
        if len(members) < 2:
            continue
        hub = members[0]
        for target in members[1:]:
            add_edge(hub, target, 4.2, "same-author", 0)

    # Exact task groups become deterministic topology filaments rather than
    # dense similarity cliques.  These edges are intentionally marked inferred.
    by_task: dict[str, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        by_task[node["_task"]].append(index)
    task_representatives: dict[str, int] = {}
    for task in sorted(by_task):
        members = by_task[task]
        task_representatives[task] = min(
            members,
            key=lambda index: (-nodes[index]["r"], nodes[index]["id"]),
        )
        ordered = sorted(
            members,
            key=lambda index: (stable_unit(f"hf-task:{task}:{nodes[index]['id']}"), nodes[index]["id"]),
        )
        for left, right in zip(ordered, ordered[1:]):
            add_edge(left, right, 1.35, "inferred-task", 1)

    # Connect related task families and then their domain representatives with
    # very light taxonomy edges so continuous_disc_layout receives one body.
    tasks_by_domain: dict[str, list[str]] = defaultdict(list)
    for task in task_representatives:
        tasks_by_domain[_domain(task)].append(task)
    domain_representatives: list[int] = []
    for domain in sorted(tasks_by_domain):
        task_names = sorted(tasks_by_domain[domain])
        representative = min(
            (task_representatives[task] for task in task_names),
            key=lambda index: (-nodes[index]["r"], nodes[index]["id"]),
        )
        domain_representatives.append(representative)
        for task in task_names:
            add_edge(representative, task_representatives[task], 0.72, "inferred-task", 1)
    domain_representatives.sort(key=lambda index: nodes[index]["_domain"])
    for left, right in zip(domain_representatives, domain_representatives[1:]):
        add_edge(left, right, 0.28, "inferred-taxonomy", 1)

    return sorted(
        edges_by_pair.values(),
        key=lambda edge: (edge["s"], edge["t"], -EDGE_PRIORITY[edge["kind"]], edge["kind"]),
    )


def facet_summary(
    nodes: list[dict[str, Any]],
    facet: str,
) -> list[dict[str, Any]]:
    members: dict[str, list[int]] = defaultdict(list)
    for index, node in enumerate(nodes):
        members[node["areas"][facet]["primary"]].append(index)
    summary: list[dict[str, Any]] = []
    for name, indexes in members.items():
        representative = min(
            indexes,
            key=lambda index: (-nodes[index]["r"], nodes[index]["id"]),
        )
        summary.append(
            {
                "name": name,
                "count": len(indexes),
                "representative": nodes[representative]["label"],
                "color": TYPE_COLORS.get(name, stable_color(f"hf-{facet}:{name}", saturation=0.6, lightness=0.64)),
            }
        )
    return sorted(summary, key=lambda item: (-item["count"], item["name"]))


def build_payload(
    provenance: dict[str, Any],
    sampling: dict[str, dict[str, Any]],
    model_records: list[dict[str, Any]],
    dataset_records: list[dict[str, Any]],
    space_records: list[dict[str, Any]],
    *,
    layout_iterations: int,
    max_iterations: int,
    resolution: float,
) -> dict[str, Any]:
    nodes, index_by_repo = prepare_nodes(model_records, dataset_records, space_records)
    edge_records = build_edges(nodes, index_by_repo)

    edge_tuples = [
        (edge["s"], edge["t"], edge["w"], edge["shared"], edge["strength"])
        for edge in edge_records
    ]
    degree = [0] * len(nodes)
    adjacency: list[list[tuple[int, float]]] = [[] for _ in nodes]
    for source, target, weight, _, strength in edge_tuples:
        affinity = math.log1p(weight) + 0.55 * math.log1p(strength)
        adjacency[source].append((target, affinity))
        adjacency[target].append((source, affinity))
        degree[source] += 1
        degree[target] += 1
    for neighbors in adjacency:
        neighbors.sort(key=lambda item: item[0])

    communities, community_iterations = weighted_label_communities(
        nodes,
        adjacency,
        max_iterations=max_iterations,
        resolution=resolution,
    )
    components = connected_components(len(nodes), edge_tuples)
    backbone = backbone_edge_indexes(len(nodes), edge_tuples)
    coordinates, bounds, layout, macro_communities = clustered_island_layout(
        nodes,
        communities,
        degree,
        edge_tuples,
        backbone,
        components,
        iterations=layout_iterations,
    )

    community_members: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(macro_communities):
        community_members[community_id].append(node_index)
    community_summary: list[dict[str, Any]] = []
    main_component_set = set(components[0])
    for community_id in range(len(community_members)):
        members = community_members[community_id]
        core_members = [index for index in members if index in main_component_set] or members
        top = min(core_members, key=lambda index: (-nodes[index]["r"], nodes[index]["id"]))
        geometry = community_geometry(coordinates, core_members)
        community_summary.append(
            {
                "id": community_id,
                "count": len(members),
                "label": nodes[top]["label"],
                "lang": nodes[top]["lang"],
                "color": community_color(community_id),
                **geometry,
                "microCommunityCount": len({communities[index] for index in members}),
            }
        )

    sorted_edge_indexes = sorted(
        range(len(edge_records)),
        key=lambda index: (
            -edge_records[index]["strength"],
            -edge_records[index]["w"],
            -edge_records[index]["shared"],
            edge_records[index]["s"],
            edge_records[index]["t"],
        ),
    )
    output_nodes: list[dict[str, Any]] = []
    private_fields = {
        "repoId",
        "author",
        "lang",
        "updatedAt",
        "_task",
        "_domain",
        "_tags",
        "_baseModels",
        "_linkedModels",
        "_linkedDatasets",
    }
    # `label` duplicates `name` and is only used to select overview callouts.
    # Retain it for a generous importance-ranked landmark set, not all 24k rows.
    landmark_indexes = set(
        sorted(
            range(len(nodes)),
            key=lambda index: (-nodes[index]["r"], nodes[index]["id"]),
        )[: min(512, len(nodes))]
    )
    for index, node in enumerate(nodes):
        output = {key: value for key, value in node.items() if key not in private_fields}
        if index not in landmark_indexes:
            output.pop("label", None)
        if not output.get("description"):
            output.pop("description", None)
        output["areas"] = {
            facet: {
                key: value
                for key, value in area.items()
                if key in {"primary", "tags"}
            }
            for facet, area in output["areas"].items()
        }
        output.update(
            {
                "x": coordinates[index][0],
                "y": coordinates[index][1],
                "r": compact_number(node["r"], 3),
                "c": macro_communities[index],
                "mc": communities[index],
                "degree": degree[index],
            }
        )
        output_nodes.append(output)

    output_edges = []
    for index in sorted_edge_indexes:
        edge = edge_records[index]
        output_edges.append(
            {
                "s": edge["s"],
                "t": edge["t"],
                "strength": compact_number(edge["strength"], 3),
                "b": 1 if index in backbone else 0,
                "kind": edge["kind"],
                "inferred": edge["inferred"],
            }
        )

    relation_kinds = Counter(edge["kind"] for edge in edge_records)
    inferred_count = sum(edge["inferred"] for edge in edge_records)
    entity_counts = Counter(node["sourceType"] for node in nodes)
    source_revision = str(provenance.get("sha") or "")
    source_last_modified = str(provenance.get("lastModified") or "")
    card_data = provenance.get("cardData")
    source_license = card_data.get("license") if isinstance(card_data, dict) else None

    strongest_index = sorted_edge_indexes[0]
    strongest = edge_records[strongest_index]
    return {
        "meta": {
            "source": "huggingface",
            "sourceUrl": SOURCE_URL,
            "sourceRevision": source_revision,
            "sourceLastModified": source_last_modified,
            "sourceLicense": source_license,
            "generatedAt": source_last_modified,
            "snapshotAt": source_last_modified,
            "provenance": {
                "dataset": SOURCE_DATASET,
                "url": SOURCE_URL,
                "apiUrl": f"https://huggingface.co/api/datasets/{SOURCE_DATASET}",
                "datasetViewerUrl": f"{DATASET_VIEWER_URL}/rows",
                "revision": source_revision,
                "lastModified": source_last_modified,
                "license": source_license,
                "method": "dataset-viewer-row-sample",
                "samples": sampling,
            },
            "nodeCount": len(output_nodes),
            "edgeCount": len(output_edges),
            "backboneCount": len(backbone),
            "componentCount": len(components),
            "mainComponent": len(components[0]),
            "entityCounts": dict(sorted(entity_counts.items())),
            "relationKinds": dict(sorted(relation_kinds.items())),
            "edgeSemantics": {
                "observed": len(edge_records) - inferred_count,
                "inferred": inferred_count,
                "byKind": dict(sorted(relation_kinds.items())),
            },
            "facets": {
                "type": facet_summary(nodes, "type"),
                "task": facet_summary(nodes, "task"),
                "library": facet_summary(nodes, "library"),
            },
            "facetOrder": ["type", "task", "library"],
            "communities": community_summary,
            "bounds": bounds,
            "edgeOrder": "strength-desc",
            "algorithm": f"{ALGORITHM_VERSION}+{BUILD_VERSION}",
            "iterations": community_iterations,
            "resolution": resolution,
            "layout": layout,
            "buildInputs": {
                "communityMaxIterations": max_iterations,
                "layoutIterations": layout_iterations,
                "resolution": resolution,
            },
            "serialization": {
                "landmarkLabels": len(landmark_indexes),
                "topicLimit": 3,
                "descriptionLimit": 96,
                "omittedNodeFields": ["author", "lang", "updatedAt"],
                "omittedEdgeFields": ["w", "shared"],
                "compactAreaFields": ["primary", "tags"],
            },
            "twinIds": [strongest["s"], strongest["t"]],
            "twinNodeIds": [nodes[strongest["s"]]["id"], nodes[strongest["t"]]["id"]],
            "twinNames": [nodes[strongest["s"]]["label"], nodes[strongest["t"]]["label"]],
            "twinEdge": {
                "w": strongest["w"],
                "shared": strongest["shared"],
                "strength": strongest["strength"],
                "kind": strongest["kind"],
            },
            "sampling": sampling,
        },
        "nodes": output_nodes,
        "edges": output_edges,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
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
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    script_path = Path(__file__).resolve()
    visualization_root = script_path.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=visualization_root / "public" / "data" / "huggingface.json",
    )
    parser.add_argument("--api-base", default="https://huggingface.co")
    parser.add_argument("--viewer-base", default=DATASET_VIEWER_URL)
    parser.add_argument("--models", type=int, default=DEFAULT_MODEL_COUNT)
    parser.add_argument("--datasets", type=int, default=DEFAULT_DATASET_COUNT)
    parser.add_argument("--spaces", type=int, default=DEFAULT_SPACE_COUNT)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-iterations", type=int, default=32)
    parser.add_argument("--resolution", type=float, default=1.15)
    parser.add_argument("--layout-iterations", type=int, default=84)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-sample even when the output already matches this source revision.",
    )
    return parser.parse_args(argv)


def usable_existing_payload(
    output: Path,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    """Load an existing graph that is safe to preserve during an API outage."""

    if not output.is_file():
        return None
    try:
        with output.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        meta = payload["meta"]
        nodes = payload["nodes"]
        edges = payload["edges"]
        expected_counts = {
            "model": args.models,
            "dataset": args.datasets,
            "space": args.spaces,
        }
        if (
            meta.get("source") != "huggingface"
            or meta.get("nodeCount") != sum(expected_counts.values())
            or meta.get("entityCounts") != expected_counts
            or not isinstance(nodes, list)
            or len(nodes) != sum(expected_counts.values())
            or not isinstance(edges, list)
            or len(edges) != meta.get("edgeCount")
            or len({node.get("id") for node in nodes if isinstance(node, dict)}) != len(nodes)
        ):
            return None
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None


def current_cached_payload(
    output: Path,
    provenance: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    """Reuse an immutable snapshot while its provenance and build inputs match."""

    if args.force:
        return None
    payload = usable_existing_payload(output, args)
    if payload is None:
        return None
    try:
        meta = payload["meta"]
        sampling = meta["sampling"]
        build_inputs = meta["buildInputs"]
        if (
            meta.get("sourceRevision") == provenance.get("sha")
            and meta.get("algorithm") == f"{ALGORITHM_VERSION}+{BUILD_VERSION}"
            and meta.get("provenance", {}).get("method") == "dataset-viewer-row-sample"
            and float(meta.get("resolution")) == args.resolution
            and int(meta["layout"]["iterations"]) == args.layout_iterations
            and int(build_inputs["communityMaxIterations"]) == args.max_iterations
            and int(build_inputs["layoutIterations"]) == args.layout_iterations
            and float(build_inputs["resolution"]) == args.resolution
            and int(sampling["models"]["count"]) == args.models
            and int(sampling["datasets"]["count"]) == args.datasets
            and int(sampling["spaces"]["count"]) == args.spaces
            and sampling["models"].get("config") == "models"
            and sampling["datasets"].get("config") == "datasets"
            and sampling["spaces"].get("config") == "spaces"
            and all(
                sampling[name].get("split") == "train"
                and sampling[name].get("offset") == 0
                and sampling[name].get("sampling") == "contiguous-dataset-order"
                for name in ("models", "datasets", "spaces")
            )
        ):
            return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None
    return None


def preserve_existing_after_transient_failure(
    output: Path,
    args: argparse.Namespace,
    error: TransientAPIError,
) -> int:
    """Keep a usable artifact when Hugging Face is temporarily unavailable."""

    existing = usable_existing_payload(output, args)
    if existing is None:
        raise error
    warning = (
        "WARNING: Hugging Face is temporarily unavailable; preserving the existing "
        f"graph artifact ({error})."
    )
    print(warning, file=sys.stderr)
    print(
        json.dumps(
            {
                "output": str(output),
                "bytes": output.stat().st_size,
                "cached": True,
                "fallback": "transient-api-failure",
                "warning": warning,
                "nodes": existing["meta"]["nodeCount"],
                "edges": existing["meta"]["edgeCount"],
                "components": existing["meta"]["componentCount"],
                "sourceRevision": existing["meta"]["sourceRevision"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for name in ("models", "datasets", "spaces", "max_iterations", "layout_iterations"):
        if getattr(args, name) < 1:
            raise ValueError(f"--{name.replace('_', '-')} must be positive")
    if args.models + args.datasets + args.spaces < 1200:
        raise ValueError("The Hugging Face graph must contain at least 1,200 nodes")
    if not math.isfinite(args.resolution) or args.resolution <= 0:
        raise ValueError("--resolution must be finite and positive")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("--timeout must be finite and positive")

    api_base = args.api_base.rstrip("/")
    viewer_base = args.viewer_base.rstrip("/")
    output = args.output.resolve()
    try:
        provenance, _ = _request_json(
            f"{api_base}/api/datasets/{SOURCE_DATASET}",
            timeout=args.timeout,
        )
    except TransientAPIError as error:
        return preserve_existing_after_transient_failure(output, args, error)
    if not isinstance(provenance, dict):
        raise ValueError("Unexpected provenance response")
    cached = current_cached_payload(output, provenance, args)
    if cached is not None:
        print(
            json.dumps(
                {
                    "output": str(output),
                    "bytes": output.stat().st_size,
                    "cached": True,
                    "nodes": cached["meta"]["nodeCount"],
                    "edges": cached["meta"]["edgeCount"],
                    "components": cached["meta"]["componentCount"],
                    "sourceRevision": cached["meta"]["sourceRevision"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    try:
        model_records, model_sampling = fetch_viewer_rows(
            viewer_base,
            "models",
            target=args.models,
            timeout=args.timeout,
        )
        dataset_records, dataset_sampling = fetch_viewer_rows(
            viewer_base,
            "datasets",
            target=args.datasets,
            timeout=args.timeout,
        )
        space_records, space_sampling = fetch_viewer_rows(
            viewer_base,
            "spaces",
            target=args.spaces,
            timeout=args.timeout,
        )
        confirmed_provenance, _ = _request_json(
            f"{api_base}/api/datasets/{SOURCE_DATASET}",
            timeout=args.timeout,
        )
    except TransientAPIError as error:
        return preserve_existing_after_transient_failure(output, args, error)
    if not isinstance(confirmed_provenance, dict):
        raise ValueError("Unexpected provenance confirmation response")
    if confirmed_provenance.get("sha") != provenance.get("sha"):
        return preserve_existing_after_transient_failure(
            output,
            args,
            TransientAPIError(
                "The hub-stats revision changed while Dataset Viewer rows were being sampled"
            ),
        )
    sampling = {
        "models": model_sampling,
        "datasets": dataset_sampling,
        "spaces": space_sampling,
    }
    payload = build_payload(
        provenance,
        sampling,
        model_records,
        dataset_records,
        space_records,
        layout_iterations=args.layout_iterations,
        max_iterations=args.max_iterations,
        resolution=args.resolution,
    )
    write_json_atomic(output, payload)
    print(
        json.dumps(
            {
                "output": str(output),
                "bytes": output.stat().st_size,
                "nodes": payload["meta"]["nodeCount"],
                "edges": payload["meta"]["edgeCount"],
                "components": payload["meta"]["componentCount"],
                "sourceRevision": payload["meta"]["sourceRevision"],
                "relationKinds": payload["meta"]["relationKinds"],
                "layout": payload["meta"]["layout"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
