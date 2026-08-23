/**
 * Crisp Engine bridge for OpenCode.
 *
 * This is an OpenCode plugin (NOT a shell hook).
 *
 * Installation:
 *   1. Copy this file to ~/.config/opencode/crisp-memory/index.ts
 *   2. Add "crisp-memory" to the "plugin" array in ~/.config/opencode/opencode.jsonc
 *   3. Ensure crisp-hook is on PATH: pip install crisp-engine
 *
 * Limitations vs Claude Code:
 *   - No session_start / session_end hooks in OpenCode plugin API
 *   - tool.execute.after output is a single string (no stdout/stderr split)
 *   - session.compacting is experimental and may not fire in all cases
 *
 * Hook coverage:
 *   tool.execute.after     → crisp-hook opencode-post-tool   (PostToolUse equivalent)
 *   tool.execute.before    → crisp-hook opencode-pre-tool    (PreToolUse equivalent)
 *   experimental.session.compacting → crisp-hook opencode-pre-compact
 */

import type { Plugin } from "@opencode-ai/plugin"
import { execSync } from "node:child_process"

function send(event: string, payload: object): void {
  try {
    const json = JSON.stringify(payload)
    execSync(`crisp-hook ${event}`, {
      input: json,
      timeout: 10000,
      stdio: ["pipe", "ignore", "ignore"],
    })
  } catch {
    // never break a session
  }
}

const plugin: Plugin = async (input) => {
  const cwd = input.config.cwd ?? process.cwd()

  return {
    "tool.execute.before": async (hookInput, _output) => {
      send("opencode-pre-tool", {
        session_id: hookInput.sessionID,
        cwd,
        tool_name: hookInput.tool,
        tool_input: {},  // args not yet available in before hook
      })
    },

    "tool.execute.after": async (hookInput, hookOutput) => {
      send("opencode-post-tool", {
        session_id: hookInput.sessionID,
        cwd,
        tool_name: hookInput.tool,
        tool_input: hookInput.args ?? {},
        // OpenCode gives a single output string, not split stdout/stderr.
        // The adapter maps this to { stdout: output, exit_code: 0 }.
        output: hookOutput.output ?? "",
        metadata: hookOutput.metadata ?? {},
      })
    },

    "experimental.session.compacting": async (hookInput, _output) => {
      send("opencode-pre-compact", {
        session_id: hookInput.sessionID ?? "unknown",
        cwd,
        reason: hookInput.reason,
      })
    },
  }
}

export default { server: plugin }
