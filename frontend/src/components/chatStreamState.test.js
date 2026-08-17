import assert from "node:assert/strict";
import test from "node:test";

import {
  FAILED_STATUS,
  PAUSED_STATUS,
  RESOLVING_STATUS,
  attachPendingAction,
  beginResolveAction,
  buildActionResolution,
  finishResolvedAction,
  settleTerminalMessage,
} from "./chatStreamState.js";


test("paused stops streaming and displays the waiting-for-confirmation state", () => {
  const message = { streaming: true, statusText: "Searching…" };

  settleTerminalMessage(message, "paused");

  assert.equal(message.streaming, false);
  assert.equal(message.statusText, PAUSED_STATUS);
  assert.match(message.statusText, /waiting for confirmation/i);
});


test("failed stops streaming and displays a distinct failure state", () => {
  const message = { streaming: true, statusText: "Searching…" };

  settleTerminalMessage(message, "failed");

  assert.equal(message.streaming, false);
  assert.equal(message.statusText, FAILED_STATUS);
  assert.notEqual(message.statusText, PAUSED_STATUS);
});


test("pending action stores current-page proposal state", () => {
  const message = {};

  attachPendingAction(message, {
    thread_id: "thread-1",
    action_id: "action-1",
    tool: "write_note",
    title: "Title",
    content: "Body",
  });

  assert.deepEqual(message.pendingAction, {
    threadId: "thread-1",
    actionId: "action-1",
    tool: "write_note",
    title: "Title",
    content: "Body",
    mode: "approve",
    editTitle: "Title",
    editContent: "Body",
    rejectReason: "",
    resolving: false,
    error: "",
  });
});


test("approval resolution is minimal and sends no proposal payload", () => {
  const pendingAction = {
    actionId: "action-1",
    mode: "approve",
    title: "Title",
    content: "Body",
    editTitle: "Edited",
    editContent: "Edited body",
    rejectReason: "No",
  };

  assert.deepEqual(buildActionResolution(pendingAction), {
    action_id: "action-1",
    decision: "approve",
  });
});


test("edit resolution requires complete replacement title and content", () => {
  assert.throws(
    () => buildActionResolution({
      actionId: "action-1",
      mode: "edit",
      editTitle: "",
      editContent: "Body",
    }),
    /title/i,
  );
  assert.throws(
    () => buildActionResolution({
      actionId: "action-1",
      mode: "edit",
      editTitle: "Edited",
      editContent: "   ",
    }),
    /content/i,
  );
  assert.deepEqual(
    buildActionResolution({
      actionId: "action-1",
      mode: "edit",
      editTitle: " Edited ",
      editContent: "  Edited body\n",
    }),
    {
      action_id: "action-1",
      decision: "edit",
      title: "Edited",
      content: "  Edited body\n",
    },
  );
});


test("reject resolution omits a blank reason and trims a provided reason", () => {
  assert.deepEqual(
    buildActionResolution({ actionId: "action-1", mode: "reject", rejectReason: "   " }),
    {
      action_id: "action-1",
      decision: "reject",
    },
  );
  assert.deepEqual(
    buildActionResolution({ actionId: "action-1", mode: "reject", rejectReason: " Changed " }),
    {
      action_id: "action-1",
      decision: "reject",
      reason: "Changed",
    },
  );
});


test("resolve submit is single-flight and terminal success clears controls", () => {
  const message = {
    streaming: false,
    statusText: PAUSED_STATUS,
    pendingAction: {
      resolving: false,
      error: "previous failure",
    },
  };

  assert.equal(beginResolveAction(message), true);
  assert.equal(beginResolveAction(message), false);
  assert.equal(message.pendingAction.resolving, true);
  assert.equal(message.pendingAction.error, "");
  assert.equal(message.statusText, RESOLVING_STATUS);

  finishResolvedAction(message);

  assert.equal(message.streaming, false);
  assert.equal(message.statusText, "");
  assert.equal(message.pendingAction, null);
});
