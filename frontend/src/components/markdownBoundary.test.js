import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";


const srcDir = dirname(dirname(fileURLToPath(import.meta.url)));
const chatWindowSource = readFileSync(
  join(srcDir, "components", "ChatWindow.vue"),
  "utf8",
);


function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(js|vue)$/.test(entry.name) && !entry.name.endsWith(".test.js")
      ? [path]
      : [];
  });
}


function matchingOccurrences(pattern) {
  return sourceFiles(srcDir).flatMap((path) => {
    const relativePath = path.slice(srcDir.length + 1).replaceAll("\\", "/");
    const matches = readFileSync(path, "utf8").match(
      new RegExp(pattern.source, `${pattern.flags}g`),
    );
    return Array(matches?.length ?? 0).fill(relativePath);
  });
}


test("keeps a single v-html sink in ChatWindow", () => {
  assert.deepEqual(matchingOccurrences(/\bv-html\s*=/), [
    "components/ChatWindow.vue",
  ]);
  assert.match(chatWindowSource, /\bv-html="renderMessageContent\(msg\)"/);
});


test("keeps marked.parse inside the sanitized Markdown boundary", () => {
  assert.deepEqual(matchingOccurrences(/\bmarked\.parse\s*\(/), [
    "components/markdown.js",
  ]);
  assert.match(
    chatWindowSource,
    /function renderMessageContent\(msg\)\s*{\s*const html = renderMarkdown\(msg\.answer\);/,
  );
});
