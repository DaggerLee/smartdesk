export const PAUSED_STATUS = "This operation is waiting for confirmation.";
export const FAILED_STATUS = "The operation failed.";


const TERMINAL_STATUS = {
  paused: PAUSED_STATUS,
  failed: FAILED_STATUS,
};


export function settleTerminalMessage(message, outcome) {
  message.streaming = false;
  message.statusText = TERMINAL_STATUS[outcome];
}
