# OpenGalaxy Web Atlas

Interactive scientific-plate visualization for two public ecosystems:

- **GitHub Collaboration** — 19,771 repositories and 106,650 shared-contributor relations for 2025-08 through 2026-07.
- **Hugging Face Hub** — a daily high-signal model, dataset, and Space graph derived from [`cfahlgren1/hub-stats`](https://huggingface.co/datasets/cfahlgren1/hub-stats).

The browser only receives compact, precomputed graph JSON. It never connects to ClickHouse or downloads the multi-gigabyte Hugging Face source snapshot. The GitHub layout groups topology communities into irregular macro clusters, while every visible node remains a real exported repository.

## Local development

Requirements: Node.js 22.13+ and Python 3.10+.

```bash
npm ci
npm run dev
npm run lint
npm test
```

## Rebuild graph data

```bash
python scripts/build_graph_data.py
python scripts/build_hf_graph.py
```

`build_hf_graph.py` records the source dataset revision and reads bounded, contiguous pages directly from the official Dataset Viewer `/rows` API for the `models`, `datasets`, and `spaces` configs. The output explicitly marks inferred semantic edges separately from observed lineage, declared-dataset, publisher, and Space-use relations.

GitHub Area enrichment is optional and read-only. Provide credentials only as environment variables, export the curated taxonomy, then rebuild:

```bash
python scripts/fetch_clickhouse_areas.py
python scripts/build_graph_data.py
```

The exporter forces `readonly=1` and never writes credentials into an artifact. Use a least-privilege account over HTTPS; do not put database credentials in source files or workflow YAML.

## GitHub Pages deployment

The workflow at `.github/workflows/pages.yml` builds and deploys the static atlas whenever `main` changes, on manual dispatch, and once per day to refresh the Hugging Face snapshot.

One repository setting is required the first time:

1. Open **Settings → Pages**.
2. Under **Build and deployment**, select **GitHub Actions** as the source.
3. Run **Deploy OpenGalaxy to GitHub Pages** or push to `main`.

Pages deployment uses the repository-provided `GITHUB_TOKEN`; no hosting token is required. The Vinext Worker/Sites build remains available when `GITHUB_PAGES` is not set.
