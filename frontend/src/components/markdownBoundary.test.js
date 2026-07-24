import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";


const srcDir = dirname(dirname(fileURLToPath(import.meta.url)));


function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(js|vue)$/.test(entry.name) && !entry.name.endsWith(".test.js")
      ? [path]
      : [];
  });
}


function matchingFiles(pattern) {
  return sourceFiles(srcDir)
    .filter((path) => pattern.test(readFileSync(path, "utf8")))
    .map((path) => path.slice(srcDir.length + 1).replaceAll("\\", "/"));
}


test("keeps a single v-html sink in ChatWindow", () => {
  assert.deepEqual(matchingFiles(/\bv-html\s*=/), ["components/ChatWindow.vue"]);
});


test("keeps marked.parse inside the sanitized Markdown boundary", () => {
  assert.deepEqual(matchingFiles(/\bmarked\.parse\s*\(/), ["components/markdown.js"]);
});
