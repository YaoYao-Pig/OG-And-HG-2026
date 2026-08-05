import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const isGitHubPages = process.env.GITHUB_PAGES === "true";

function resolveSiteUrl() {
  const configuredUrl = process.env.NEXT_PUBLIC_SITE_URL?.trim();

  try {
    const siteUrl = new URL(
      configuredUrl ||
        "https://open-galaxy-collaboration-atlas.s20020515.chatgpt.site/",
    );
    siteUrl.pathname = `${siteUrl.pathname.replace(/\/+$/, "")}/`;
    siteUrl.search = "";
    siteUrl.hash = "";
    return siteUrl;
  } catch {
    return new URL(
      "https://open-galaxy-collaboration-atlas.s20020515.chatgpt.site/",
    );
  }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function render() {
  if (isGitHubPages) {
    const html = await readFile(
      new URL("../dist/client/index.html", import.meta.url),
      "utf8",
    );

    return {
      status: 200,
      contentType: "text/html",
      html,
    };
  }

  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  return {
    status: response.status,
    contentType: response.headers.get("content-type") ?? "",
    html: await response.text(),
  };
}

test("server-renders the dual-source Galaxy atlas", async () => {
  const rendered = await render();
  assert.equal(rendered.status, 200);
  assert.match(rendered.contentType, /^text\/html\b/i);

  const { html } = rendered;
  assert.match(html, /<html[^>]*\blang=["']zh-CN["']/i);
  assert.match(
    html,
    /<title>OpenGalaxy × ModelGalaxy · 开源与 AI 生态星图<\/title>/i,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']description["'])(?=[^>]*\bcontent=["'][^"']*GitHub[^"']*Hugging Face[^"']*19,771[^"']*106,650[^"']*24,000[^"']*["'])[^>]*>/i,
  );
  assert.match(html, /OPEN GALAXY/);
  assert.match(html, /开源与 AI 生态星图/);
  assert.match(html, /02 HUGGING FACE/);
  assert.match(html, /19,771/);
  assert.match(html, /106,650/);
  assert.match(
    html,
    /<button(?=[^>]*\brole=["']switch["'])(?=[^>]*\baria-checked=["']true["'])(?=[^>]*\baria-label=["']辉光效果["'])[^>]*>/i,
  );
  assert.match(html, /Glow\s*<span[^>]*>On<\/span>/i);
  assert.match(
    html,
    /关系表示共享已知非 Bot 贡献者形成的协作亲和，不代表代码依赖或组织归属。/,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']theme-color["'])(?=[^>]*\bcontent=["']#05070d["'])[^>]*>/i,
  );
  const expectedImageUrl = new URL("og.png", resolveSiteUrl()).toString();
  assert.match(
    html,
    new RegExp(
      `<meta(?=[^>]*\\bproperty=["']og:image["'])(?=[^>]*\\bcontent=["']${escapeRegExp(expectedImageUrl)}["'])[^>]*>`,
      "i",
    ),
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']twitter:card["'])(?=[^>]*\bcontent=["']summary_large_image["'])[^>]*>/i,
  );
  assert.match(
    html,
    new RegExp(
      `<meta(?=[^>]*\\bname=["']twitter:image["'])(?=[^>]*\\bcontent=["']${escapeRegExp(expectedImageUrl)}["'])[^>]*>`,
      "i",
    ),
  );
  assert.doesNotMatch(
    html,
    /codex-preview|loading skeleton|react-loading-skeleton/i,
  );
});

test("GitHub Pages mode emits a static index", async (context) => {
  if (!isGitHubPages) {
    context.skip("only applies to GITHUB_PAGES=true builds");
    return;
  }

  const html = await readFile(
    new URL("../dist/client/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /<html[^>]*\blang=["']zh-CN["']/i);
  assert.match(html, /OpenGalaxy × ModelGalaxy/);
});
