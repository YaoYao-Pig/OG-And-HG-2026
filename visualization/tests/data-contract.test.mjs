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
  assert.ok(graph.meta.communities.length >= 12);
  assert.ok(graph.meta.communities.length <= 18);
  assert.equal(graph.meta.layout.mode, "clustered-island-disc");
  assert.equal(graph.meta.layout.realNodeCount, graph.nodes.length);
  assert.equal(graph.meta.layout.syntheticNodeCount, 0);
  assert.equal(
    graph.meta.communities.reduce((sum, community) => sum + community.count, 0),
    graph.nodes.length,
  );
  const communityIds = new Set(graph.meta.communities.map(({ id }) => id));
  assert.ok(graph.nodes.every((node) => communityIds.has(node.c)));
  for (const edge of graph.edges) {
    assert.ok(Number.isInteger(edge.s) && edge.s >= 0 && edge.s < graph.nodes.length);
    assert.ok(Number.isInteger(edge.t) && edge.t >= 0 && edge.t < graph.nodes.length);
    assert.ok(Number.isFinite(edge.strength) && edge.strength >= 0);
  }
}

test("GitHub graph exposes complete language, AI, and domain facets", async () => {
  const graph = await readGraph("graph.json");
  assertGraphIntegrity(graph);
  assert.equal(graph.nodes.length, 19_771);
  assert.equal(graph.edges.length, 106_650);
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
  assert.equal(aiFacetCount, aiNodes.length);
  assert.ok(aiNodes.length >= 1_500);
  assert.ok(
    graph.nodes.filter((node) => node.areas.domain.source === "curated").length >=
      2_500,
  );
});

test("Hugging Face graph preserves source provenance and edge semantics", async () => {
  const graph = await readGraph("huggingface.json");
  assertGraphIntegrity(graph);
  assert.equal(graph.meta.source, "huggingface");
  assert.equal(graph.meta.sourceLicense, "apache-2.0");
  assert.match(graph.meta.sourceRevision, /^[a-f0-9]{40}$/);
  assert.equal(graph.meta.provenance.method, "dataset-viewer-row-sample");
  assert.equal(graph.nodes.length, 24_000);
  assert.ok(graph.edges.length >= graph.nodes.length - 1);
  assert.ok(graph.edges.length <= graph.nodes.length * 3);
  assert.equal(graph.meta.componentCount, 1);
  assert.equal(new Set(graph.nodes.map(({ id }) => id)).size, graph.nodes.length);
  assert.deepEqual(graph.meta.entityCounts, {
    dataset: 7_000,
    model: 12_000,
    space: 5_000,
  });
  assert.deepEqual(graph.meta.facetOrder, ["type", "task", "library"]);
  assert.deepEqual(graph.meta.serialization, {
    landmarkLabels: 512,
    topicLimit: 3,
    descriptionLimit: 96,
    omittedNodeFields: ["author", "lang", "updatedAt"],
    omittedEdgeFields: ["w", "shared"],
    compactAreaFields: ["primary", "tags"],
  });
  for (const config of ["models", "datasets", "spaces"]) {
    assert.equal(graph.meta.sampling[config].config, config);
    assert.equal(graph.meta.sampling[config].split, "train");
    assert.equal(graph.meta.sampling[config].offset, 0);
    assert.ok(graph.meta.sampling[config].pageSize <= 100);
  }

  assert.ok(graph.nodes.every((node) => node.name.includes("/")));
  assert.equal(graph.nodes.filter((node) => node.label).length, 512);
  assert.ok(
    graph.nodes.every(
      (node) => !("author" in node) && !("lang" in node) && !("updatedAt" in node),
    ),
  );
  assert.ok(
    graph.nodes.every(
      (node) =>
        node.areas?.type?.primary &&
        node.areas?.task?.primary &&
        node.areas?.library?.primary,
    ),
  );
  assert.ok(graph.edges.every((edge) => edge.inferred === 0 || edge.inferred === 1));
  assert.ok(graph.edges.every((edge) => !("w" in edge) && !("shared" in edge)));
  assert.equal(
    graph.meta.edgeSemantics.observed + graph.meta.edgeSemantics.inferred,
    graph.edges.length,
  );
  assert.ok(graph.meta.edgeSemantics.observed > 0);
  assert.ok(graph.meta.edgeSemantics.inferred > 0);
});
