export const PAUSED_STATUS = "This operation is waiting for confirmation.";
export const FAILED_STATUS = "The operation failed.";
export const RESOLVING_STATUS = "Submitting action resolution…";


const TERMINAL_STATUS = {
  paused: PAUSED_STATUS,
  failed: FAILED_STATUS,
};


function requireTrimmed(value, fieldName) {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) throw new Error(`${fieldName} is required`);
  return trimmed;
}

function requireNonBlank(value, fieldName) {
  if (!value?.trim()) throw new Error(`${fieldName} is required`);
  return value;
}



export function attachPendingAction(message, confirmation) {
  message.pendingAction = {
    threadId: confirmation.thread_id,
    actionId: confirmation.action_id,
    tool: confirmation.tool,
    title: confirmation.title,
    content: confirmation.content,
    mode: "approve",
    editTitle: confirmation.title,
    editContent: confirmation.content,
    rejectReason: "",
    resolving: false,
    error: "",
  };
}


export function buildActionResolution(pendingAction) {
  const action_id = pendingAction.actionId;
  if (pendingAction.mode === "edit") {
    return {
      action_id,
      decision: "edit",
      title: requireTrimmed(pendingAction.editTitle, "title"),
      content: requireNonBlank(pendingAction.editContent, "content"),
    };
  }
  if (pendingAction.mode === "reject") {
    const reason = pendingAction.rejectReason?.trim() ?? "";
    return reason
      ? { action_id, decision: "reject", reason }
      : { action_id, decision: "reject" };
  }
  return { action_id, decision: "approve" };
}


export function beginResolveAction(message) {
  if (!message.pendingAction || message.pendingAction.resolving) return false;
  message.pendingAction.resolving = true;
  message.pendingAction.error = "";
  message.statusText = RESOLVING_STATUS;
  return true;
}


export function failResolveAction(message, errorMessage = FAILED_STATUS) {
  message.streaming = false;
  message.statusText = FAILED_STATUS;
  if (message.pendingAction) {
    message.pendingAction.resolving = false;
    message.pendingAction.error = errorMessage;
  }
}


export function finishResolvedAction(message) {
  message.streaming = false;
  message.statusText = "";
  message.pendingAction = null;
}


export function settleTerminalMessage(message, outcome) {
  message.streaming = false;
  message.statusText = TERMINAL_STATUS[outcome];
}
