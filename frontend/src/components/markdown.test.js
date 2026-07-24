import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";


const { window } = new JSDOM("");
globalThis.window = window;

const { renderMarkdown } = await import("./markdown.js");


test("renders ordinary Markdown", () => {
  const html = renderMarkdown("**Safe** [link](https://example.com)");

  assert.match(html, /<strong>Safe<\/strong>/);
  assert.match(html, /href="https:\/\/example\.com"/);
});


test("removes executable HTML from Markdown answers", () => {
  const html = renderMarkdown(
    '<img src="missing" onerror="globalThis.xss = true">'
      + '<script>globalThis.xss = true</script>',
  );

  assert.match(html, /<img src="missing">/);
  assert.doesNotMatch(html, /onerror/i);
  assert.doesNotMatch(html, /<script/i);
});


test("removes unsafe URL protocols", () => {
  const html = renderMarkdown(
    '[click](javascript:alert(1)) <a href="data:text/html,boom">data</a>',
  );

  assert.doesNotMatch(html, /javascript:/i);
  assert.doesNotMatch(html, /data:text\/html/i);
});
