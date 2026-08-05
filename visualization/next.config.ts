import type { NextConfig } from "next";

const isGitHubPages = process.env.GITHUB_PAGES === "true";

function normalizeBasePath(value: string | undefined): string {
  const trimmed = value?.trim();
  if (!trimmed || trimmed === "/") return "";

  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

const repositoryName = process.env.GITHUB_REPOSITORY?.split("/").at(-1);
const fallbackPagesBasePath = repositoryName
  ? `/${repositoryName}`
  : "/OG-And-HG-2026";
const pagesBasePath = normalizeBasePath(
  process.env.NEXT_PUBLIC_BASE_PATH ?? fallbackPagesBasePath,
);

const nextConfig: NextConfig = isGitHubPages
  ? {
      output: "export",
      // vinext 1.0.0-beta.2 probes `/` while prerendering. A non-empty
      // basePath makes that probe return 404 and skips index.html entirely.
      // Keep routing at the artifact root; assetPrefix and the public env value
      // still point browser assets and data requests at the Pages project path.
      basePath: "",
      assetPrefix: pagesBasePath,
      trailingSlash: true,
    }
  : {};

export default nextConfig;
