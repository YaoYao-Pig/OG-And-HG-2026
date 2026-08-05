#!/usr/bin/env python3
"""Export a bounded repository collaboration graph from ClickHouse for Graphia.

The script intentionally uses only Python's standard library. Credentials are read
from environment variables and every ClickHouse request is forced into readonly
mode. Raw event rows are never downloaded.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from xml.sax.saxutils import escape, quoteattr
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PRESETS: dict[str, dict[str, int]] = {
    "preview": {
        "top_repos": 5_000,
        "min_shared": 2,
        "max_actor_degree": 30,
        "max_edges_per_node": 30,
        "label_count": 150,
        "max_result_rows": 200_000,
    },
    "final": {
        "top_repos": 20_000,
        "min_shared": 2,
        "max_actor_degree": 30,
        "max_edges_per_node": 20,
        "label_count": 300,
        "max_result_rows": 500_000,
    },
}

SOURCE_DUPLICATE_RATIO_LIMIT = 0.01

EXPECTED_EDGE_COLUMNS = (
    "source",
    "target",
    "weight",
    "shared_contributors",
    "collaboration_strength",
)

CANDIDATE_NODE_COLUMNS = (
    "id",
    "name",
    "platform",
    "repo_id",
    "openrank_sum",
    "openrank_avg",
    "openrank_max",
    "months_present",
    "contributors",
    "normalized_contribution",
    "primary_language",
    "topics",
    "description",
    "metadata_status",
    "url",
)

EXPECTED_NODE_COLUMNS = (
    "id",
    "name",
    "display_label",
    *CANDIDATE_NODE_COLUMNS[2:],
)

NODE_GRAPHML_TYPES: dict[str, str] = {
    "name": "string",
    "display_label": "string",
    "platform": "string",
    "repo_id": "long",
    "openrank_sum": "double",
    "openrank_avg": "double",
    "openrank_max": "double",
    "months_present": "int",
    "contributors": "long",
    "normalized_contribution": "double",
    "primary_language": "string",
    "topics": "string",
    "description": "string",
    "metadata_status": "string",
    "url": "string",
}

EDGE_GRAPHML_TYPES: dict[str, str] = {
    "weight": "double",
    "shared_contributors": "long",
    "collaboration_strength": "double",
}


class ExportError(RuntimeError):
    """A user-facing export failure that is safe to print."""


@dataclass(frozen=True)
class ExportParameters:
    preset: str
    platform: str
    start_yyyymm: int
    end_yyyymm: int
    months: int
    top_repos: int
    min_shared: int
    max_actor_degree: int
    max_edges_per_node: int
    label_count: int
    max_result_rows: int
    max_threads: int
    max_execution_time: int


@dataclass(frozen=True)
class ConnectionConfig:
    endpoint: str
    username: str
    password: str
    database: str
    allow_insecure_http: bool

    @classmethod
    def from_environment(cls, allow_insecure_http: bool) -> "ConnectionConfig":
        names = (
            "CLICKHOUSE_HOST",
            "CLICKHOUSE_USERNAME",
            "CLICKHOUSE_PASSWORD",
            "CLICKHOUSE_DATABASE",
        )
        values = {name: os.environ.get(name, "") for name in names}
        missing = [
            name
            for name, value in values.items()
            if not value or (name != "CLICKHOUSE_PASSWORD" and not value.strip())
        ]
        if missing:
            raise ExportError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )

        endpoint = values["CLICKHOUSE_HOST"].strip().rstrip("/")
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ExportError("CLICKHOUSE_HOST must be a valid http:// or https:// URL")
        if parsed.username is not None or parsed.password is not None:
            raise ExportError("CLICKHOUSE_HOST must not contain embedded credentials")
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ExportError(
                "CLICKHOUSE_HOST must contain only scheme, host, and optional port"
            )
        try:
            if parsed.hostname is None or parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
        except ValueError:
            raise ExportError("CLICKHOUSE_HOST contains an invalid host or port") from None
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ExportError(
                "Refusing plaintext HTTP because Basic Auth would not be encrypted. "
                "Use an HTTPS ClickHouse endpoint, or explicitly pass "
                "--allow-insecure-http for this run."
            )

        username = values["CLICKHOUSE_USERNAME"].strip()
        if ":" in username:
            raise ExportError("CLICKHOUSE_USERNAME must not contain ':' for Basic Auth")

        return cls(
            endpoint=endpoint,
            username=username,
            password=values["CLICKHOUSE_PASSWORD"],
            database=values["CLICKHOUSE_DATABASE"].strip(),
            allow_insecure_http=allow_insecure_http,
        )


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a ClickHouse Authorization header through a redirect."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class ClickHouseHttpClient:
    def __init__(
        self,
        connection: ConnectionConfig,
        max_threads: int,
        max_execution_time: int,
        run_id: str,
    ) -> None:
        self.connection = connection
        self.max_threads = max_threads
        self.max_execution_time = max_execution_time
        self.run_id = run_id
        auth_bytes = f"{connection.username}:{connection.password}".encode("utf-8")
        self.authorization = "Basic " + base64.b64encode(auth_bytes).decode("ascii")
        self.opener = urllib.request.build_opener(RejectRedirectHandler())

    def _request(
        self,
        query: str,
        *,
        query_name: str,
        max_result_rows: int | None = None,
    ) -> urllib.response.addinfourl:
        settings: dict[str, str | int] = {
            "database": self.connection.database,
            "readonly": 1,
            "max_threads": self.max_threads,
            "max_execution_time": self.max_execution_time,
            "query_id": f"open-galaxy-{self.run_id}-{query_name}",
            "wait_end_of_query": 1,
            "buffer_size": 67_108_864,
            "max_rows_to_group_by": 10_000_000,
            "group_by_overflow_mode": "throw",
            "max_bytes_before_external_group_by": 1_000_000_000,
            "max_bytes_before_external_sort": 1_000_000_000,
        }
        if max_result_rows is not None:
            settings["max_result_rows"] = max_result_rows
            settings["result_overflow_mode"] = "throw"

        url = self.connection.endpoint + "/?" + urllib.parse.urlencode(settings)
        request = urllib.request.Request(
            url,
            data=query.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self.authorization,
                "Content-Type": "text/plain; charset=utf-8",
                "User-Agent": "open-galaxy-export/1.2",
            },
        )
        try:
            return self.opener.open(
                request,
                timeout=self.max_execution_time + 30,
            )
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(8_192).decode("utf-8", errors="replace").strip()
            finally:
                exc.close()
            raise ExportError(
                f"ClickHouse query {query_name!r} failed with HTTP {exc.code}: {detail}"
            ) from None
        except urllib.error.URLError as exc:
            raise ExportError(
                f"ClickHouse query {query_name!r} could not connect: {exc.reason}"
            ) from None

    @staticmethod
    def _check_exception_headers(headers: Any, query_name: str) -> None:
        for name, value in headers.items():
            lowered = name.lower()
            if (
                lowered == "x-clickhouse-exception-code"
                and str(value).strip() not in {"", "0"}
            ):
                raise ExportError(
                    f"ClickHouse query {query_name!r} reported an exception in HTTP headers"
                )

    @staticmethod
    def _check_exception_bytes(data: bytes, query_name: str) -> None:
        patterns = (
            rb"(?:^|\n)Code:\s*\d+\.\s*DB::Exception:",
            rb"(?:^|\n)__exception__",
        )
        if any(re.search(pattern, data) for pattern in patterns):
            raise ExportError(
                f"ClickHouse query {query_name!r} returned a streamed exception body"
            )

    def text(self, query: str, *, query_name: str) -> str:
        with self._request(query, query_name=query_name) as response:
            self._check_exception_headers(response.headers, query_name)
            data = response.read()
        self._check_exception_bytes(data, query_name)
        return data.decode("utf-8").strip()

    def export_csv(
        self,
        query: str,
        destination: Path,
        *,
        query_name: str,
        max_result_rows: int,
    ) -> tuple[int, float]:
        temporary: Path | None = None
        started = time.perf_counter()
        try:
            with self._request(
                query,
                query_name=query_name,
                max_result_rows=max_result_rows,
            ) as response:
                self._check_exception_headers(response.headers, query_name)
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination.parent,
                    prefix=f".{destination.name}.{self.run_id}.",
                    suffix=".part",
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if temporary is None:
                raise ExportError(f"Query {query_name!r} produced no temporary output")
            with temporary.open("rb") as exported:
                exported.seek(max(0, temporary.stat().st_size - 65_536))
                tail = exported.read()
            self._check_exception_bytes(tail, query_name)
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        elapsed = time.perf_counter() - started
        return destination.stat().st_size, elapsed


def clickhouse_string(value: str) -> str:
    """Quote a trusted scalar as a ClickHouse SQL string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def parse_yyyymm(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must use YYYYMM format") from exc
    year, month = divmod(parsed, 100)
    if year < 2000 or not 1 <= month <= 12:
        raise argparse.ArgumentTypeError("month must use YYYYMM format")
    return parsed


def parse_platform(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise argparse.ArgumentTypeError(
            "platform may contain only letters, numbers, underscore, dot, or hyphen"
        )
    return value


def shift_month(yyyymm: int, delta: int) -> int:
    year, month = divmod(yyyymm, 100)
    absolute = year * 12 + (month - 1) + delta
    shifted_year, shifted_month_zero = divmod(absolute, 12)
    return shifted_year * 100 + shifted_month_zero + 1


def build_top_repo_ctes(
    parameters: ExportParameters,
    *,
    include_metrics: bool = False,
) -> str:
    platform = clickhouse_string(parameters.platform)
    monthly_metric_columns = """
            argMax(repo_name, tuple(toFloat64(openrank), repo_name)) AS repo_name,
            argMax(org_login, tuple(toFloat64(openrank), org_login)) AS org_login,""" if include_metrics else ""
    score_metric_columns = """
            argMax(repo_name, tuple(yyyymm, month_openrank, repo_name)) AS repo_name,
            argMax(org_login, tuple(yyyymm, month_openrank, org_login)) AS org_login,
            avg(month_openrank) AS openrank_avg,
            max(month_openrank) AS openrank_max,
            uniqExact(yyyymm) AS months_present,""" if include_metrics else ""
    return f"""
    global_repo_months AS
    (
        SELECT
            repo_id,
            toYYYYMM(created_at) AS yyyymm,
{monthly_metric_columns}
            max(toFloat64(openrank)) AS month_openrank
        FROM global_openrank
        WHERE platform = {platform}
          AND type = 'Repo'
          AND toYYYYMM(created_at) BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
          AND repo_id != 0
        GROUP BY repo_id, yyyymm
    ),
    repo_scores AS
    (
        SELECT
            repo_id,
{score_metric_columns}
            sum(month_openrank) AS openrank_sum
        FROM global_repo_months
        GROUP BY repo_id
    ),
    latest_repo_info AS
    (
        SELECT
            id,
            argMax(isFork, tuple(updated_at, isFork)) AS is_fork
        FROM repo_info
        WHERE platform = {platform}
        GROUP BY id
    ),
    top_repos AS
    (
        SELECT scores.*
        FROM repo_scores AS scores
        LEFT JOIN latest_repo_info AS info ON info.id = scores.repo_id
        WHERE info.id = 0 OR info.is_fork = 0
        ORDER BY scores.openrank_sum DESC, scores.repo_id
        LIMIT {parameters.top_repos}
    )""".strip()


def build_edges_query(parameters: ExportParameters) -> str:
    platform = clickhouse_string(parameters.platform)
    top_repo_ctes = build_top_repo_ctes(parameters)
    return f"""WITH
    {top_repo_ctes},
    actor_annual_degrees AS
    (
        SELECT
            actor_id,
            uniqExact(repo_id) AS annual_repo_degree
        FROM normalized_community_openrank
        WHERE platform = {platform}
          AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
          AND actor_id != 0
        GROUP BY actor_id
        HAVING annual_repo_degree BETWEEN 2 AND {parameters.max_actor_degree}
    ),
    actor_repo_months AS
    (
        SELECT
            actor_id,
            repo_id,
            yyyymm,
            max(toFloat64(openrank)) AS month_contribution
        FROM normalized_community_openrank
        WHERE platform = {platform}
          AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
          AND actor_id IN (SELECT actor_id FROM actor_annual_degrees)
          AND repo_id IN (SELECT repo_id FROM top_repos)
        GROUP BY actor_id, repo_id, yyyymm
    ),
    actor_repo AS
    (
        SELECT
            actor_id,
            repo_id,
            sum(month_contribution) AS contribution
        FROM actor_repo_months
        GROUP BY actor_id, repo_id
        HAVING contribution > 0
    ),
    selected_actor_bundles AS
    (
        SELECT
            actor_id,
            arraySort(groupArray({parameters.max_actor_degree})((repo_id, contribution))) AS repos,
            length(repos) AS selected_repo_degree
        FROM actor_repo
        GROUP BY actor_id
        HAVING selected_repo_degree >= 2
    ),
    actor_bundles AS
    (
        SELECT
            bundles.actor_id,
            bundles.repos,
            degrees.annual_repo_degree
        FROM selected_actor_bundles AS bundles
        INNER JOIN actor_annual_degrees AS degrees ON degrees.actor_id = bundles.actor_id
    ),
    pair_rows AS
    (
        SELECT
            tupleElement(repos[i], 1) AS source_repo_id,
            tupleElement(repos[j], 1) AS target_repo_id,
            tupleElement(repos[i], 2) AS source_contribution,
            tupleElement(repos[j], 2) AS target_contribution,
            2.0 / (toFloat64(annual_repo_degree) * (annual_repo_degree - 1)) AS actor_weight
        FROM actor_bundles
        ARRAY JOIN arrayEnumerate(repos) AS i
        ARRAY JOIN arrayEnumerate(repos) AS j
        WHERE i < j
    ),
    aggregated_edges AS
    (
        SELECT
            source_repo_id,
            target_repo_id,
            round(sum(actor_weight), 8) AS weight,
            count() AS shared_contributors,
            round(
                sum(actor_weight * sqrt(source_contribution * target_contribution)),
                8
            ) AS collaboration_strength
        FROM pair_rows
        GROUP BY source_repo_id, target_repo_id
        HAVING shared_contributors >= {parameters.min_shared}
    ),
    incident_edges AS
    (
        SELECT
            tupleElement(endpoint, 1) AS node_repo_id,
            tupleElement(endpoint, 2) AS neighbor_repo_id,
            weight,
            shared_contributors,
            collaboration_strength
        FROM aggregated_edges
        ARRAY JOIN [
            (source_repo_id, target_repo_id),
            (target_repo_id, source_repo_id)
        ] AS endpoint
    ),
    ranked_incident_edges AS
    (
        SELECT
            *,
            row_number() OVER
            (
                PARTITION BY node_repo_id
                ORDER BY weight DESC, shared_contributors DESC, neighbor_repo_id
            ) AS edge_rank
        FROM incident_edges
    ),
    pruned_edges AS
    (
        SELECT
            least(node_repo_id, neighbor_repo_id) AS source_repo_id,
            greatest(node_repo_id, neighbor_repo_id) AS target_repo_id,
            max(weight) AS weight,
            max(shared_contributors) AS shared_contributors,
            max(collaboration_strength) AS collaboration_strength
        FROM ranked_incident_edges
        WHERE edge_rank <= {parameters.max_edges_per_node}
        GROUP BY source_repo_id, target_repo_id
    )
SELECT
    concat({platform}, ':', toString(source_repo_id)) AS source,
    concat({platform}, ':', toString(target_repo_id)) AS target,
    weight,
    shared_contributors,
    collaboration_strength
FROM pruned_edges
ORDER BY weight DESC, shared_contributors DESC, source_repo_id, target_repo_id
FORMAT CSVWithNames
"""


def build_nodes_query(parameters: ExportParameters) -> str:
    platform = clickhouse_string(parameters.platform)
    return f"""WITH
    {build_top_repo_ctes(parameters, include_metrics=True)},
    normalized_repo_actor_months AS
    (
        SELECT
            repo_id,
            actor_id,
            yyyymm,
            argMax(repo_name, tuple(toFloat64(openrank), repo_name)) AS repo_name,
            max(toFloat64(openrank)) AS month_contribution
        FROM normalized_community_openrank
        WHERE platform = {platform}
          AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
          AND actor_id != 0
          AND repo_id IN (SELECT repo_id FROM top_repos)
        GROUP BY repo_id, actor_id, yyyymm
    ),
    contribution_stats AS
    (
        SELECT
            repo_id,
            argMax(repo_name, tuple(yyyymm, month_contribution, repo_name)) AS repo_name,
            uniqExact(actor_id) AS contributors,
            sum(month_contribution) AS normalized_contribution
        FROM normalized_repo_actor_months
        GROUP BY repo_id
    ),
    latest_node_info AS
    (
        SELECT
            id,
            argMax(status, updated_at) AS status,
            argMax(isFork, tuple(updated_at, isFork)) AS is_fork,
            argMax(primary_language, updated_at) AS primary_language,
            argMax(topics, updated_at) AS topics,
            argMax(description, updated_at) AS description
        FROM repo_info
        WHERE platform = {platform}
          AND id IN (SELECT repo_id FROM top_repos)
        GROUP BY id
    )
SELECT
    concat({platform}, ':', toString(top.repo_id)) AS id,
    if(notEmpty(top.repo_name), top.repo_name, stats.repo_name) AS name,
    {platform} AS platform,
    top.repo_id AS repo_id,
    round(top.openrank_sum, 6) AS openrank_sum,
    round(top.openrank_avg, 6) AS openrank_avg,
    round(top.openrank_max, 6) AS openrank_max,
    top.months_present AS months_present,
    stats.contributors AS contributors,
    round(stats.normalized_contribution, 6) AS normalized_contribution,
    if(notEmpty(info.primary_language), info.primary_language, 'Unknown') AS primary_language,
    arrayStringConcat(arraySlice(info.topics, 1, 12), '|') AS topics,
    replaceRegexpAll(substringUTF8(info.description, 1, 300), '[\\r\\n\\t]+', ' ') AS description,
    if(info.id = 0, 'missing', toString(info.status)) AS metadata_status,
    if({platform} = 'GitHub', concat('https://github.com/', name), '') AS url
FROM top_repos AS top
LEFT JOIN contribution_stats AS stats ON stats.repo_id = top.repo_id
LEFT JOIN latest_node_info AS info ON info.id = top.repo_id
ORDER BY top.openrank_sum DESC, top.repo_id
FORMAT CSVWithNames
"""


def build_freshness_query(platform: str) -> str:
    quoted = clickhouse_string(platform)
    return f"""SELECT
    (SELECT max(yyyymm)
     FROM normalized_community_openrank
     WHERE platform = {quoted}) AS normalized_latest,
    (SELECT max(toYYYYMM(created_at))
     FROM global_openrank
     WHERE platform = {quoted} AND type = 'Repo') AS global_latest,
    toYYYYMM(addMonths(toStartOfMonth(now()), -1)) AS previous_complete_month
FORMAT TabSeparatedRaw
"""


def build_month_coverage_query(platform: str, start_yyyymm: int, end_yyyymm: int) -> str:
    quoted = clickhouse_string(platform)
    return f"""SELECT
    (SELECT arrayStringConcat(arrayMap(x -> toString(x), arraySort(groupUniqArray(yyyymm))), ',')
     FROM normalized_community_openrank
     WHERE platform = {quoted}
       AND yyyymm BETWEEN {start_yyyymm} AND {end_yyyymm}) AS normalized_months,
    (SELECT arrayStringConcat(
        arrayMap(x -> toString(x), arraySort(groupUniqArray(toYYYYMM(created_at)))),
        ','
     )
     FROM global_openrank
     WHERE platform = {quoted}
       AND type = 'Repo'
       AND toYYYYMM(created_at) BETWEEN {start_yyyymm} AND {end_yyyymm}) AS global_months
FORMAT TabSeparatedRaw
"""


def build_source_uniqueness_query(parameters: ExportParameters) -> str:
    platform = clickhouse_string(parameters.platform)
    return f"""WITH
    {build_top_repo_ctes(parameters)}
SELECT
    (SELECT count()
     FROM global_openrank
     WHERE platform = {platform}
       AND type = 'Repo'
       AND toYYYYMM(created_at) BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS global_rows,
    (SELECT uniqExact(tuple(repo_id, toYYYYMM(created_at)))
     FROM global_openrank
     WHERE platform = {platform}
       AND type = 'Repo'
       AND toYYYYMM(created_at) BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS global_unique_keys,
    (SELECT count()
     FROM
     (
         SELECT repo_id, toYYYYMM(created_at) AS yyyymm
         FROM global_openrank
         WHERE platform = {platform}
           AND type = 'Repo'
           AND toYYYYMM(created_at) BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
           AND repo_id IN (SELECT repo_id FROM top_repos)
         GROUP BY repo_id, yyyymm
         HAVING uniqExact(toFloat64(openrank)) > 1
     )) AS global_conflicting_duplicate_keys,
    (SELECT count()
     FROM normalized_community_openrank
     WHERE platform = {platform}
       AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS normalized_rows,
    (SELECT uniqExact(tuple(repo_id, actor_id, yyyymm))
     FROM normalized_community_openrank
     WHERE platform = {platform}
       AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
       AND repo_id IN (SELECT repo_id FROM top_repos)) AS normalized_unique_keys,
    (SELECT count()
     FROM
     (
         SELECT repo_id, actor_id, yyyymm
         FROM normalized_community_openrank
         WHERE platform = {platform}
           AND yyyymm BETWEEN {parameters.start_yyyymm} AND {parameters.end_yyyymm}
           AND repo_id IN (SELECT repo_id FROM top_repos)
         GROUP BY repo_id, actor_id, yyyymm
         HAVING uniqExact(toFloat64(openrank)) > 1
     )) AS normalized_conflicting_duplicate_keys
FORMAT TabSeparatedRaw
"""


def source_uniqueness_stats(
    global_rows: int,
    global_unique: int,
    global_conflicting: int,
    normalized_rows: int,
    normalized_unique: int,
    normalized_conflicting: int,
) -> dict[str, Any]:
    sources = {
        "global_openrank": (global_rows, global_unique, global_conflicting),
        "normalized_community_openrank": (
            normalized_rows,
            normalized_unique,
            normalized_conflicting,
        ),
    }
    result: dict[str, Any] = {
        "duplicate_ratio_limit": SOURCE_DUPLICATE_RATIO_LIMIT,
        "dedupe_strategy": "max(openrank) per source-specific monthly key",
    }
    violations: list[str] = []
    for name, (rows, unique_keys, conflicting_keys) in sources.items():
        duplicate_rows = rows - unique_keys
        if (
            rows < 0
            or unique_keys < 0
            or unique_keys > rows
            or conflicting_keys < 0
            or conflicting_keys > unique_keys
            or conflicting_keys > duplicate_rows
        ):
            raise ExportError(f"Invalid source uniqueness counts for {name}")
        duplicate_ratio = duplicate_rows / rows if rows else 0.0
        conflicting_ratio = conflicting_keys / rows if rows else 0.0
        result[name] = {
            "rows": rows,
            "unique_keys": unique_keys,
            "duplicate_rows": duplicate_rows,
            "duplicate_ratio": round(duplicate_ratio, 10),
            "conflicting_duplicate_keys": conflicting_keys,
            "conflicting_key_ratio": round(conflicting_ratio, 10),
        }
        if duplicate_ratio > SOURCE_DUPLICATE_RATIO_LIMIT:
            violations.append(f"{name}={duplicate_ratio:.4%}")
    if violations:
        raise ExportError(
            "Source duplicate ratio exceeded the "
            f"{SOURCE_DUPLICATE_RATIO_LIMIT:.2%} limit: " + ", ".join(violations)
        )
    return result


def build_identity_query() -> str:
    return """SELECT version(), currentDatabase()
FORMAT TabSeparatedRaw
"""


FINAL_RELATIVE_FILES = {
    Path("edges.csv"),
    Path("nodes.csv"),
    Path("graph.graphml"),
    Path("manifest.json"),
    Path("qa.json"),
    Path("queries/edges.sql"),
    Path("queries/nodes.sql"),
    Path("queries/source_qa.sql"),
}


def make_output_paths(root: Path) -> dict[str, Path]:
    return {
        "edges": root / "edges.csv",
        "nodes": root / "nodes.csv",
        "node_candidates": root / "nodes.candidates.csv",
        "graphml": root / "graph.graphml",
        "manifest": root / "manifest.json",
        "qa": root / "qa.json",
        "edge_sql": root / "queries" / "edges.sql",
        "node_sql": root / "queries" / "nodes.sql",
        "source_qa_sql": root / "queries" / "source_qa.sql",
    }


class OutputWorkspace:
    """Build a complete snapshot in staging and publish the directory as a unit."""

    def __init__(self, output_dir: Path, *, force: bool, run_id: str) -> None:
        raw_output = Path(os.path.abspath(output_dir.expanduser()))
        if raw_output == Path(raw_output.anchor):
            raise ExportError("Refusing to use a filesystem root as the output directory")
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        parent = raw_output.parent.resolve()
        self.final_dir = parent / raw_output.name
        self.force = force
        self.run_id = run_id
        self.lock_path = parent / f".{raw_output.name}.lock"
        self.stage_dir: Path | None = None
        self.paths: dict[str, Path] = {}
        self._lock_fd: int | None = None
        self._published = False

    def _validate_existing_output(self) -> None:
        if not self.final_dir.exists():
            return
        if self.final_dir.is_symlink() or not self.final_dir.is_dir():
            raise ExportError("Existing output target must be a normal directory")
        if not self.force:
            raise ExportError(
                f"Output directory already exists; use --force to replace: {self.final_dir}"
            )
        for path in self.final_dir.rglob("*"):
            if path.is_symlink():
                raise ExportError(f"Refusing to replace output containing a symlink: {path}")
            relative = path.relative_to(self.final_dir)
            if path.is_dir():
                if relative != Path("queries"):
                    raise ExportError(
                        f"Refusing to replace output containing an unexpected directory: {path}"
                    )
            elif relative not in FINAL_RELATIVE_FILES:
                raise ExportError(
                    f"Refusing to replace output containing an unexpected file: {path}"
                )

    def __enter__(self) -> "OutputWorkspace":
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._lock_fd = os.open(self.lock_path, flags, 0o600)
        except FileExistsError:
            raise ExportError(
                f"Another export appears to be using this output: {self.final_dir}"
            ) from None
        os.write(self._lock_fd, self.run_id.encode("ascii"))
        try:
            self._validate_existing_output()
            self.stage_dir = Path(
                tempfile.mkdtemp(
                    dir=self.final_dir.parent,
                    prefix=f".{self.final_dir.name}.stage-{self.run_id}-",
                )
            )
            (self.stage_dir / "queries").mkdir()
            self.paths = make_output_paths(self.stage_dir)
            return self
        except Exception:
            try:
                if self.stage_dir is not None and self.stage_dir.exists():
                    stage_resolved = self.stage_dir.resolve()
                    if (
                        stage_resolved.parent == self.final_dir.parent.resolve()
                        and stage_resolved.name.startswith(
                            f".{self.final_dir.name}.stage-{self.run_id}-"
                        )
                    ):
                        shutil.rmtree(stage_resolved)
                    self.stage_dir = None
            finally:
                self._release_lock()
            raise

    def publish(self) -> None:
        if self.stage_dir is None:
            raise ExportError("Output staging directory was not initialized")
        missing = [
            str(relative)
            for relative in FINAL_RELATIVE_FILES
            if not (self.stage_dir / relative).is_file()
        ]
        if missing:
            raise ExportError("Cannot publish incomplete output: " + ", ".join(missing))

        backup: Path | None = None
        if self.final_dir.exists():
            backup = self.final_dir.parent / (
                f".{self.final_dir.name}.backup-{self.run_id}"
            )
            os.replace(self.final_dir, backup)
        try:
            os.replace(self.stage_dir, self.final_dir)
            self.stage_dir = None
            self._published = True
        except Exception:
            if backup is not None and backup.exists() and not self.final_dir.exists():
                os.replace(backup, self.final_dir)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        if self.lock_path.exists() and not self.lock_path.is_symlink():
            try:
                if self.lock_path.read_text(encoding="ascii") == self.run_id:
                    self.lock_path.unlink()
            except OSError:
                pass

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.stage_dir is not None and self.stage_dir.exists():
            expected_parent = self.final_dir.parent.resolve()
            stage_resolved = self.stage_dir.resolve()
            if stage_resolved.parent == expected_parent and stage_resolved.name.startswith(
                f".{self.final_dir.name}.stage-{self.run_id}-"
            ):
                shutil.rmtree(stage_resolved)
        self._release_lock()


def write_text(path: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".part",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(text)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[max(index, 0)]


def inspect_edges(
    path: Path,
    *,
    platform: str,
    min_shared: int,
) -> tuple[set[int], dict[str, Any]]:
    expected_prefix = platform + ":"
    repo_ids: set[int] = set()
    seen_edges: set[tuple[int, int]] = set()
    weights: list[float] = []
    shared_counts: list[float] = []
    self_loops = 0
    duplicate_edges = 0
    invalid_rows = 0

    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != EXPECTED_EDGE_COLUMNS:
            raise ExportError(
                f"Unexpected edge columns: {reader.fieldnames}; expected {EXPECTED_EDGE_COLUMNS}"
            )
        for row in reader:
            if None in row:
                invalid_rows += 1
                continue
            source_id = row["source"]
            target_id = row["target"]
            if source_id == target_id:
                self_loops += 1
            try:
                if not source_id.startswith(expected_prefix) or not target_id.startswith(
                    expected_prefix
                ):
                    raise ValueError("unexpected platform prefix")
                source_repo = int(source_id[len(expected_prefix) :])
                target_repo = int(target_id[len(expected_prefix) :])
                if source_repo <= 0 or target_repo <= 0:
                    raise ValueError("repository IDs must be positive")
                if source_id != f"{platform}:{source_repo}" or target_id != (
                    f"{platform}:{target_repo}"
                ):
                    raise ValueError("endpoint IDs are not canonical")
                if source_repo >= target_repo:
                    raise ValueError("edge endpoints are not in canonical order")
                weight = float(row["weight"])
                shared = int(row["shared_contributors"])
                collaboration_strength = float(row["collaboration_strength"])
                if (
                    not math.isfinite(weight)
                    or not math.isfinite(collaboration_strength)
                    or weight <= 0
                    or collaboration_strength <= 0
                    or shared < min_shared
                ):
                    raise ValueError("edge values violate configured thresholds")
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            edge = (source_repo, target_repo)
            if edge in seen_edges:
                duplicate_edges += 1
            seen_edges.add(edge)
            repo_ids.update((source_repo, target_repo))
            weights.append(weight)
            shared_counts.append(float(shared))

    if not seen_edges:
        raise ExportError("Edge export was empty")
    if duplicate_edges or self_loops or invalid_rows:
        raise ExportError(
            "Edge QA failed: "
            f"duplicates={duplicate_edges}, self_loops={self_loops}, invalid={invalid_rows}"
        )
    weights.sort()
    shared_counts.sort()
    return repo_ids, {
        "edge_count": len(seen_edges),
        "endpoint_node_count": len(repo_ids),
        "duplicate_edges": duplicate_edges,
        "self_loops": self_loops,
        "invalid_rows": invalid_rows,
        "weight": {
            "min": weights[0],
            "p50": percentile(weights, 0.50),
            "p90": percentile(weights, 0.90),
            "p99": percentile(weights, 0.99),
            "max": weights[-1],
        },
        "shared_contributors": {
            "min": int(shared_counts[0]),
            "p50": int(percentile(shared_counts, 0.50)),
            "p90": int(percentile(shared_counts, 0.90)),
            "p99": int(percentile(shared_counts, 0.99)),
            "max": int(shared_counts[-1]),
        },
    }


def filter_node_candidates(
    candidate_path: Path,
    destination: Path,
    expected_repo_ids: set[int],
    *,
    label_count: int,
) -> int:
    labels_written = 0
    rows_written = 0
    temporary: Path | None = None
    try:
        with candidate_path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != CANDIDATE_NODE_COLUMNS:
                raise ExportError(
                    "Unexpected candidate node columns: "
                    f"{reader.fieldnames}; expected {CANDIDATE_NODE_COLUMNS}"
                )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".part",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                writer = csv.DictWriter(output, fieldnames=EXPECTED_NODE_COLUMNS)
                writer.writeheader()
                for row in reader:
                    if None in row:
                        raise ExportError("Candidate node CSV contains extra columns")
                    try:
                        repo_id = int(row["repo_id"])
                    except ValueError:
                        raise ExportError("Candidate node CSV contains an invalid repo_id") from None
                    if repo_id not in expected_repo_ids:
                        continue
                    display_label = ""
                    if labels_written < label_count:
                        display_label = row["name"]
                        labels_written += 1
                    final_row = {
                        "id": row["id"],
                        "name": row["name"],
                        "display_label": display_label,
                        **{field: row[field] for field in CANDIDATE_NODE_COLUMNS[2:]},
                    }
                    writer.writerow(final_row)
                    rows_written += 1
        if temporary is None:
            raise ExportError("Node filtering produced no temporary output")
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return rows_written


def inspect_nodes(
    path: Path,
    expected_repo_ids: set[int],
    *,
    platform: str,
    months: int,
    label_count: int,
) -> dict[str, Any]:
    seen_ids: set[str] = set()
    seen_repo_ids: set[int] = set()
    duplicate_nodes = 0
    empty_names = 0
    metadata_missing = 0
    language_unknown = 0
    invalid_rows = 0
    labels_present = 0
    contribution_relative_errors: list[float] = []

    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != EXPECTED_NODE_COLUMNS:
            raise ExportError(
                f"Unexpected node columns: {reader.fieldnames}; expected {EXPECTED_NODE_COLUMNS}"
            )
        for row in reader:
            if None in row:
                invalid_rows += 1
                continue
            try:
                node_id = row["id"]
                repo_id = int(row["repo_id"])
                openrank_sum = float(row["openrank_sum"])
                openrank_avg = float(row["openrank_avg"])
                openrank_max = float(row["openrank_max"])
                months_present = int(row["months_present"])
                contributors = int(row["contributors"])
                normalized_contribution = float(row["normalized_contribution"])
                numeric_values = (
                    openrank_sum,
                    openrank_avg,
                    openrank_max,
                    normalized_contribution,
                )
                if repo_id <= 0 or not all(math.isfinite(value) for value in numeric_values):
                    raise ValueError("invalid numeric node fields")
                if min(numeric_values) < 0 or not 1 <= months_present <= months:
                    raise ValueError("node values are outside expected ranges")
                if contributors < 0:
                    raise ValueError("contributors must not be negative")
                if node_id != f"{platform}:{repo_id}" or row["platform"] != platform:
                    raise ValueError("node identity fields are inconsistent")
                if row["display_label"]:
                    labels_present += 1
                    if row["display_label"] != row["name"]:
                        raise ValueError("display_label must equal name when present")
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            if node_id in seen_ids:
                duplicate_nodes += 1
            seen_ids.add(node_id)
            seen_repo_ids.add(repo_id)
            if not row["name"].strip():
                empty_names += 1
            if row["metadata_status"] == "missing":
                metadata_missing += 1
            if row["primary_language"] == "Unknown":
                language_unknown += 1
            if openrank_sum > 0:
                contribution_relative_errors.append(
                    abs(normalized_contribution - openrank_sum) / openrank_sum
                )

    missing_nodes = sorted(expected_repo_ids - seen_repo_ids)
    extra_nodes = sorted(seen_repo_ids - expected_repo_ids)
    if (
        duplicate_nodes
        or empty_names
        or missing_nodes
        or extra_nodes
        or invalid_rows
        or labels_present > label_count
    ):
        raise ExportError(
            "Node QA failed: "
            f"duplicates={duplicate_nodes}, empty_names={empty_names}, "
            f"missing_endpoints={len(missing_nodes)}, extra_nodes={len(extra_nodes)}, "
            f"invalid={invalid_rows}, labels={labels_present}/{label_count}"
        )
    contribution_relative_errors.sort()
    return {
        "node_count": len(seen_ids),
        "duplicate_nodes": duplicate_nodes,
        "empty_names": empty_names,
        "missing_edge_endpoints": len(missing_nodes),
        "extra_nodes": len(extra_nodes),
        "invalid_rows": invalid_rows,
        "display_labels": labels_present,
        "metadata_missing": metadata_missing,
        "language_unknown": language_unknown,
        "metadata_coverage_ratio": round(
            (len(seen_ids) - metadata_missing) / len(seen_ids), 6
        ),
        "language_coverage_ratio": round(
            (len(seen_ids) - language_unknown) / len(seen_ids), 6
        ),
        "normalized_vs_global_relative_error": {
            "p50": percentile(contribution_relative_errors, 0.50),
            "p90": percentile(contribution_relative_errors, 0.90),
            "p99": percentile(contribution_relative_errors, 0.99),
            "max": contribution_relative_errors[-1]
            if contribution_relative_errors
            else 0.0,
        },
    }


def xml_safe_text(value: str) -> str:
    """Remove characters that XML 1.0 cannot represent."""
    return "".join(
        character
        for character in value
        if character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
    )


def build_graphml(nodes_path: Path, edges_path: Path, destination: Path) -> dict[str, int]:
    """Stream a GraphML file containing all node and edge attributes."""
    temporary: Path | None = None
    node_count = 0
    edge_count = 0
    node_ids: set[str] = set()
    missing_edge_endpoints = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".part",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            output.write(
                '<graphml xmlns="http://graphml.graphdrawing.org/xmlns" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                'xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns '
                'http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">\n'
            )
            for field, graphml_type in NODE_GRAPHML_TYPES.items():
                output.write(
                    f'  <key id="n_{field}" for="node" attr.name={quoteattr(field)} '
                    f'attr.type={quoteattr(graphml_type)}/>\n'
                )
            for field, graphml_type in EDGE_GRAPHML_TYPES.items():
                output.write(
                    f'  <key id="e_{field}" for="edge" attr.name={quoteattr(field)} '
                    f'attr.type={quoteattr(graphml_type)}/>\n'
                )
            output.write('  <graph id="repository-collaboration" edgedefault="undirected">\n')

            with nodes_path.open("r", encoding="utf-8", newline="") as nodes_source:
                reader = csv.DictReader(nodes_source)
                for row in reader:
                    node_ids.add(row["id"])
                    output.write(f'    <node id={quoteattr(xml_safe_text(row["id"]))}>\n')
                    for field in NODE_GRAPHML_TYPES:
                        value = row[field]
                        if value != "":
                            safe_value = escape(xml_safe_text(value))
                            output.write(
                                f'      <data key="n_{field}">{safe_value}</data>\n'
                            )
                    output.write("    </node>\n")
                    node_count += 1

            with edges_path.open("r", encoding="utf-8", newline="") as edges_source:
                reader = csv.DictReader(edges_source)
                for row in reader:
                    if row["source"] not in node_ids or row["target"] not in node_ids:
                        missing_edge_endpoints += 1
                    output.write(
                        f'    <edge id="e{edge_count}" '
                        f'source={quoteattr(xml_safe_text(row["source"]))} '
                        f'target={quoteattr(xml_safe_text(row["target"]))}>\n'
                    )
                    for field in EDGE_GRAPHML_TYPES:
                        safe_value = escape(xml_safe_text(row[field]))
                        output.write(
                            f'      <data key="e_{field}">{safe_value}</data>\n'
                        )
                    output.write("    </edge>\n")
                    edge_count += 1

            output.write("  </graph>\n</graphml>\n")
        if missing_edge_endpoints:
            raise ExportError(
                f"GraphML generation found {missing_edge_endpoints} missing edge endpoints"
            )
        if temporary is None:
            raise ExportError("GraphML generation produced no temporary output")
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

    return {
        "nodes": node_count,
        "edges": edge_count,
        "missing_edge_endpoints": missing_edge_endpoints,
        "bytes": destination.stat().st_size,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a bounded repository collaboration graph for Graphia."
    )
    parser.add_argument("--preset", choices=PRESETS, default="preview")
    parser.add_argument("--platform", type=parse_platform, default="GitHub")
    parser.add_argument("--end-month", type=parse_yyyymm)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--top-repos", type=int)
    parser.add_argument("--min-shared", type=int)
    parser.add_argument("--max-actor-degree", type=int)
    parser.add_argument("--max-edges-per-node", type=int)
    parser.add_argument("--label-count", type=int)
    parser.add_argument("--max-result-rows", type=int)
    parser.add_argument("--max-threads", type=int, default=4)
    parser.add_argument("--max-execution-time", type=int, default=300)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def positive(name: str, value: int) -> int:
    if value <= 0:
        raise ExportError(f"{name} must be greater than zero")
    return value


def configured_positive(name: str, value: int | None, default: int) -> int:
    return positive(name, value if value is not None else default)


def validate_source_month(name: str, value: int) -> int:
    try:
        return parse_yyyymm(str(value))
    except argparse.ArgumentTypeError:
        raise ExportError(f"{name} returned an invalid YYYYMM value: {value}") from None


def month_sequence(start_yyyymm: int, count: int) -> list[int]:
    return [shift_month(start_yyyymm, offset) for offset in range(count)]


def parse_month_csv(value: str) -> set[int]:
    if not value:
        return set()
    return {validate_source_month("month coverage", int(item)) for item in value.split(",")}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    preset = PRESETS[args.preset]
    months = positive("months", args.months)
    top_repos = configured_positive(
        "top_repos", args.top_repos, preset["top_repos"]
    )
    min_shared = configured_positive(
        "min_shared", args.min_shared, preset["min_shared"]
    )
    max_actor_degree = configured_positive(
        "max_actor_degree", args.max_actor_degree, preset["max_actor_degree"]
    )
    if max_actor_degree < 2:
        raise ExportError("max_actor_degree must be at least 2")
    max_edges_per_node = configured_positive(
        "max_edges_per_node",
        args.max_edges_per_node,
        preset["max_edges_per_node"],
    )
    label_count = configured_positive(
        "label_count", args.label_count, preset["label_count"]
    )
    max_result_rows = configured_positive(
        "max_result_rows", args.max_result_rows, preset["max_result_rows"]
    )
    max_threads = positive("max_threads", args.max_threads)
    max_execution_time = positive("max_execution_time", args.max_execution_time)

    connection = ConnectionConfig.from_environment(args.allow_insecure_http)
    run_id = uuid.uuid4().hex[:12]
    client = ClickHouseHttpClient(
        connection,
        max_threads=max_threads,
        max_execution_time=max_execution_time,
        run_id=run_id,
    )

    identity = client.text(build_identity_query(), query_name="identity").split("\t")
    if len(identity) != 2:
        raise ExportError("Unexpected response while reading ClickHouse identity")
    version, current_database = identity
    if current_database != connection.database:
        raise ExportError(
            f"Connected database {current_database!r} did not match configured database"
        )

    freshness = client.text(
        build_freshness_query(args.platform), query_name="freshness"
    ).split("\t")
    if len(freshness) != 3:
        raise ExportError("Unexpected response while reading source freshness")
    try:
        normalized_latest, global_latest, previous_complete_month = (
            validate_source_month(name, int(value))
            for name, value in zip(
                (
                    "normalized latest month",
                    "global latest month",
                    "server previous complete month",
                ),
                freshness,
                strict=True,
            )
        )
    except ValueError:
        raise ExportError("Source freshness contained a non-numeric month") from None
    latest_available = min(
        normalized_latest,
        global_latest,
        previous_complete_month,
    )
    end_yyyymm = args.end_month if args.end_month is not None else latest_available
    if end_yyyymm > latest_available:
        raise ExportError(
            f"Requested end month {end_yyyymm} is newer than complete source month "
            f"{latest_available}"
        )
    start_yyyymm = shift_month(end_yyyymm, -(months - 1))
    expected_months = set(month_sequence(start_yyyymm, months))
    coverage = client.text(
        build_month_coverage_query(args.platform, start_yyyymm, end_yyyymm),
        query_name="month-coverage",
    ).split("\t")
    if len(coverage) != 2:
        raise ExportError("Unexpected response while reading source month coverage")
    normalized_months = parse_month_csv(coverage[0])
    global_months = parse_month_csv(coverage[1])
    missing_normalized = sorted(expected_months - normalized_months)
    missing_global = sorted(expected_months - global_months)
    if missing_normalized or missing_global:
        raise ExportError(
            "Source month coverage is incomplete: "
            f"normalized missing={missing_normalized}, global missing={missing_global}"
        )

    parameters = ExportParameters(
        preset=args.preset,
        platform=args.platform,
        start_yyyymm=start_yyyymm,
        end_yyyymm=end_yyyymm,
        months=months,
        top_repos=top_repos,
        min_shared=min_shared,
        max_actor_degree=max_actor_degree,
        max_edges_per_node=max_edges_per_node,
        label_count=label_count,
        max_result_rows=max_result_rows,
        max_threads=max_threads,
        max_execution_time=max_execution_time,
    )

    default_output = (
        Path(__file__).resolve().parent
        / "output"
        / f"open_galaxy_{args.platform.lower()}_{start_yyyymm}_{end_yyyymm}_{args.preset}"
    )
    output_dir = args.output_dir or default_output

    print(
        f"Connected to ClickHouse {version}; exporting {args.platform} "
        f"{start_yyyymm}-{end_yyyymm} ({args.preset})."
    )

    with OutputWorkspace(output_dir, force=args.force, run_id=run_id) as workspace:
        paths = workspace.paths
        source_qa_query = build_source_uniqueness_query(parameters)
        write_text(paths["source_qa_sql"], source_qa_query)
        source_qa_values = client.text(
            source_qa_query,
            query_name="source-uniqueness",
        ).split("\t")
        if len(source_qa_values) != 6:
            raise ExportError("Unexpected response while checking source uniqueness")
        try:
            (
                global_rows,
                global_unique,
                global_conflicting,
                normalized_rows,
                normalized_unique,
                normalized_conflicting,
            ) = (int(value) for value in source_qa_values)
        except ValueError:
            raise ExportError("Source uniqueness check returned non-integer values") from None
        source_uniqueness = source_uniqueness_stats(
            global_rows,
            global_unique,
            global_conflicting,
            normalized_rows,
            normalized_unique,
            normalized_conflicting,
        )

        edge_query = build_edges_query(parameters)
        write_text(paths["edge_sql"], edge_query)
        edge_size, edge_seconds = client.export_csv(
            edge_query,
            paths["edges"],
            query_name="edges",
            max_result_rows=max_result_rows,
        )
        repo_ids, edge_qa = inspect_edges(
            paths["edges"], platform=args.platform, min_shared=min_shared
        )
        print(
            f"Exported {edge_qa['edge_count']:,} edges and "
            f"{edge_qa['endpoint_node_count']:,} endpoint nodes in {edge_seconds:.2f}s."
        )

        node_query = build_nodes_query(parameters)
        write_text(paths["node_sql"], node_query)
        _, node_seconds = client.export_csv(
            node_query,
            paths["node_candidates"],
            query_name="nodes",
            max_result_rows=top_repos + 1,
        )
        filtered_nodes = filter_node_candidates(
            paths["node_candidates"],
            paths["nodes"],
            repo_ids,
            label_count=label_count,
        )
        paths["node_candidates"].unlink()
        node_size = paths["nodes"].stat().st_size
        node_qa = inspect_nodes(
            paths["nodes"],
            repo_ids,
            platform=args.platform,
            months=months,
            label_count=label_count,
        )
        if filtered_nodes != node_qa["node_count"]:
            raise ExportError("Filtered node count did not match node QA")
        print(f"Exported {node_qa['node_count']:,} nodes in {node_seconds:.2f}s.")

        graphml_stats = build_graphml(paths["nodes"], paths["edges"], paths["graphml"])
        if graphml_stats["nodes"] != node_qa["node_count"]:
            raise ExportError("GraphML node count did not match nodes.csv")
        if graphml_stats["edges"] != edge_qa["edge_count"]:
            raise ExportError("GraphML edge count did not match edges.csv")

        qa = {
            "status": "pass",
            "source_months": {
                "expected": sorted(expected_months),
                "normalized": sorted(normalized_months),
                "global": sorted(global_months),
            },
            "source_uniqueness": source_uniqueness,
            "edges": edge_qa,
            "nodes": node_qa,
        }
        write_json(paths["qa"], qa)

        manifest = {
            "schema_version": 2,
            "tool_version": "1.2.0",
            "run_id": run_id,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "clickhouse": {
                "version": version,
                "database": current_database,
                "readonly": True,
                "normalized_latest_month": normalized_latest,
                "global_latest_month": global_latest,
                "server_previous_complete_month": previous_complete_month,
            },
            "parameters": asdict(parameters),
            "semantics": {
                "node": "repository",
                "edge": "two repositories sharing at least the configured number of known non-bot contributors",
                "weight": (
                    "sum of annual-degree-corrected actor mass; each eligible actor "
                    "distributes total mass 1 across all annual repository pairs"
                ),
                "collaboration_strength": (
                    "sum(weight_per_actor * sqrt(contribution_a * contribution_b))"
                ),
                "fork_filter": "known forks are excluded; missing repo metadata is retained",
                "source_deduplication": (
                    "global_openrank uses max(openrank) per (repo_id, month); "
                    "normalized_community_openrank uses max(openrank) per "
                    "(repo_id, actor_id, month)"
                ),
                "source_tables": [
                    "global_openrank",
                    "normalized_community_openrank",
                    "repo_info",
                ],
            },
            "outputs": {
                "edges.csv": {
                    "bytes": edge_size,
                    "sha256": sha256_file(paths["edges"]),
                    "rows": edge_qa["edge_count"],
                    "query_seconds": round(edge_seconds, 3),
                },
                "nodes.csv": {
                    "bytes": node_size,
                    "sha256": sha256_file(paths["nodes"]),
                    "rows": node_qa["node_count"],
                    "query_seconds": round(node_seconds, 3),
                },
                "graph.graphml": {
                    "bytes": graphml_stats["bytes"],
                    "sha256": sha256_file(paths["graphml"]),
                    "nodes": graphml_stats["nodes"],
                    "edges": graphml_stats["edges"],
                },
                "qa.json": {"sha256": sha256_file(paths["qa"])},
                "queries/edges.sql": {"sha256": sha256_file(paths["edge_sql"])},
                "queries/nodes.sql": {"sha256": sha256_file(paths["node_sql"])},
                "queries/source_qa.sql": {
                    "sha256": sha256_file(paths["source_qa_sql"])
                },
            },
        }
        write_json(paths["manifest"], manifest)
        workspace.publish()
        final_output = workspace.final_dir
    print(f"QA passed. Output: {final_output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
