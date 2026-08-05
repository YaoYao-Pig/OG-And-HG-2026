import csv
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import export_graph


def parameters() -> export_graph.ExportParameters:
    return export_graph.ExportParameters(
        preset="preview",
        platform="GitHub",
        start_yyyymm=202508,
        end_yyyymm=202607,
        months=12,
        top_repos=5000,
        min_shared=2,
        max_actor_degree=30,
        max_edges_per_node=30,
        label_count=150,
        max_result_rows=200000,
        max_threads=4,
        max_execution_time=30,
    )


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def read_request_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)


def start_server(handler: type[BaseHTTPRequestHandler]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class MonthTests(unittest.TestCase):
    def test_shift_month_across_year(self) -> None:
        self.assertEqual(export_graph.shift_month(202607, -11), 202508)
        self.assertEqual(export_graph.shift_month(202601, -1), 202512)
        self.assertEqual(
            export_graph.month_sequence(202511, 4),
            [202511, 202512, 202601, 202602],
        )

    def test_parse_yyyymm_rejects_invalid_month(self) -> None:
        with self.assertRaises(Exception):
            export_graph.parse_yyyymm("202613")
        with self.assertRaises(export_graph.ExportError):
            export_graph.validate_source_month("source", 0)

    def test_explicit_zero_does_not_fall_back_to_preset(self) -> None:
        with self.assertRaises(export_graph.ExportError):
            export_graph.configured_positive("top_repos", 0, 5000)


class QueryTests(unittest.TestCase):
    def test_edges_query_has_global_degree_and_hard_pruning(self) -> None:
        query = export_graph.build_edges_query(parameters())
        self.assertIn("yyyymm BETWEEN 202508 AND 202607", query)
        self.assertIn("LIMIT 5000", query)
        self.assertIn("actor_annual_degrees", query)
        self.assertIn("annual_repo_degree BETWEEN 2 AND 30", query)
        self.assertIn("actor_repo_months", query)
        self.assertIn("max(toFloat64(openrank)) AS month_contribution", query)
        self.assertIn("edge_rank <= 30", query)
        self.assertIn("shared_contributors >= 2", query)
        self.assertIn("collaboration_strength", query)
        self.assertNotIn("FROM events", query)

    def test_nodes_query_reuses_top_repo_cte_without_literal_id_list(self) -> None:
        query = export_graph.build_nodes_query(parameters())
        self.assertIn("FROM top_repos AS top", query)
        self.assertIn("repo_id IN (SELECT repo_id FROM top_repos)", query)
        self.assertIn("normalized_repo_actor_months", query)
        self.assertIn("max(toFloat64(openrank)) AS month_contribution", query)
        self.assertLess(len(query.encode("utf-8")), 32_000)
        self.assertIn("FORMAT CSVWithNames", query)

    def test_top_repo_query_deduplicates_monthly_rows(self) -> None:
        query = export_graph.build_top_repo_ctes(parameters(), include_metrics=True)
        self.assertIn("global_repo_months", query)
        self.assertIn("max(toFloat64(openrank)) AS month_openrank", query)


class SourceUniquenessTests(unittest.TestCase):
    def test_small_duplicate_ratio_is_recorded(self) -> None:
        stats = export_graph.source_uniqueness_stats(
            100,
            100,
            0,
            1_000_000,
            999_900,
            80,
        )
        normalized = stats["normalized_community_openrank"]
        self.assertEqual(normalized["duplicate_rows"], 100)
        self.assertEqual(normalized["duplicate_ratio"], 0.0001)
        self.assertEqual(normalized["conflicting_duplicate_keys"], 80)

    def test_excessive_duplicate_ratio_fails(self) -> None:
        with self.assertRaises(export_graph.ExportError):
            export_graph.source_uniqueness_stats(100, 98, 1, 100, 100, 0)


class ConnectionTests(unittest.TestCase):
    def test_missing_environment_is_reported_without_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(export_graph.ExportError) as raised:
                export_graph.ConnectionConfig.from_environment(False)
        self.assertIn("CLICKHOUSE_PASSWORD", str(raised.exception))

    def test_plain_http_requires_explicit_opt_in(self) -> None:
        env = {
            "CLICKHOUSE_HOST": "http://example.test:8123",
            "CLICKHOUSE_USERNAME": "user",
            "CLICKHOUSE_PASSWORD": " secret with spaces ",
            "CLICKHOUSE_DATABASE": "db",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(export_graph.ExportError):
                export_graph.ConnectionConfig.from_environment(False)
            config = export_graph.ConnectionConfig.from_environment(True)
        self.assertEqual(config.endpoint, "http://example.test:8123")
        self.assertEqual(config.password, " secret with spaces ")

    def test_endpoint_userinfo_and_colon_username_are_rejected(self) -> None:
        env = {
            "CLICKHOUSE_HOST": "https://embedded@example.test",
            "CLICKHOUSE_USERNAME": "user",
            "CLICKHOUSE_PASSWORD": "secret",
            "CLICKHOUSE_DATABASE": "db",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(export_graph.ExportError):
                export_graph.ConnectionConfig.from_environment(False)
        env["CLICKHOUSE_HOST"] = "https://example.test"
        env["CLICKHOUSE_USERNAME"] = "bad:user"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(export_graph.ExportError):
                export_graph.ConnectionConfig.from_environment(False)

    def test_redirect_is_not_followed_with_authorization(self) -> None:
        class TargetHandler(QuietHandler):
            hits = 0

            def do_GET(self) -> None:
                type(self).hits += 1
                self.send_response(200)
                self.end_headers()

            do_POST = do_GET

        target, target_thread = start_server(TargetHandler)
        target_url = f"http://127.0.0.1:{target.server_port}/"

        class RedirectHandler(QuietHandler):
            def do_POST(self) -> None:
                self.read_request_body()
                self.send_response(302)
                self.send_header("Location", target_url)
                self.end_headers()

        redirect, redirect_thread = start_server(RedirectHandler)
        try:
            config = export_graph.ConnectionConfig(
                endpoint=f"http://127.0.0.1:{redirect.server_port}",
                username="user",
                password="secret",
                database="db",
                allow_insecure_http=True,
            )
            client = export_graph.ClickHouseHttpClient(config, 1, 5, "test")
            with self.assertRaises(export_graph.ExportError):
                client.text("SELECT 1", query_name="redirect")
            self.assertEqual(TargetHandler.hits, 0)
        finally:
            redirect.shutdown()
            target.shutdown()
            redirect_thread.join()
            target_thread.join()
            redirect.server_close()
            target.server_close()

    def test_streamed_exception_body_is_rejected(self) -> None:
        class ExceptionHandler(QuietHandler):
            def do_POST(self) -> None:
                self.read_request_body()
                body = (
                    b"source,target,weight,shared_contributors,collaboration_strength\n"
                    b"GitHub:1,GitHub:2,1,2,1\n"
                    b"Code: 241. DB::Exception: simulated\n"
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server, thread = start_server(ExceptionHandler)
        try:
            config = export_graph.ConnectionConfig(
                endpoint=f"http://127.0.0.1:{server.server_port}",
                username="user",
                password="secret",
                database="db",
                allow_insecure_http=True,
            )
            client = export_graph.ClickHouseHttpClient(config, 1, 5, "test")
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "edges.csv"
                with self.assertRaises(export_graph.ExportError):
                    client.export_csv(
                        "SELECT 1",
                        destination,
                        query_name="partial",
                        max_result_rows=10,
                    )
                self.assertFalse(destination.exists())
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_successful_exception_tag_header_is_not_an_error(self) -> None:
        class SuccessHandler(QuietHandler):
            def do_POST(self) -> None:
                self.read_request_body()
                body = b"1\n"
                self.send_response(200)
                self.send_header("X-ClickHouse-Exception-Tag", "abcdefghijklmnop")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server, thread = start_server(SuccessHandler)
        try:
            config = export_graph.ConnectionConfig(
                endpoint=f"http://127.0.0.1:{server.server_port}",
                username="user",
                password="secret",
                database="db",
                allow_insecure_http=True,
            )
            client = export_graph.ClickHouseHttpClient(config, 1, 5, "test")
            self.assertEqual(client.text("SELECT 1", query_name="tag"), "1")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


class OutputWorkspaceTests(unittest.TestCase):
    def test_failed_force_run_preserves_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            final = Path(temporary) / "result"
            final.mkdir()
            (final / "edges.csv").write_text("old", encoding="utf-8")
            with export_graph.OutputWorkspace(final, force=True, run_id="run1") as work:
                work.paths["edges"].write_text("new", encoding="utf-8")
            self.assertEqual((final / "edges.csv").read_text(encoding="utf-8"), "old")

    def test_publish_replaces_complete_known_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            final = Path(temporary) / "result"
            final.mkdir()
            (final / "edges.csv").write_text("old", encoding="utf-8")
            with export_graph.OutputWorkspace(final, force=True, run_id="run2") as work:
                for relative in export_graph.FINAL_RELATIVE_FILES:
                    path = work.stage_dir / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"new:{relative}", encoding="utf-8")
                work.publish()
            self.assertTrue((final / "manifest.json").is_file())
            self.assertTrue((final / "edges.csv").read_text(encoding="utf-8").startswith("new:"))
            self.assertFalse((Path(temporary) / ".result.lock").exists())

    def test_force_refuses_unexpected_user_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            final = Path(temporary) / "result"
            final.mkdir()
            (final / "user-notes.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(export_graph.ExportError):
                with export_graph.OutputWorkspace(final, force=True, run_id="run3"):
                    pass
            self.assertTrue((final / "user-notes.txt").exists())


class QaTests(unittest.TestCase):
    def test_nan_edge_and_noncanonical_edge_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "edges.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=export_graph.EXPECTED_EDGE_COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {
                        "source": "GitHub:2",
                        "target": "GitHub:1",
                        "weight": "nan",
                        "shared_contributors": "2",
                        "collaboration_strength": "1",
                    }
                )
            with self.assertRaises(export_graph.ExportError):
                export_graph.inspect_edges(path, platform="GitHub", min_shared=2)

    def test_inconsistent_node_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nodes.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=export_graph.EXPECTED_NODE_COLUMNS)
                writer.writeheader()
                row = {field: "" for field in export_graph.EXPECTED_NODE_COLUMNS}
                row.update(
                    {
                        "id": "GitHub:999",
                        "name": "owner/repo",
                        "platform": "Wrong",
                        "repo_id": "1",
                        "openrank_sum": "1",
                        "openrank_avg": "1",
                        "openrank_max": "1",
                        "months_present": "12",
                        "contributors": "2",
                        "normalized_contribution": "1",
                        "primary_language": "Python",
                        "metadata_status": "normal",
                    }
                )
                writer.writerow(row)
            with self.assertRaises(export_graph.ExportError):
                export_graph.inspect_nodes(
                    path,
                    {1},
                    platform="GitHub",
                    months=12,
                    label_count=150,
                )


class GraphMlTests(unittest.TestCase):
    def test_graphml_contains_csv_nodes_edges_and_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes_path = root / "nodes.csv"
            edges_path = root / "edges.csv"
            graphml_path = root / "graph.graphml"
            with nodes_path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=export_graph.EXPECTED_NODE_COLUMNS)
                writer.writeheader()
                base = {field: "" for field in export_graph.EXPECTED_NODE_COLUMNS}
                for repo_id, name in ((1, "owner/a"), (2, "owner/b")):
                    row = dict(base)
                    row.update(
                        {
                            "id": f"GitHub:{repo_id}",
                            "name": name,
                            "display_label": name,
                            "platform": "GitHub",
                            "repo_id": str(repo_id),
                            "openrank_sum": "1.5",
                            "openrank_avg": "1.5",
                            "openrank_max": "1.5",
                            "months_present": "12",
                            "contributors": "2",
                            "normalized_contribution": "1.5",
                            "primary_language": "Python",
                            "metadata_status": "normal",
                        }
                    )
                    writer.writerow(row)
            with edges_path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=export_graph.EXPECTED_EDGE_COLUMNS)
                writer.writeheader()
                writer.writerow(
                    {
                        "source": "GitHub:1",
                        "target": "GitHub:2",
                        "weight": "1.0",
                        "shared_contributors": "2",
                        "collaboration_strength": "0.5",
                    }
                )

            stats = export_graph.build_graphml(nodes_path, edges_path, graphml_path)
            tree = ET.parse(graphml_path)
            namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
            self.assertEqual(stats["nodes"], 2)
            self.assertEqual(stats["edges"], 1)
            self.assertEqual(stats["missing_edge_endpoints"], 0)
            self.assertEqual(len(tree.findall(".//g:node", namespace)), 2)
            self.assertEqual(len(tree.findall(".//g:edge", namespace)), 1)


if __name__ == "__main__":
    unittest.main()
