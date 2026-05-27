from __future__ import annotations

import re

_AUTH_HEADER_PATTERN = r"Authorization:\s*" + r"Bearer\s+[A-Za-z0-9._~+\-/=]+"
_SECRET_PATTERNS = [
    (re.compile(_AUTH_HEADER_PATTERN, re.IGNORECASE), "Authorization: Bearer <redacted>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+\-/=]+", re.IGNORECASE), "Bearer <redacted>"),
    (re.compile(r"Cookie:\s*[^\n\r]+", re.IGNORECASE), "Cookie: <redacted>"),
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE), "bot<redacted>"),
]
TYPING_ACTION_INTERVAL_SECONDS = 4.0
TELEGRAM_UPDATE_HANDLE_TIMEOUT_SECONDS = 30.0
IDLE_PROGRESS_NOTICE_SECONDS = 120.0
STALE_RECONCILE_SECONDS = 300.0
STALE_RECONCILIATION_TIMEOUT_SECONDS = 5.0
USER_NOTICE_RATE_LIMIT_SECONDS = 120.0
AUTO_THREAD_TITLE_MAX_CHARS = 96
APPROVAL_POLICY_CHOICES = [
    ("Untrusted", "untrusted"),
    ("On failure", "on-failure"),
    ("On request", "on-request"),
    ("Never", "never"),
]
EFFORT_CHOICES = [
    ("None", "none"),
    ("Minimal", "minimal"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("Extra high", "xhigh"),
]
PERSONALITY_CHOICES = [
    ("None", "none"),
    ("Friendly", "friendly"),
    ("Pragmatic", "pragmatic"),
]
MEMORY_MODE_CHOICES = [
    ("Memory enabled", "enabled"),
    ("Memory disabled", "disabled"),
]
CLI_PERMISSION_CHOICES = [
    ("Read Only", (":read-only", "read-only"), ":read-only"),
    ("Default", (":workspace", "workspace", "workspace-write", "default"), ":workspace"),
    ("Auto-review", (":auto-review", "auto-review"), ":auto-review"),
    ("Full Access", (":danger-full-access", "danger-full-access", ":full-access", "full-access"), ":danger-full-access"),
]
ACTIVE_TURN_DISABLED_COMMANDS = frozenset(
    {
        "new",
        "resume",
        "fork",
        "side",
        "btw",
        "archive",
        "unarchive",
        "rollback",
        "rename",
        "review",
        "compact",
        "init",
        "mention",
        "read",
        "plan",
        "collab",
        "exec",
        "model",
        "permissions",
        "approval",
        "mode",
        "effort",
        "personality",
        "experimental",
        "memories",
        "skills",
        "stop",
        "setcwd",
        "workspace",
        "reset",
        "clear",
    }
)
SETTABLE_EXPERIMENTAL_FEATURES = {
    "apps",
    "memories",
    "mentions_v2",
    "plugins",
    "remote_control",
    "tool_call_mcp",
    "tool_suggest",
}
TELEGRAM_SERVER_REQUEST_SUPPORT = {
    "item/commandExecution/requestApproval": "implemented",
    "item/fileChange/requestApproval": "implemented",
    "item/permissions/requestApproval": "implemented",
    "mcpServer/elicitation/request": "implemented",
    "item/tool/requestUserInput": "implemented",
    "item/tool/call": "implemented",
    "account/chatgptAuthTokens/refresh": "unsupported",
    "attestation/generate": "not_negotiated",
    "applyPatchApproval": "not_negotiated",
    "execCommandApproval": "not_negotiated",
}
TELEGRAM_DYNAMIC_TOOLS_FINGERPRINT_KEY = "dynamic_tools_fingerprint"
TELEGRAM_GATEWAY_DEVELOPER_INSTRUCTIONS = (
    "This thread is connected through a Telegram gateway. Generated image outputs are returned to Telegram "
    "automatically. Native Telegram tools can send workspace files, structured payloads, or copy/forward the "
    "current inbound message when a payload cannot be recreated directly."
)
TELEGRAM_HELP_TEXT = """Codex Gateway commands

Basics
/start - start or pair this chat
/help - show this command reference
/status - workspace, thread, account, usage, and limits
/commands - sync Telegram's command menu

Workspace
/project - show the active workspace
/projects - list allowed workspace roots
/getcwd - show the active workspace
/setcwd <path> - switch workspace
/searchcwd <term> - search allowed workspaces
/clear - forget Telegram's active thread mapping

Threads
/new - create a new Codex thread
/threads [search] - list threads for this workspace
/resume - select a thread to resume
/fork - fork the current thread and continue
/side [text] - use an ephemeral side thread
/btw [text] - use an ephemeral aside thread
/archive - archive the current thread
/unarchive - restore the current thread
/rename <title> - rename the current thread

Turns
/compact - compact the current context
/review - review the working tree
/diff - show the local git diff
/mention <path> - ask Codex about a path
/init - create AGENTS.md instructions
/plan [text] - enter plan mode, optionally with a prompt
/goal - show, set, or clear the current goal
/cancel or /interrupt - stop the active turn
/steer <text> - steer the active turn
/exec <command> - run a shell command when enabled

Settings and Tools
/model - choose model and reasoning effort
/permissions - choose a permission profile
/personality - choose assistant style
/experimental - feature toggles
/memories - memory mode
/skills - list or toggle skills
/apps - list apps when available
/plugins - list plugins
/account - account status
/hooks - list hooks
/mcp [verbose] - MCP server status
/agent or /subagents - loaded agent threads
/ps - active commands and processes
/stop - stop background terminals
/approve - approve a recent denied action

Typed-only aliases
/approval, /mode, /effort, /features, /config, /debug-config, /limits, /read, /rollback, /workspace, /reset

Send any normal message to start or continue a Codex turn."""
