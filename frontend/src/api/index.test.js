import assert from "node:assert/strict";
import test from "node:test";

import { resolveActionStream, sendMessageStream } from "./index.js";


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


function responseWithReadError(framesBeforeError) {
  const chunks = framesBeforeError.map((frame) => new TextEncoder().encode(frame));
  return {
    ok: true,
    body: {
      getReader() {
        let index = 0;
        return {
          async read() {
            if (index < chunks.length) return { done: false, value: chunks[index++] };
            throw new Error("stream interrupted");
          },
        };
      },
    },
  };
}


async function runStream(frames) {
  const observed = {
    chunks: [],
    confirmations: [],
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
    (confirmation) => observed.confirmations.push(confirmation),
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
    confirmations: [],
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
    confirmations: [],
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
    confirmations: [],
    done: 1,
    paused: 0,
    failed: 0,
  });
});


test("confirmation_required payload is captured before PAUSED settles", async () => {
  const observed = await runStream([
    'data: {"confirmation_required":{"thread_id":"thread-1","action_id":"action-1","tool":"write_note","title":"Title","content":"Body"}}\n\n',
    "data: [PAUSED]\n\n",
  ]);

  assert.deepEqual(observed, {
    chunks: [],
    confirmations: [
      {
        thread_id: "thread-1",
        action_id: "action-1",
        tool: "write_note",
        title: "Title",
        content: "Body",
      },
    ],
    done: 0,
    paused: 1,
    failed: 0,
  });
});


test("resolveActionStream posts a strict resolution and streams canonical answer", async () => {
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return responseWithFrames([
      'data: {"action_result":{"action_id":"action-1","result":"succeeded"}}\n\n',
      'data: "The note was saved."\n\n',
      "data: [DONE]\n\n",
    ]);
  };
  const observed = { chunks: [], done: 0, failed: 0 };

  await resolveActionStream(
    "thread-1",
    { action_id: "action-1", decision: "approve" },
    {
      onChunk: (chunk) => observed.chunks.push(chunk),
      onDone: () => observed.done++,
      onFailed: () => observed.failed++,
    },
  );

  assert.equal(request.url, "/api/chat/actions/thread-1/resolve");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), {
    action_id: "action-1",
    decision: "approve",
  });
  assert.deepEqual(observed, {
    chunks: ["The note was saved."],
    done: 1,
    failed: 0,
  });
});


test("resolveActionStream treats empty EOF as failed", async () => {
  globalThis.fetch = async () => responseWithFrames([]);
  const observed = { chunks: [], done: 0, failed: 0 };

  await resolveActionStream(
    "thread-1",
    { action_id: "action-1", decision: "approve" },
    {
      onChunk: (chunk) => observed.chunks.push(chunk),
      onDone: () => observed.done++,
      onFailed: () => observed.failed++,
    },
  );

  assert.deepEqual(observed, {
    chunks: [],
    done: 0,
    failed: 1,
  });
});


test("resolveActionStream treats EOF without DONE as failed", async () => {
  globalThis.fetch = async () => responseWithFrames([
    'data: "partial receipt answer"\n\n',
  ]);
  const observed = { chunks: [], done: 0, failed: 0 };

  await resolveActionStream(
    "thread-1",
    { action_id: "action-1", decision: "approve" },
    {
      onChunk: (chunk) => observed.chunks.push(chunk),
      onDone: () => observed.done++,
      onFailed: () => observed.failed++,
    },
  );

  assert.deepEqual(observed, {
    chunks: [],
    done: 0,
    failed: 1,
  });
});


test("resolveActionStream discards buffered answer when read fails before DONE", async () => {
  globalThis.fetch = async () => responseWithReadError([
    'data: "The note was saved."\n\n',
  ]);
  const observed = { chunks: [], done: 0, failed: 0 };

  await resolveActionStream(
    "thread-1",
    { action_id: "action-1", decision: "approve" },
    {
      onChunk: (chunk) => observed.chunks.push(chunk),
      onDone: () => observed.done++,
      onFailed: () => observed.failed++,
    },
  );

  assert.deepEqual(observed, {
    chunks: [],
    done: 0,
    failed: 1,
  });
});
