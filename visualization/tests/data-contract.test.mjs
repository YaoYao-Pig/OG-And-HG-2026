import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function readGraph(name) {
  const source = await readFile(
    new URL(`../public/data/${name}`, import.meta.url),
    "utf8",
  );
  return JSON.parse(source);
}

function assertGraphIntegrity(graph) {
  assert.equal(graph.meta.nodeCount, graph.nodes.length);
  assert.equal(graph.meta.edgeCount, graph.edges.length);
  for (const edge of graph.edges) {
    assert.ok(Number.isInteger(edge.s) && edge.s >= 0 && edge.s < graph.nodes.length);
    assert.ok(Number.isInteger(edge.t) && edge.t >= 0 && edge.t < graph.nodes.length);
    assert.ok(Number.isFinite(edge.strength) && edge.strength >= 0);
  }
}

test("GitHub graph exposes complete language, AI, and domain facets", async () => {
  const graph = await readGraph("graph.json");
  assertGraphIntegrity(graph);
  assert.equal(graph.nodes.length, 4_243);
  assert.equal(graph.edges.length, 46_173);
  assert.deepEqual(graph.meta.facetOrder, ["language", "ai", "domain"]);

  const expectedDomains = new Set([
    "Development Tools",
    "Application Software",
    "AI",
    "Cloud Native",
    "Agentic AI",
    "Web Frameworks",
    "Operating System",
    "Database",
    "Blockchain",
    "Big Data",
    "Programming Language",
    "IoT",
  ]);
  assert.deepEqual(
    new Set(graph.meta.facets.domain.map(({ name }) => name)),
    expectedDomains,
  );

  for (const facet of ["language", "ai", "domain"]) {
    assert.equal(
      graph.meta.facets[facet].reduce((sum, item) => sum + item.count, 0),
      graph.nodes.length,
    );
    assert.ok(graph.nodes.every((node) => node.areas?.[facet]?.primary));
  }

  const aiNodes = graph.nodes.filter((node) => node.areas.ai.isAi);
  const aiFacetCount = graph.meta.facets.ai
    .filter(({ name }) => name !== "Non-AI")
    .reduce((sum, item) => sum + item.count, 0);
  assert.equal(aiNodes.length, 616);
  assert.equal(aiFacetCount, aiNodes.length);
  assert.ok(
    graph.nodes.filter((node) => node.areas.domain.source === "curated").length >=
      1_300,
  );
});

test("Hugging Face graph preserves source provenance and edge semantics", async () => {
  const graph = await readGraph("huggingface.json");
  assertGraphIntegrity(graph);
  assert.equal(graph.meta.source, "huggingface");
  assert.equal(graph.meta.sourceLicense, "apache-2.0");
  assert.match(graph.meta.sourceRevision, /^[a-f0-9]{40}$/);
  assert.equal(graph.meta.provenance.method, "dataset-viewer-row-sample");
  assert.equal(graph.nodes.length, 2_400);
  assert.ok(graph.edges.length >= graph.nodes.length - 1);
  assert.ok(graph.edges.length < 20_000);
  assert.deepEqual(graph.meta.entityCounts, {
    dataset: 700,
    model: 1_200,
    space: 500,
  });
  assert.deepEqual(graph.meta.facetOrder, ["type", "task", "library"]);
  for (const config of ["models", "datasets", "spaces"]) {
    assert.equal(graph.meta.sampling[config].config, config);
    assert.equal(graph.meta.sampling[config].split, "train");
    assert.equal(graph.meta.sampling[config].offset, 0);
    assert.ok(graph.meta.sampling[config].pageSize <= 100);
  }

  assert.ok(graph.nodes.every((node) => node.name.includes("/")));
  assert.ok(
    graph.nodes.every(
      (node) =>
        node.areas?.type?.primary &&
        node.areas?.task?.primary &&
        node.areas?.library?.primary,
    ),
  );
  assert.ok(graph.edges.every((edge) => edge.inferred === 0 || edge.inferred === 1));
  assert.equal(
    graph.meta.edgeSemantics.observed + graph.meta.edgeSemantics.inferred,
    graph.edges.length,
  );
  assert.ok(graph.meta.edgeSemantics.observed > 0);
  assert.ok(graph.meta.edgeSemantics.inferred > 0);
});
