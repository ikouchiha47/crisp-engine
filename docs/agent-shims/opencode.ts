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
 * Hook coverage:
 *   experimental.chat.system.transform  → inject instincts into system prompt
 *   tool.execute.before                 → crisp-hook opencode-pre-tool
 *   tool.execute.after                  → crisp-hook opencode-post-tool
 *   experimental.session.compacting     → crisp-hook opencode-pre-compact
 *
 * Limitations vs Claude Code:
 *   - tool.execute.before only receives tool name + sessionID, not args.
 *     Per-file episodic memory injection is done post-tool instead.
 *   - system.transform fires per-message, so we cache the instinct block.
 */

import type { Plugin } from "@opencode-ai/plugin"
import { execSync, spawnSync } from "node:child_process"

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

/** Call crisp-hook and return stdout as parsed JSON, or null on failure. */
function query(event: string, payload: object): any | null {
  try {
    const json = JSON.stringify(payload)
    const result = spawnSync("crisp-hook", [event], {
      input: json,
      timeout: 5000,
      encoding: "utf8",
    })
    if (result.status === 0 && result.stdout) {
      return JSON.parse(result.stdout)
    }
  } catch {}
  return null
}

const plugin: Plugin = async (input) => {
  const cwd = input.config?.cwd ?? process.cwd()

  // Cache instinct block per session to avoid re-querying on every message.
  // Invalidated when a new session_id appears.
  let cachedSessionId = ""
  let cachedInstincts = ""

  function getInstincts(sessionID: string): string {
    if (sessionID === cachedSessionId) return cachedInstincts
    const result = query("opencode-get-instincts", { session_id: sessionID, cwd })
    cachedInstincts = (result?.instinct_block as string) ?? ""
    cachedSessionId = sessionID
    return cachedInstincts
  }

  return {
    /**
     * Inject instincts into the system prompt before every LLM call.
     * output.system is string[] — we push our block if non-empty.
     */
    "experimental.chat.system.transform": async (hookInput, output) => {
      // hookInput.sessionID is available in newer SDK versions; fall back gracefully.
      const sessionID = (hookInput as any).sessionID ?? cachedSessionId
      const block = getInstincts(sessionID)
      if (block) {
        output.system.push(block)
      }
    },

    "tool.execute.before": async (hookInput, _output) => {
      send("opencode-pre-tool", {
        session_id: hookInput.sessionID,
        cwd,
        tool_name: hookInput.tool,
        tool_input: {},  // args not available in before hook
      })
    },

    "tool.execute.after": async (hookInput, hookOutput) => {
      send("opencode-post-tool", {
        session_id: hookInput.sessionID,
        cwd,
        tool_name: hookInput.tool,
        tool_input: hookInput.args ?? {},
        output: hookOutput.output ?? "",
        metadata: hookOutput.metadata ?? {},
      })
    },

    "experimental.session.compacting": async (hookInput, _output) => {
      send("opencode-pre-compact", {
        session_id: hookInput.sessionID ?? "unknown",
        cwd,
      })
    },
  }
}

export default { server: plugin }
