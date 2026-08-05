import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
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
}

test("server-renders the Open Galaxy collaboration atlas", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html[^>]*\blang=["']zh-CN["']/i);
  assert.match(html, /<title>OpenGalaxy 2025—26 · 开源协作星图<\/title>/i);
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']description["'])(?=[^>]*\bcontent=["'][^"']*2025-08[^"']*2026-07[^"']*4,243[^"']*46,173[^"']*["'])[^>]*>/i,
  );
  assert.match(html, /OPEN GALAXY/);
  assert.match(html, /开源协作星图/);
  assert.match(html, /4,243/);
  assert.match(html, /46,173/);
  assert.match(
    html,
    /关系表示共享已知非 Bot 贡献者形成的协作亲和，不代表代码依赖或组织归属。/,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']theme-color["'])(?=[^>]*\bcontent=["']#05070d["'])[^>]*>/i,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bproperty=["']og:image["'])(?=[^>]*\bcontent=["']http:\/\/localhost\/og\.png["'])[^>]*>/i,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']twitter:card["'])(?=[^>]*\bcontent=["']summary_large_image["'])[^>]*>/i,
  );
  assert.match(
    html,
    /<meta(?=[^>]*\bname=["']twitter:image["'])(?=[^>]*\bcontent=["']http:\/\/localhost\/og\.png["'])[^>]*>/i,
  );
  assert.doesNotMatch(
    html,
    /codex-preview|loading skeleton|react-loading-skeleton/i,
  );
});
