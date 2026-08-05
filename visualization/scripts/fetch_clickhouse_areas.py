#!/usr/bin/env python3
"""Export curated repository-area labels from ClickHouse with a read-only query.

Credentials are accepted only through CLICKHOUSE_* environment variables and
are never written to the output. The resulting JSON contains taxonomy labels
for repositories already present in the checked-in graph artifact.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ARTIFACT_NAME = "open-galaxy-github-202508-202607-preview"


def require_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def read_repo_ids(path: Path) -> list[int]:
    repo_ids: set[int] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("repo_id") or "").strip()
            if value:
                repo_ids.add(int(value))
    if not repo_ids:
        raise ValueError(f"No repo_id values found in {path}")
    return sorted(repo_ids)


def build_query(repo_ids: list[int]) -> str:
    target_ids = ",".join(str(repo_id) for repo_id in repo_ids)
    return f"""
WITH [{target_ids}] AS target_ids
SELECT
  toString(entity_id) AS repo_id,
  groupUniqArrayIf(name, type = 'Tech-0') AS tech0_labels,
  groupUniqArrayIf(name, type = 'Domain-0') AS domain0_labels,
  groupUniqArrayIf(name, startsWith(id, ':technology/ai')) AS ai_labels,
  max(id = ':technology/agentic_ai') AS is_agentic_ai
FROM flatten_labels
WHERE platform = 'GitHub'
  AND entity_type = 'Repo'
  AND entity_id IN target_ids
GROUP BY entity_id
ORDER BY entity_id
FORMAT JSONEachRow
""".strip()


class RejectRedirectHandler(HTTPRedirectHandler):
    """Never forward a ClickHouse Authorization header through a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def query_clickhouse(
    query: str,
    *,
    allow_insecure_http: bool,
) -> list[dict[str, object]]:
    host = require_environment("CLICKHOUSE_HOST").rstrip("/")
    parsed = urlsplit(host)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("CLICKHOUSE_HOST must be a valid http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise RuntimeError("CLICKHOUSE_HOST must not contain embedded credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RuntimeError(
            "CLICKHOUSE_HOST must contain only scheme, host, and optional port"
        )
    if parsed.scheme == "http" and not allow_insecure_http:
        raise RuntimeError(
            "Refusing plaintext HTTP because Basic Auth would not be encrypted. "
            "Use HTTPS or explicitly pass --allow-insecure-http for this run."
        )
    username = require_environment("CLICKHOUSE_USERNAME")
    if ":" in username:
        raise RuntimeError("CLICKHOUSE_USERNAME must not contain ':' for Basic Auth")
    password = require_environment("CLICKHOUSE_PASSWORD")
    database = require_environment("CLICKHOUSE_DATABASE")
    params = urlencode({"database": database, "readonly": "1"})
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{host}/?{params}",
        data=query.encode("utf-8"),
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "open-galaxy-area-export/1.0",
        },
        method="POST",
    )
    opener = build_opener(RejectRedirectHandler())
    with opener.open(request, timeout=90) as response:
        body = response.read().decode("utf-8")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
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
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    script_path = Path(__file__).resolve()
    repository_root = script_path.parents[2]
    artifact_root = repository_root / "artifacts" / ARTIFACT_NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=Path, default=artifact_root / "nodes.csv")
    parser.add_argument(
        "--output",
        type=Path,
        default=artifact_root / "area-enrichment.json",
    )
    parser.add_argument(
        "--allow-insecure-http",
        action="store_true",
        help="Explicitly allow unencrypted Basic Auth to a plaintext HTTP endpoint.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_ids = read_repo_ids(args.nodes.resolve())
    rows = query_clickhouse(
        build_query(repo_ids),
        allow_insecure_http=args.allow_insecure_http,
    )
    by_repo_id = {str(row["repo_id"]): row for row in rows}
    payload: dict[str, object] = {
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "repositoryCount": len(repo_ids),
        "curatedMatchCount": len(by_repo_id),
        "repositories": by_repo_id,
    }
    output = args.output.resolve()
    write_json_atomic(output, payload)
    print(
        json.dumps(
            {
                "output": str(output),
                "repositories": len(repo_ids),
                "curatedMatches": len(by_repo_id),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
