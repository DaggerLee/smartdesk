import assert from "node:assert/strict";
import test from "node:test";

import { sendMessageStream } from "./index.js";


globalThis.localStorage = {
  getItem() {
    return "test-token";
  },
};


function responseWithFrames(frames) {
  const chunks = frames.map((frame) => new TextEncoder().encode(frame));
  return {
    ok: true,
    body: {
      getReader() {
        let index = 0;
        return {
          async read() {
            if (index === chunks.length) return { done: true, value: undefined };
            return { done: false, value: chunks[index++] };
          },
        };
      },
    },
  };
}


async function runStream(frames) {
  const observed = {
    chunks: [],
    done: 0,
    paused: 0,
    failed: 0,
  };
  globalThis.fetch = async () => responseWithFrames(frames);

  await sendMessageStream(
    1,
    "question",
    (chunk) => observed.chunks.push(chunk),
    undefined,
    () => observed.done++,
    undefined,
    () => observed.paused++,
    () => observed.failed++,
  );
  return observed;
}


test("PAUSED is a distinct terminal outcome and settles once", async () => {
  const observed = await runStream([
    'data: "partial"\n\n',
    "data: [PAUSED]\n\n",
  ]);

  assert.deepEqual(observed, {
    chunks: ["partial"],
    done: 0,
    paused: 1,
    failed: 0,
  });
});


test("FAILED is a distinct terminal outcome and is never a text chunk", async () => {
  const observed = await runStream([
    "data: [FAILED]\n\n",
  ]);

  assert.deepEqual(observed, {
    chunks: [],
    done: 0,
    paused: 0,
    failed: 1,
  });
});


test("a naturally ended stream retains the existing onDone behavior", async () => {
  const observed = await runStream([
    'data: "answer"\n\n',
  ]);

  assert.deepEqual(observed, {
    chunks: ["answer"],
    done: 1,
    paused: 0,
    failed: 0,
  });
});
