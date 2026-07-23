import assert from "node:assert/strict";
import test from "node:test";

import {
  FAILED_STATUS,
  PAUSED_STATUS,
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
