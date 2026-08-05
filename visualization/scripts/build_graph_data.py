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
import re
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ARTIFACT_NAME = "open-galaxy-github-202508-202607-final"
ALGORITHM_VERSION = "hierarchical-clustered-galaxy-v4"
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


# Tech-0's broad software taxonomy, expanded with common GitHub topic and
# description vocabulary.  Ordering is meaningful: it is the deterministic
# tie-breaker used when two domains receive the same score.
DOMAIN_ORDER = (
    "AI",
    "Agentic AI",
    "Application Software",
    "Big Data",
    "Blockchain",
    "Cloud Native",
    "Database",
    "Development Tools",
    "IoT",
    "Operating System",
    "Programming Language",
    "Web Frameworks",
)

DOMAIN_KEYWORDS = {
    "Application Software": (
        "application",
        "app",
        "desktop",
        "mobile",
        "android",
        "ios",
        "productivity",
        "collaboration",
        "ecommerce",
        "e-commerce",
        "cms",
        "content management",
        "erp",
        "crm",
        "chat",
        "browser",
        "media player",
        "video",
        "music",
        "game",
        "gaming",
        "home assistant",
        "self hosted",
        "dashboard",
        "email client",
        "note taking",
    ),
    "Big Data": (
        "big data",
        "analytics",
        "data analytics",
        "data engineering",
        "data pipeline",
        "data processing",
        "data visualization",
        "business intelligence",
        "olap",
        "etl",
        "elt",
        "streaming",
        "stream processing",
        "apache spark",
        "spark",
        "hadoop",
        "flink",
        "kafka",
        "airflow",
        "dbt",
        "data lake",
        "data warehouse",
        "lakehouse",
        "arrow",
        "parquet",
        "scientific computing",
        "data science",
    ),
    "Blockchain": (
        "blockchain",
        "web3",
        "ethereum",
        "bitcoin",
        "cryptocurrency",
        "crypto currency",
        "smart contract",
        "solidity",
        "defi",
        "decentralized",
        "distributed ledger",
        "wallet",
        "nft",
    ),
    "Cloud Native": (
        "cloud native",
        "kubernetes",
        "k8s",
        "docker",
        "container",
        "containers",
        "containerd",
        "cncf",
        "devops",
        "sre",
        "observability",
        "opentelemetry",
        "prometheus",
        "service mesh",
        "microservices",
        "serverless",
        "terraform",
        "helm",
        "orchestration",
        "cloud computing",
        "aws",
        "azure",
        "openstack",
        "infrastructure as code",
        "iac",
        "ci cd",
        "continuous delivery",
        "distributed tracing",
        "monitoring",
    ),
    "Database": (
        "database",
        "dbms",
        "sql",
        "nosql",
        "postgresql",
        "postgres",
        "mysql",
        "sqlite",
        "mongodb",
        "redis",
        "elasticsearch",
        "search engine",
        "vector database",
        "time series database",
        "distributed database",
        "query engine",
        "storage engine",
        "orm",
        "cache",
    ),
    "Development Tools": (
        "developer tools",
        "development tools",
        "devtool",
        "ide",
        "editor",
        "code editor",
        "vscode",
        "neovim",
        "cli",
        "command line",
        "terminal",
        "sdk",
        "api",
        "library",
        "build tool",
        "package manager",
        "testing",
        "test framework",
        "linter",
        "formatter",
        "static analysis",
        "debugger",
        "profiling",
        "compiler toolchain",
        "git",
        "github",
        "workflow",
        "automation",
        "documentation",
        "reverse engineering",
        "security tools",
        "code quality",
        "monorepo",
    ),
    "IoT": (
        "iot",
        "internet of things",
        "embedded",
        "embedded systems",
        "firmware",
        "microcontroller",
        "arduino",
        "esp32",
        "raspberry pi",
        "mqtt",
        "home automation",
        "sensor",
        "edge computing",
    ),
    "Operating System": (
        "operating system",
        "os kernel",
        "kernel",
        "linux",
        "nixos",
        "unix",
        "bsd",
        "windows",
        "macos",
        "android os",
        "filesystem",
        "file system",
        "bootloader",
        "systemd",
        "virtualization",
        "hypervisor",
        "emulator",
    ),
    "Programming Language": (
        "programming language",
        "compiler",
        "interpreter",
        "language runtime",
        "runtime system",
        "virtual machine",
        "bytecode",
        "parser",
        "lexer",
        "llvm",
        "webassembly",
        "wasm",
        "language server",
        "type system",
    ),
    "Web Frameworks": (
        "web framework",
        "frontend framework",
        "frontend",
        "backend framework",
        "full stack",
        "react",
        "reactjs",
        "vue",
        "vuejs",
        "angular",
        "nextjs",
        "next.js",
        "nuxt",
        "svelte",
        "django",
        "flask",
        "fastapi",
        "laravel",
        "rails",
        "spring boot",
        "nodejs",
        "express",
        "tailwindcss",
        "web components",
        "ui components",
        "component library",
        "design system",
        "wordpress",
    ),
}

AI_SUBCATEGORY_ORDER = (
    "LLM & Generative AI",
    "Agents & RAG",
    "ML Frameworks",
    "Computer Vision",
    "NLP & Speech",
    "Data / Evaluation / MLOps",
    "Robotics",
    "AI Applications",
)

AI_SUBCATEGORY_KEYWORDS = {
    "LLM & Generative AI": (
        "llm",
        "llms",
        "large language model",
        "large language models",
        "generative ai",
        "genai",
        "gpt",
        "chatgpt",
        "claude",
        "gemini",
        "deepseek",
        "qwen",
        "llama",
        "transformer",
        "diffusion",
        "stable diffusion",
        "text generation",
        "image generation",
        "multimodal",
        "foundation model",
        "fine tuning",
        "prompt engineering",
    ),
    "Agents & RAG": (
        "ai agent",
        "ai agents",
        "agentic ai",
        "agentic",
        "agents",
        "rag",
        "retrieval augmented generation",
        "mcp",
        "mcp server",
        "model context protocol",
        "langchain",
        "llamaindex",
        "autogen",
        "crew ai",
        "tool calling",
        "function calling",
        "multi agent",
    ),
    "ML Frameworks": (
        "machine learning",
        "deep learning",
        "ml framework",
        "neural network",
        "pytorch",
        "tensorflow",
        "keras",
        "jax",
        "scikit learn",
        "sklearn",
        "xgboost",
        "lightgbm",
        "onnx",
        "cuda",
        "tensor",
        "autograd",
    ),
    "Computer Vision": (
        "computer vision",
        "image recognition",
        "image classification",
        "object detection",
        "image segmentation",
        "vision transformer",
        "opencv",
        "yolo",
        "ocr",
        "visual recognition",
        "3d vision",
    ),
    "NLP & Speech": (
        "natural language processing",
        "nlp",
        "speech recognition",
        "speech synthesis",
        "text to speech",
        "tts",
        "speech to text",
        "stt",
        "automatic speech recognition",
        "asr",
        "whisper",
        "voice assistant",
        "audio generation",
        "translation model",
    ),
    "Data / Evaluation / MLOps": (
        "mlops",
        "llmops",
        "model evaluation",
        "llm evaluation",
        "evaluation",
        "benchmark",
        "ai benchmark",
        "dataset",
        "data labeling",
        "feature store",
        "model monitoring",
        "experiment tracking",
        "model serving",
        "inference server",
        "inference engine",
        "vector search",
        "embedding",
        "embeddings",
    ),
    "Robotics": (
        "robotics",
        "robot",
        "ros",
        "robot operating system",
        "autonomous driving",
        "self driving",
        "slam",
        "motion planning",
        "reinforcement learning",
    ),
    "AI Applications": (
        "artificial intelligence",
        "ai",
        "ai application",
        "ai assistant",
        "personal assistant",
        "chatbot",
        "copilot",
        "code assistant",
        "ai coding",
        "recommendation system",
        "recommender system",
        "intelligent assistant",
    ),
}

DOMAIN_COLORS = {
    "AI": "#6f78ff",
    "Agentic AI": "#23c5c9",
    "Application Software": "#d77ad8",
    "Big Data": "#65b9ab",
    "Blockchain": "#e0ad55",
    "Cloud Native": "#5ca9da",
    "Database": "#78b86d",
    "Development Tools": "#a4a9b5",
    "IoT": "#dd8756",
    "Operating System": "#cf6c78",
    "Programming Language": "#9a7bd5",
    "Web Frameworks": "#cb7895",
}

AI_COLORS = {
    "LLM & Generative AI": "#777cff",
    "Agents & RAG": "#2fc3c7",
    "ML Frameworks": "#a184d8",
    "Computer Vision": "#d9769a",
    "NLP & Speech": "#739cd7",
    "Data / Evaluation / MLOps": "#6caf9c",
    "Robotics": "#d58d5e",
    "AI Applications": "#c9a15a",
    "Non-AI": "#92979e",
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


def normalize_taxonomy_text(value: str) -> str:
    """Normalize prose and GitHub topics for phrase-safe lexical matching."""

    return " ".join(re.sub(r"[^a-z0-9+#.]+", " ", value.casefold()).split())


def contains_taxonomy_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    if not normalized_text or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {normalized_text} "


def keyword_evidence(
    keywords: tuple[str, ...],
    topic_values: tuple[str, ...],
    description: str,
) -> tuple[float, list[tuple[str, float]], set[str]]:
    """Score a keyword list, weighting curated topics above prose mentions."""

    matches: list[tuple[str, float]] = []
    sources: set[str] = set()
    for keyword in keywords:
        normalized_keyword = normalize_taxonomy_text(keyword)
        contribution = 0.0
        topic_hit = False
        for topic in topic_values:
            if topic == normalized_keyword:
                contribution = max(contribution, 4.6)
                topic_hit = True
            elif contains_taxonomy_phrase(topic, normalized_keyword):
                contribution = max(contribution, 3.1)
                topic_hit = True
        if topic_hit:
            sources.add("topics")
        if contains_taxonomy_phrase(description, normalized_keyword):
            contribution += 1.15 if len(normalized_keyword) > 2 else 0.9
            sources.add("description")
        if contribution:
            matches.append((keyword, contribution))
    matches.sort(key=lambda item: (-item[1], item[0]))
    return sum(contribution for _, contribution in matches), matches, sources


def taxonomy_inputs(node: dict[str, Any]) -> tuple[tuple[str, ...], str]:
    topics = tuple(
        normalized
        for topic in node["topics"].split("|")
        if (normalized := normalize_taxonomy_text(topic))
    )
    return topics, normalize_taxonomy_text(node["description"])


def confidence_from_score(score: float, *, floor: float = 0.52) -> float:
    return round(min(0.99, floor + 0.45 * score / (score + 7.0)), 2)


def classify_ai(node: dict[str, Any]) -> dict[str, Any]:
    topics, description = taxonomy_inputs(node)
    scores: dict[str, float] = {}
    evidence_by_category: dict[str, list[tuple[str, float]]] = {}
    sources: set[str] = set()
    for category in AI_SUBCATEGORY_ORDER:
        score, evidence, category_sources = keyword_evidence(
            AI_SUBCATEGORY_KEYWORDS[category],
            topics,
            description,
        )
        scores[category] = score
        evidence_by_category[category] = evidence
        sources.update(category_sources)

    primary = min(
        AI_SUBCATEGORY_ORDER,
        key=lambda category: (-scores[category], AI_SUBCATEGORY_ORDER.index(category)),
    )
    best_score = scores[primary]
    is_ai = best_score >= 0.9
    if not is_ai:
        return {
            "isAi": False,
            "primary": "Non-AI",
            "tags": [],
            "source": "tech0-ai-lexicon-v1:no-signal",
            "confidence": 0.58,
        }

    all_evidence = [
        (keyword, contribution)
        for evidence in evidence_by_category.values()
        for keyword, contribution in evidence
    ]
    all_evidence.sort(key=lambda item: (-item[1], item[0]))
    tags: list[str] = []
    for keyword, _ in all_evidence:
        if keyword not in tags:
            tags.append(keyword)
        if len(tags) == 6:
            break
    source_suffix = "+".join(sorted(sources)) or "no-signal"
    return {
        "isAi": True,
        "primary": primary,
        "tags": tags,
        "source": f"tech0-ai-lexicon-v1:{source_suffix}",
        "confidence": confidence_from_score(best_score, floor=0.57),
    }


DOMAIN_TIE_ORDER = (
    "Blockchain",
    "Database",
    "IoT",
    "Operating System",
    "Programming Language",
    "Web Frameworks",
    "Big Data",
    "Cloud Native",
    "Application Software",
    "Development Tools",
)


def domain_fallback(language: str) -> str:
    if language in {"Shell", "Nix", "HCL", "Dockerfile", "Makefile"}:
        return "Cloud Native"
    if language in {
        "JavaScript",
        "TypeScript",
        "HTML",
        "CSS",
        "Dart",
        "Kotlin",
        "Swift",
        "C#",
        "Java",
        "PHP",
        "Ruby",
    }:
        return "Application Software"
    if language in {"PLpgSQL", "SQLPL"}:
        return "Database"
    return "Development Tools"


def classify_domain(node: dict[str, Any], ai_area: dict[str, Any]) -> dict[str, Any]:
    # AI is first-class in Tech-0.  Preserve the more specific Agentic AI split
    # instead of allowing generic words such as "framework" to override it.
    if ai_area["isAi"]:
        primary = "Agentic AI" if ai_area["primary"] == "Agents & RAG" else "AI"
        return {
            "primary": primary,
            "tags": list(ai_area["tags"]),
            "source": ai_area["source"].replace("tech0-ai", "tech0-domain"),
            "confidence": ai_area["confidence"],
        }

    topics, description = taxonomy_inputs(node)
    scores: dict[str, float] = {}
    evidence_by_category: dict[str, list[tuple[str, float]]] = {}
    sources_by_category: dict[str, set[str]] = {}
    for category in DOMAIN_TIE_ORDER:
        score, evidence, sources = keyword_evidence(
            DOMAIN_KEYWORDS[category],
            topics,
            description,
        )
        scores[category] = score
        evidence_by_category[category] = evidence
        sources_by_category[category] = sources

    primary = min(
        DOMAIN_TIE_ORDER,
        key=lambda category: (-scores[category], DOMAIN_TIE_ORDER.index(category)),
    )
    best_score = scores[primary]
    if best_score == 0:
        primary = domain_fallback(node["lang"])
        return {
            "primary": primary,
            "tags": [node["lang"]],
            "source": "tech0-domain-lexicon-v1:primary_language-fallback",
            "confidence": 0.24,
        }

    tags = [keyword for keyword, _ in evidence_by_category[primary][:6]]
    source_suffix = "+".join(sorted(sources_by_category[primary]))
    return {
        "primary": primary,
        "tags": tags,
        "source": f"tech0-domain-lexicon-v1:{source_suffix}",
        "confidence": confidence_from_score(best_score),
    }


def curated_label_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return curated_label_values(decoded)
        return [part.strip() for part in re.split(r"[|;,]", stripped) if part.strip()]
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            values.extend(curated_label_values(item))
        return values
    return [str(value)]


def curated_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def normalize_repo_id(value: Any) -> str:
    normalized = str(value).strip()
    if normalized.casefold().startswith("github:"):
        normalized = normalized.split(":", 1)[1]
    return normalized


def load_area_enrichment(path: Path | None) -> dict[str, dict[str, Any]]:
    """Load optional curated labels without coupling generation to ClickHouse."""

    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    keyed_records: list[tuple[str | None, dict[str, Any]]] = []
    if isinstance(payload, list):
        keyed_records = [(None, record) for record in payload if isinstance(record, dict)]
    elif isinstance(payload, dict):
        container = next(
            (payload[key] for key in ("repositories", "items", "rows", "data") if key in payload),
            None,
        )
        if isinstance(container, list):
            keyed_records = [(None, record) for record in container if isinstance(record, dict)]
        elif isinstance(container, dict) and all(
            isinstance(record, dict) for record in container.values()
        ):
            keyed_records = [(str(key), record) for key, record in container.items()]
        elif all(isinstance(record, dict) for record in payload.values()):
            keyed_records = [(str(key), record) for key, record in payload.items()]
        else:
            raise ValueError(
                f"{path} must be a record list, keyed record object, or contain rows/items/data"
            )
    else:
        raise ValueError(f"{path} must contain a JSON array or object")

    enrichment: dict[str, dict[str, Any]] = {}
    for lookup_key, record in keyed_records:
        repo_id = record.get("repo_id", record.get("repoId"))
        name = record.get("name", record.get("repo_name", record.get("repository")))
        if repo_id is None and name is None and lookup_key is not None:
            if normalize_repo_id(lookup_key).isdigit():
                repo_id = lookup_key
            else:
                name = lookup_key
        keys: list[str] = []
        if repo_id is not None and normalize_repo_id(repo_id):
            keys.append(f"repo:{normalize_repo_id(repo_id)}")
        if name is not None and str(name).strip():
            keys.append(f"name:{str(name).strip().casefold()}")
        if not keys:
            continue
        for key in keys:
            existing = enrichment.get(key)
            if existing is not None and existing != record:
                raise ValueError(f"Duplicate area enrichment key: {key}")
            enrichment[key] = record
    return enrichment


def canonical_domain_label(labels: list[str]) -> str | None:
    exact = {normalize_taxonomy_text(category): category for category in DOMAIN_ORDER}
    aliases = {
        "artificial intelligence": "AI",
        "agentic": "Agentic AI",
        "applications": "Application Software",
        "application": "Application Software",
        "data": "Big Data",
        "bigdata": "Big Data",
        "block chain": "Blockchain",
        "crypto": "Blockchain",
        "cloud": "Cloud Native",
        "cloud infrastructure": "Cloud Native",
        "databases": "Database",
        "libraries and frameworks": "Development Tools",
        "software tools": "Development Tools",
        "developer tooling": "Development Tools",
        "devtools": "Development Tools",
        "internet of things": "IoT",
        "os": "Operating System",
        "system software": "Operating System",
        "non software": "Development Tools",
        "languages": "Programming Language",
        "programming languages": "Programming Language",
        "web": "Web Frameworks",
        "web frameworks": "Web Frameworks",
    }
    for label in labels:
        normalized = normalize_taxonomy_text(label)
        if normalized in exact:
            return exact[normalized]
        if normalized in aliases:
            return aliases[normalized]
    return None


def canonical_ai_label(labels: list[str]) -> str | None:
    exact = {normalize_taxonomy_text(category): category for category in AI_SUBCATEGORY_ORDER}
    aliases = {
        "llm": "LLM & Generative AI",
        "large language models": "LLM & Generative AI",
        "generative ai": "LLM & Generative AI",
        "genai": "LLM & Generative AI",
        "agent": "Agents & RAG",
        "agents": "Agents & RAG",
        "agentic ai": "Agents & RAG",
        "rag": "Agents & RAG",
        "machine learning": "ML Frameworks",
        "deep learning": "ML Frameworks",
        "computer vision": "Computer Vision",
        "nlp": "NLP & Speech",
        "speech": "NLP & Speech",
        "mlops": "Data / Evaluation / MLOps",
        "evaluation": "Data / Evaluation / MLOps",
        "data": "Data / Evaluation / MLOps",
        "robotics": "Robotics",
        "ai application": "AI Applications",
        "ai applications": "AI Applications",
    }
    for label in labels:
        normalized = normalize_taxonomy_text(label)
        if normalized in exact:
            return exact[normalized]
        if normalized in aliases:
            return aliases[normalized]
        if "vector database" in normalized or "infrastructure data" in normalized:
            return "Data / Evaluation / MLOps"
        if "platform environment" in normalized or "develop tool" in normalized:
            return "Data / Evaluation / MLOps"
        if "framework architecture" in normalized:
            return "ML Frameworks"
        if "large language model" in normalized or "generative artificial intelligence" in normalized:
            return "LLM & Generative AI"
        if normalized.endswith(" application"):
            return "AI Applications"
    return None


def apply_curated_areas(
    ai_area: dict[str, Any],
    domain_area: dict[str, Any],
    curated: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if curated is None:
        return ai_area, domain_area

    tech0_labels = curated_label_values(curated.get("tech0_labels"))
    domain0_labels = curated_label_values(curated.get("domain0_labels"))
    ai_labels = curated_label_values(curated.get("ai_labels"))
    has_agentic_flag = "is_agentic_ai" in curated
    is_agentic = curated_boolean(curated.get("is_agentic_ai")) if has_agentic_flag else False
    has_ai_data = "ai_labels" in curated or has_agentic_flag

    curated_ai_primary = canonical_ai_label(ai_labels)
    if is_agentic:
        curated_ai_primary = "Agents & RAG"
    if has_ai_data:
        is_ai = is_agentic or bool(ai_labels)
        if is_ai and curated_ai_primary is None and ai_area["isAi"]:
            curated_ai_primary = ai_area["primary"]
        ai_area = {
            "isAi": is_ai,
            "primary": curated_ai_primary or ("AI Applications" if is_ai else "Non-AI"),
            "tags": ai_labels[:6],
            "source": "curated",
            "confidence": 1.0,
        }

    curated_domain = canonical_domain_label(tech0_labels + domain0_labels)
    if is_agentic:
        curated_domain = "Agentic AI"
    elif ai_area["isAi"] and (has_ai_data or curated_domain == "AI"):
        curated_domain = "AI"
    if curated_domain is not None:
        domain_area = {
            "primary": curated_domain,
            "tags": (tech0_labels + domain0_labels + ai_labels)[:6],
            "source": "curated",
            "confidence": 1.0,
        }
    return ai_area, domain_area


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
        "repo_id",
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
                    "repoId": row["repo_id"].strip(),
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


def macro_community_assignments(
    micro_communities: list[int],
    edges: list[tuple[int, int, float, int, float]],
    *,
    target_count: int,
) -> list[int]:
    """Aggregate topology communities into a small deterministic macro atlas.

    Large micro-communities seed the macro atlas, then grow through real
    inter-community affinity with a load penalty that prevents one dominant
    seed from swallowing the full graph. Disconnected components remain in a
    small set of balanced satellite fields.
    """

    micro_members: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(micro_communities):
        micro_members[community_id].append(node_index)
    micro_ids = sorted(micro_members)
    if not micro_ids:
        return []
    target_count = max(1, min(target_count, len(micro_ids)))
    micro_index = {community_id: index for index, community_id in enumerate(micro_ids)}
    sizes = [len(micro_members[community_id]) for community_id in micro_ids]

    affinity: dict[tuple[int, int], float] = defaultdict(float)
    for source, target, weight, _, strength in edges:
        source_micro = micro_index[micro_communities[source]]
        target_micro = micro_index[micro_communities[target]]
        if source_micro == target_micro:
            continue
        pair = (
            (source_micro, target_micro)
            if source_micro < target_micro
            else (target_micro, source_micro)
        )
        affinity[pair] += math.log1p(max(weight, 0.0)) + 0.55 * math.log1p(
            max(strength, 0.0)
        )

    micro_adjacency = [set() for _ in micro_ids]
    for left, right in affinity:
        micro_adjacency[left].add(right)
        micro_adjacency[right].add(left)
    topology_components: list[list[int]] = []
    unseen = set(range(len(micro_ids)))
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(micro_adjacency[current], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        topology_components.append(sorted(component))
    topology_components.sort(
        key=lambda component: (
            -sum(sizes[index] for index in component),
            min(micro_ids[index] for index in component),
        )
    )
    main_micro_indexes = set(topology_components[0])
    satellite_components = topology_components[1:]
    main_target = min(len(main_micro_indexes), target_count)
    satellite_slots = target_count - main_target

    main_node_count = sum(sizes[index] for index in main_micro_indexes)
    ideal_load = main_node_count / main_target
    seeds = sorted(
        main_micro_indexes,
        key=lambda index: (-sizes[index], micro_ids[index]),
    )[:main_target]
    macro_by_index = {seed: macro_id for macro_id, seed in enumerate(seeds)}
    loads = [sizes[seed] for seed in seeds]
    unassigned = set(main_micro_indexes) - set(seeds)

    # Grow every seed through real affinity edges.  Dividing by current macro
    # load prevents the largest seed from absorbing the whole main component.
    while unassigned:
        best_assignment: tuple[float, int, int] | None = None
        for index in sorted(unassigned):
            scores: dict[int, float] = defaultdict(float)
            for neighbor in micro_adjacency[index]:
                macro_id = macro_by_index.get(neighbor)
                if macro_id is None:
                    continue
                pair = (index, neighbor) if index < neighbor else (neighbor, index)
                scores[macro_id] += affinity[pair]
            for macro_id, signal in scores.items():
                load_penalty = (1.0 + loads[macro_id] / ideal_load) ** 1.45
                score = signal / load_penalty
                candidate = (score, -micro_ids[index], -macro_id)
                if best_assignment is None or candidate > best_assignment:
                    best_assignment = candidate
        if best_assignment is None:
            index = min(unassigned, key=lambda item: (-sizes[item], micro_ids[item]))
            macro_id = min(range(main_target), key=lambda item: (loads[item], item))
        else:
            _, negative_micro_id, negative_macro_id = best_assignment
            index = micro_index[-negative_micro_id]
            macro_id = -negative_macro_id
        macro_by_index[index] = macro_id
        loads[macro_id] += sizes[index]
        unassigned.remove(index)

    macro_by_micro = {
        micro_ids[index]: macro_by_index[index] for index in main_micro_indexes
    }

    # Disconnected graph components stay intact. A stable component key spreads
    # their display membership across the available macro colors without the
    # artificial equal-capacity buckets that topology cannot justify.
    if satellite_components:
        if satellite_slots <= 0:
            slots = list(range(main_target))
        else:
            slots = list(range(main_target, main_target + satellite_slots))
        for component in satellite_components:
            component_key = min(micro_ids[index] for index in component)
            slot_position = min(
                len(slots) - 1,
                int(
                    stable_unit(f"satellite-macro:{component_key}")
                    * len(slots)
                ),
            )
            macro_id = slots[slot_position]
            for index in component:
                macro_by_micro[micro_ids[index]] = macro_id
    return [macro_by_micro[community_id] for community_id in micro_communities]


def clustered_island_layout(
    nodes: list[dict[str, Any]],
    micro_communities: list[int],
    degree: list[int],
    edges: list[tuple[int, int, float, int, float]],
    backbone_indexes: set[int],
    components: list[list[int]],
    *,
    iterations: int,
) -> tuple[
    list[tuple[float, float]],
    dict[str, float],
    dict[str, Any],
    list[int],
]:
    """Create one continuous galaxy composed of irregular topology islands.

    Expensive force simulation is restricted to at most eighteen macro nodes.
    Real repositories are then placed into deterministic micro-community lobes,
    with strong cross-community endpoints pulled slightly toward their bridge.
    Runtime is O(nodes + edges + macro_iterations).
    """

    node_count = len(nodes)
    requested_macros = 18 if node_count >= 8_000 else 14 if node_count >= 1_500 else 10
    macro_communities = macro_community_assignments(
        micro_communities,
        edges,
        target_count=requested_macros,
    )
    macro_members: dict[int, list[int]] = defaultdict(list)
    micro_members: dict[int, list[int]] = defaultdict(list)
    for node_index, macro_id in enumerate(macro_communities):
        macro_members[macro_id].append(node_index)
        micro_members[micro_communities[node_index]].append(node_index)
    main_members = components[0]
    main_set = set(main_members)
    main_node_count = len(main_members)
    macro_count = len(macro_members)
    macro_sizes = [
        sum(node_index in main_set for node_index in macro_members[macro_id])
        for macro_id in range(macro_count)
    ]
    largest_macro = max(macro_sizes)
    macro_radii = [
        0.07 + 0.19 * (size / largest_macro) ** 0.52 for size in macro_sizes
    ]

    macro_affinity: dict[tuple[int, int], float] = defaultdict(float)
    for source, target, weight, _, strength in edges:
        left = macro_communities[source]
        right = macro_communities[target]
        if left == right:
            continue
        pair = (left, right) if left < right else (right, left)
        macro_affinity[pair] += math.log1p(max(weight, 0.0)) + 0.55 * math.log1p(
            max(strength, 0.0)
        )

    macro_x = [0.0] * macro_count
    macro_y = [0.0] * macro_count
    target_radius = [0.0] * macro_count
    phase = stable_unit(nodes[0]["id"]) * math.tau
    for macro_id in range(1, macro_count):
        fraction = macro_id / max(1, macro_count - 1)
        radius = 0.18 + 0.49 * math.sqrt(fraction)
        angle = phase + macro_id * GOLDEN_ANGLE + 0.18 * (
            stable_unit(f"macro-angle:{macro_id}") - 0.5
        )
        macro_x[macro_id] = radius * math.cos(angle)
        macro_y[macro_id] = radius * math.sin(angle)
        target_radius[macro_id] = radius

    maximum_affinity = max(macro_affinity.values(), default=1.0)
    for iteration in range(iterations):
        progress = iteration / max(1, iterations - 1)
        force_x = [0.0] * macro_count
        force_y = [0.0] * macro_count
        for left in range(macro_count):
            for right in range(left + 1, macro_count):
                delta_x = macro_x[right] - macro_x[left]
                delta_y = macro_y[right] - macro_y[left]
                distance = math.hypot(delta_x, delta_y) + 1e-9
                minimum = (
                    0.68 if left < 4 and right < 4 else 0.9
                ) * (macro_radii[left] + macro_radii[right])
                if distance < minimum:
                    magnitude = 0.045 * (minimum - distance) / distance
                else:
                    magnitude = 0.00034 / (distance * distance)
                push_x = delta_x * magnitude
                push_y = delta_y * magnitude
                force_x[left] -= push_x
                force_y[left] -= push_y
                force_x[right] += push_x
                force_y[right] += push_y

        for (left, right), affinity in macro_affinity.items():
            delta_x = macro_x[right] - macro_x[left]
            delta_y = macro_y[right] - macro_y[left]
            distance = math.hypot(delta_x, delta_y) + 1e-9
            affinity_share = math.sqrt(affinity / maximum_affinity)
            desired = 0.24 + 0.3 * (1.0 - affinity_share)
            magnitude = 0.021 * (0.3 + 0.7 * affinity_share) * (
                distance - desired
            ) / distance
            pull_x = delta_x * magnitude
            pull_y = delta_y * magnitude
            force_x[left] += pull_x
            force_y[left] += pull_y
            force_x[right] -= pull_x
            force_y[right] -= pull_y

        for macro_id in range(macro_count):
            radius = math.hypot(macro_x[macro_id], macro_y[macro_id]) + 1e-9
            if macro_id == 0:
                force_x[macro_id] -= macro_x[macro_id] * 0.09
                force_y[macro_id] -= macro_y[macro_id] * 0.09
            else:
                radial_force = 0.026 * (target_radius[macro_id] - radius)
                force_x[macro_id] += macro_x[macro_id] / radius * radial_force
                force_y[macro_id] += macro_y[macro_id] / radius * radial_force

        temperature = 0.035 * (1.0 - progress) ** 1.25 + 0.0015
        for macro_id in range(macro_count):
            move_x = force_x[macro_id]
            move_y = force_y[macro_id]
            movement = math.hypot(move_x, move_y)
            if movement > temperature:
                move_x *= temperature / movement
                move_y *= temperature / movement
            macro_x[macro_id] += move_x
            macro_y[macro_id] += move_y

        center_x = sum(
            macro_x[index] * macro_sizes[index] for index in range(macro_count)
        ) / main_node_count
        center_y = sum(
            macro_y[index] * macro_sizes[index] for index in range(macro_count)
        ) / main_node_count
        for macro_id in range(macro_count):
            macro_x[macro_id] -= center_x
            macro_y[macro_id] -= center_y

    macro_micro_ids: dict[int, list[int]] = defaultdict(list)
    for micro_id, members in micro_members.items():
        if any(node_index in main_set for node_index in members):
            macro_micro_ids[macro_communities[members[0]]].append(micro_id)
    for micro_ids in macro_micro_ids.values():
        micro_ids.sort(key=lambda micro_id: (-len(micro_members[micro_id]), micro_id))

    coordinates: list[tuple[float, float]] = [(0.0, 0.0)] * node_count
    for macro_id in range(macro_count):
        micro_ids = macro_micro_ids[macro_id]
        macro_angle = math.atan2(macro_y[macro_id], macro_x[macro_id])
        if math.hypot(macro_x[macro_id], macro_y[macro_id]) < 1e-6:
            macro_angle = phase
        orientation = macro_angle + math.pi / 2 + 0.35 * (
            stable_unit(f"macro-orientation:{macro_id}") - 0.5
        )
        for position, micro_id in enumerate(micro_ids):
            members = sorted(
                (
                    node_index
                    for node_index in micro_members[micro_id]
                    if node_index in main_set
                ),
                key=lambda index: (
                    -nodes[index]["r"],
                    -degree[index],
                    nodes[index]["id"],
                ),
            )
            if position == 0:
                center_radius = macro_radii[macro_id] * 0.055
            else:
                center_radius = macro_radii[macro_id] * (
                    0.19
                    + 0.48
                    * math.sqrt(position / max(1, len(micro_ids) - 1))
                )
            center_angle = (
                orientation
                + position * GOLDEN_ANGLE
                + 0.24 * (stable_unit(f"micro-angle:{micro_id}") - 0.5)
            )
            micro_center_x = macro_x[macro_id] + center_radius * math.cos(center_angle)
            micro_center_y = macro_y[macro_id] + center_radius * math.sin(center_angle)
            extent_share = 0.1 + 0.58 * math.sqrt(
                len(members) / max(1, macro_sizes[macro_id])
            )
            micro_extent = macro_radii[macro_id] * max(
                0.12, min(0.54, extent_share)
            )
            local_orientation = orientation + 0.55 * (
                stable_unit(f"micro-orientation:{micro_id}") - 0.5
            )
            local_aspect = 1.12 + 0.6 * stable_unit(f"micro-aspect:{micro_id}")
            cosine = math.cos(local_orientation)
            sine = math.sin(local_orientation)
            for local_position, node_index in enumerate(members):
                if len(members) == 1:
                    local_radius = 0.0
                else:
                    fraction = (local_position + 0.28) / len(members)
                    local_radius = micro_extent * (0.045 + 0.94 * math.sqrt(fraction))
                local_angle = (
                    local_position * GOLDEN_ANGLE
                    + stable_unit(f"node-angle:{nodes[node_index]['id']}") * 0.58
                )
                irregularity = (
                    1
                    + 0.15 * math.sin(local_angle * 3 + micro_id)
                    + 0.07 * math.sin(local_angle * 5 + macro_id)
                )
                local_x = (
                    local_radius * irregularity * math.cos(local_angle) * local_aspect
                )
                local_y = (
                    local_radius * irregularity * math.sin(local_angle) / local_aspect
                )
                coordinates[node_index] = (
                    micro_center_x + local_x * cosine - local_y * sine,
                    micro_center_y + local_x * sine + local_y * cosine,
                )

    # Keep every disconnected graph component intact, but distribute the 944
    # small components as natural satellite islands across the full outer disc
    # instead of collapsing them into a few artificial mega-communities.
    satellite_phase = phase + 0.37
    for component in components[1:]:
        component_key = min(nodes[index]["id"] for index in component)
        center_angle = (
            satellite_phase
            + stable_unit(f"satellite-angle:{component_key}") * math.tau
        )
        center_radius = 0.56 + 0.34 * stable_unit(
            f"satellite-radius:{component_key}"
        )
        center_x = center_radius * math.cos(center_angle)
        center_y = center_radius * math.sin(center_angle)
        local_extent = min(0.032, 0.0035 + 0.0032 * math.sqrt(len(component)))
        ordered_component = sorted(
            component,
            key=lambda index: (-nodes[index]["r"], -degree[index], nodes[index]["id"]),
        )
        local_phase = stable_unit(f"satellite-local:{component_key}") * math.tau
        for position, node_index in enumerate(ordered_component):
            local_radius = local_extent * math.sqrt(
                (position + 0.28) / len(ordered_component)
            )
            local_angle = local_phase + position * GOLDEN_ANGLE
            coordinates[node_index] = (
                center_x + local_radius * math.cos(local_angle),
                center_y + local_radius * math.sin(local_angle),
            )

    # Strong real cross-cluster edges pull their endpoints into subtle bridge
    # ports, forming filaments without adding synthetic nodes or relations.
    bridge_x = [0.0] * node_count
    bridge_y = [0.0] * node_count
    bridge_mass = [0.0] * node_count
    for edge_index in sorted(backbone_indexes):
        source, target, weight, _, strength = edges[edge_index]
        source_macro = macro_communities[source]
        target_macro = macro_communities[target]
        if source_macro == target_macro:
            continue
        delta_x = macro_x[target_macro] - macro_x[source_macro]
        delta_y = macro_y[target_macro] - macro_y[source_macro]
        distance = math.hypot(delta_x, delta_y) + 1e-9
        signal = math.log1p(max(weight, 0.0)) + 0.55 * math.log1p(max(strength, 0.0))
        direction_x = delta_x / distance
        direction_y = delta_y / distance
        bridge_x[source] += direction_x * signal
        bridge_y[source] += direction_y * signal
        bridge_mass[source] += signal
        bridge_x[target] -= direction_x * signal
        bridge_y[target] -= direction_y * signal
        bridge_mass[target] += signal
    for node_index, mass in enumerate(bridge_mass):
        if mass <= 0:
            continue
        length = math.hypot(bridge_x[node_index], bridge_y[node_index]) + 1e-9
        macro_radius = macro_radii[macro_communities[node_index]]
        shift = macro_radius * (0.045 + 0.095 * math.tanh(mass / 6.0))
        x, y = coordinates[node_index]
        coordinates[node_index] = (
            x + bridge_x[node_index] / length * shift,
            y + bridge_y[node_index] / length * shift,
        )

    center_x = sum(coordinates[index][0] for index in main_members) / len(main_members)
    center_y = sum(coordinates[index][1] for index in main_members) / len(main_members)
    centered = [
        (coordinate[0] - center_x, coordinate[1] - center_y)
        for coordinate in coordinates
    ]
    maximum_radius = max(math.hypot(x, y) for x, y in centered)
    final_scale = 0.965 / max(maximum_radius, 1e-9)
    coordinates = [
        (round(x * final_scale, 5), round(y * final_scale, 5))
        for x, y in centered
    ]
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
        macro_communities,
        components,
        edges,
        backbone_indexes,
    )
    metrics.update(
        {
            "mode": "clustered-island-disc",
            "iterations": iterations,
            "macroCommunityCount": macro_count,
            "microCommunityCount": len(set(micro_communities)),
            "realNodeCount": node_count,
            "syntheticNodeCount": 0,
        }
    )
    return coordinates, bounds, metrics, macro_communities


def community_geometry(
    coordinates: list[tuple[float, float]], members: list[int]
) -> dict[str, float]:
    """Summarize a community's rendered covariance envelope."""

    center_x = sum(coordinates[index][0] for index in members) / len(members)
    center_y = sum(coordinates[index][1] for index in members) / len(members)
    variance_x = 0.0
    variance_y = 0.0
    covariance = 0.0
    distances = []
    for index in members:
        delta_x = coordinates[index][0] - center_x
        delta_y = coordinates[index][1] - center_y
        variance_x += delta_x * delta_x
        variance_y += delta_y * delta_y
        covariance += delta_x * delta_y
        distances.append(math.hypot(delta_x, delta_y))
    variance_x /= len(members)
    variance_y /= len(members)
    covariance /= len(members)
    trace = variance_x + variance_y
    discriminant = math.sqrt(
        max(0.0, (variance_x - variance_y) ** 2 + 4 * covariance * covariance)
    )
    major = max((trace + discriminant) / 2, 1e-8)
    minor = max((trace - discriminant) / 2, major * 0.12)
    return {
        "x": round(center_x, 5),
        "y": round(center_y, 5),
        "radius": round(max(0.025, quantile(distances, 0.92) * 1.08), 5),
        "angle": round(0.5 * math.atan2(2 * covariance, variance_x - variance_y), 5),
        "aspect": round(max(1.05, min(2.35, math.sqrt(major / minor))), 4),
    }


def compact_number(value: float, digits: int) -> int | float:
    rounded = round(value, digits)
    if rounded == 0:
        return 0
    if rounded.is_integer():
        return int(rounded)
    return rounded


def facet_summary(
    nodes: list[dict[str, Any]],
    output_nodes: list[dict[str, Any]],
    facet: str,
    *,
    colors: dict[str, str] | None = None,
    fixed_order: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    members: dict[str, list[int]] = defaultdict(list)
    for node_index, node in enumerate(output_nodes):
        members[node["areas"][facet]["primary"]].append(node_index)

    if fixed_order is None:
        ordered_names = sorted(members, key=lambda name: (-len(members[name]), name))
    else:
        order_index = {name: index for index, name in enumerate(fixed_order)}
        ordered_names = sorted(
            members,
            key=lambda name: (order_index.get(name, len(order_index)), name),
        )

    summary: list[dict[str, Any]] = []
    for name in ordered_names:
        indexes = members[name]
        representative = min(
            indexes,
            key=lambda index: (-nodes[index]["r"], -output_nodes[index]["degree"], nodes[index]["id"]),
        )
        item: dict[str, Any] = {
            "name": name,
            "count": len(indexes),
            "representative": nodes[representative]["name"],
        }
        if colors is not None:
            item["color"] = colors.get(name, stable_color(f"facet:{facet}:{name}"))
        summary.append(item)
    return summary


def build_payload(
    nodes_path: Path,
    edges_path: Path,
    manifest_path: Path,
    area_enrichment_path: Path | None,
    *,
    max_iterations: int,
    resolution: float,
    layout_iterations: int,
) -> dict[str, Any]:
    nodes, index_by_id = read_nodes(nodes_path)
    edges, adjacency, degree = read_edges(edges_path, index_by_id, len(nodes))
    area_enrichment = load_area_enrichment(area_enrichment_path)

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
    coordinates, bounds, layout_summary, macro_community_ids = clustered_island_layout(
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
    main_component_set = set(components[0])

    community_members: dict[int, list[int]] = defaultdict(list)
    for node_index, community_id in enumerate(macro_community_ids):
        community_members[community_id].append(node_index)

    community_summary: list[dict[str, Any]] = []
    for community_id in range(len(community_members)):
        members = community_members[community_id]
        core_members = [index for index in members if index in main_component_set] or members
        top_node = min(
            core_members,
            key=lambda index: (-nodes[index]["r"], -degree[index], nodes[index]["id"]),
        )
        top_language = min(
            Counter(nodes[index]["lang"] for index in core_members).items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        geometry = community_geometry(coordinates, core_members)
        community_summary.append(
            {
                "id": community_id,
                "count": len(members),
                "label": nodes[top_node]["name"],
                "lang": top_language,
                "color": community_color(community_id),
                **geometry,
                "microCommunityCount": len(
                    {community_ids[index] for index in members}
                ),
            }
        )

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
        ai_area = classify_ai(node)
        domain_area = classify_domain(node, ai_area)
        curated = area_enrichment.get(f"repo:{normalize_repo_id(node['repoId'])}")
        if curated is None:
            curated = area_enrichment.get(f"name:{node['name'].strip().casefold()}")
        ai_area, domain_area = apply_curated_areas(ai_area, domain_area, curated)
        output_nodes.append(
            {
                "id": node["id"],
                "name": node["name"],
                "label": node["label"],
                "x": x,
                "y": y,
                "r": compact_number(node["r"], 3),
                "c": macro_community_ids[node_index],
                "mc": community_ids[node_index],
                "lang": node["lang"],
                "areas": {
                    "language": {
                        "primary": node["lang"],
                        "source": "nodes.csv:primary_language",
                        "confidence": 1,
                    },
                    "domain": domain_area,
                    "ai": ai_area,
                },
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

    language_summary = facet_summary(
        nodes,
        output_nodes,
        "language",
        colors={language: language_color(language) for language in {node["lang"] for node in nodes}},
    )
    domain_summary = facet_summary(
        nodes,
        output_nodes,
        "domain",
        colors=DOMAIN_COLORS,
    )
    ai_summary = facet_summary(
        nodes,
        output_nodes,
        "ai",
        colors=AI_COLORS,
        fixed_order=AI_SUBCATEGORY_ORDER + ("Non-AI",),
    )

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
            "source": "github",
            "languages": language_summary,
            "facets": {
                "language": language_summary,
                "ai": ai_summary,
                "domain": domain_summary,
            },
            "facetOrder": ["language", "ai", "domain"],
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
        for attempt in range(8):
            try:
                os.replace(temporary_path, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                # Windows indexers and preview servers can briefly retain a
                # read handle after a large JSON build. Preserve atomic output
                # semantics while allowing that transient handle to drain.
                time.sleep(0.12 * (attempt + 1))
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
    parser.add_argument(
        "--area-enrichment",
        type=Path,
        help=(
            "Optional curated area-label JSON. Defaults to area-enrichment.json "
            "inside --artifact-dir when that file exists."
        ),
    )
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
    if args.area_enrichment is not None:
        area_enrichment_path: Path | None = args.area_enrichment.resolve()
        if not area_enrichment_path.is_file():
            raise ValueError(f"Area enrichment file does not exist: {area_enrichment_path}")
    else:
        enrichment_candidate = artifact_dir / "area-enrichment.json"
        area_enrichment_path = enrichment_candidate if enrichment_candidate.is_file() else None
    payload = build_payload(
        artifact_dir / "nodes.csv",
        artifact_dir / "edges.csv",
        artifact_dir / "manifest.json",
        area_enrichment_path,
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
                "domains": len(payload["meta"]["facets"]["domain"]),
                "aiSubcategories": len(payload["meta"]["facets"]["ai"]) - 1,
                "aiNodes": sum(
                    item["count"]
                    for item in payload["meta"]["facets"]["ai"]
                    if item["name"] != "Non-AI"
                ),
                "areaEnrichment": str(area_enrichment_path) if area_enrichment_path else None,
                "iterations": payload["meta"]["iterations"],
                "layout": payload["meta"]["layout"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
