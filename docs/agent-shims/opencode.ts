/**
 * Crisp Engine bridge for OpenCode.
 *
 * Drop this file in .opencode/plugin/ (project) or
 * ~/.config/opencode/plugin/ (global) to wire Crisp memory capture
 * into an OpenCode session.
 *
 * Requires: crisp-hook on PATH (pip install crisp-engine)
 */

import { definePlugin } from "opencode"
import { $ } from "bun"

function send(event: string, payload: object) {
  const json = JSON.stringify(payload)
  return $`crisp-hook ${event}`.stdin(json).quiet().nothrow()
}

export default definePlugin({
  name: "crisp-memory",
  hooks: {
    "session.created": async (e, ctx) => {
      await send("opencode-session-start", {
        session_id: ctx.session.id,
        cwd: ctx.session.cwd,
        reason: e.reason,
      })
    },

    "tool.execute.after": async (e, ctx) => {
      await send("opencode-post-tool", {
        session_id: ctx.session.id,
        cwd: ctx.session.cwd,
        toolName: e.toolName,
        input: e.input,
        output: {
          stdout: e.output?.stdout ?? "",
          stderr: e.output?.stderr ?? "",
          exitCode: e.output?.exitCode ?? 0,
        },
      })
    },

    "session.idle": async (e, ctx) => {
      await send("opencode-stop", {
        session_id: ctx.session.id,
        cwd: ctx.session.cwd,
      })
    },

    "session.deleted": async (e, ctx) => {
      await send("opencode-session-end", {
        session_id: ctx.session.id,
        cwd: ctx.session.cwd,
      })
    },

    "session.compacted": async (e, ctx) => {
      await send("opencode-pre-compact", {
        session_id: ctx.session.id,
        cwd: ctx.session.cwd,
      })
    },
  },
})
