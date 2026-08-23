/**
 * Crisp Engine bridge for Pi (pi.dev).
 *
 * Drop this file in .pi/extensions/ (project) or
 * ~/.config/pi/extensions/ (global) to wire Crisp memory capture
 * into a Pi session.
 *
 * Requires: crisp-hook on PATH (pip install crisp-engine)
 */

import type { Extension } from "@pi/sdk"
import { exec } from "node:child_process"
import { promisify } from "node:util"

const run = promisify(exec)

async function send(event: string, payload: object): Promise<void> {
  const json = JSON.stringify(payload).replace(/'/g, "'\\''")
  await run(`echo '${json}' | crisp-hook ${event}`).catch(() => {})
}

export default {
  name: "crisp-memory",

  events: {
    session_start: async (e, ctx) => {
      await send("pi-session-start", {
        session_id: ctx.sessionId,
        cwd: ctx.cwd,
        reason: e.reason,
      })
    },

    // tool_result fires after execution with input + content blocks
    tool_result: async (e, ctx) => {
      await send("pi-post-tool", {
        session_id: ctx.sessionId,
        cwd: ctx.cwd,
        toolName: e.toolName,
        input: e.input,
        content: e.content,   // ContentBlock[] -- adapter extracts text
        isError: e.isError,
      })
    },

    agent_end: async (e, ctx) => {
      await send("pi-stop", {
        session_id: ctx.sessionId,
        cwd: ctx.cwd,
      })
    },

    session_shutdown: async (e, ctx) => {
      await send("pi-session-end", {
        session_id: ctx.sessionId,
        cwd: ctx.cwd,
        reason: e.reason,
      })
    },

    session_before_compact: async (e, ctx) => {
      await send("pi-pre-compact", {
        session_id: ctx.sessionId,
        cwd: ctx.cwd,
        reason: e.reason,
      })
    },
  },
} satisfies Extension
