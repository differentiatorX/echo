"""Echo as an MCP server. Works with Claude Code, Claude Desktop, Antigravity, Cursor, Codex,
Gemini CLI, Windsurf, VS Code and anything else that speaks MCP. Run: echoclip mcp"""
from __future__ import annotations

import json

from . import api

INSTRUCTIONS = (
    "Echo turns a long video into the 3-5 best Instagram Reels. Workflow: call guide once and follow it. "
    "prepare -> read_brief -> submit_judgments -> finish -> status (poll) -> clips -> frames -> fix -> export. "
    "You are the judge and the editor. Never publish unless the user explicitly asks."
)


def _server():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
    return Server("echo", instructions=INSTRUCTIONS)


def build():
    mcp = _server()

    @mcp.tool()
    def guide() -> str:
        """The step-by-step playbook. Read this first and follow it."""
        return api.guide()

    @mcp.tool()
    def prepare(source: str, clips: int = 4, style: str = "echo", topic: str = "", length: str = "medium",
                aspect: str = "9:16", language: str = "") -> str:
        """Read a YouTube URL or local video path and write a judging brief. No video is downloaded yet.
        style: echo | beast | solo | neon | karaoke | minimal. length: short (15-30s) | medium (25-55s) | long (45-90s).
        aspect: 9:16 (Reels) | 4:5 (feed) | 1:1. topic: optional, only clip moments about this. language: '' or auto or a code."""
        return json.dumps(api.prepare(source, clips, style, topic, length, aspect, language), indent=2)

    @mcp.tool()
    def read_brief(job: str) -> str:
        """The transcript, audience signals and judging rules for a prepared job."""
        return api.read_brief(job)

    @mcp.tool()
    def submit_judgments(job: str, judgments: list[dict]) -> str:
        """Save your picks: a list of objects with start, end, hook, flow, value, share, loop (0-10),
        hook_title, cold_open, keywords, ig_caption, hashtags, why. Format is in the brief."""
        return json.dumps(api.submit_judgments(job, judgments))

    @mcp.tool()
    def finish(job: str) -> str:
        """Rank the picks, then download, cut and render the clips in the background (2-5 minutes)."""
        return json.dumps(api.finish(job, background=True))

    @mcp.tool()
    def status(job: str) -> str:
        """Progress of a job. Poll every 20-30 seconds until state is done or error."""
        return json.dumps(api.status(job), indent=2)

    @mcp.tool()
    def clips(job: str) -> str:
        """Every clip: score, hook, caption, file path, Instagram check notes and the exact words with indices."""
        return json.dumps(api.clips(job), indent=2, ensure_ascii=False)

    @mcp.tool()
    def frames(job: str, clip: str) -> str:
        """Save opening, middle and ending frames of a clip and return their file paths to look at."""
        return json.dumps(api.frames(job, clip))

    @mcp.tool()
    def fix(job: str, clip: str, start_at: int | None = None, end_after: int | None = None, cut: str = "",
            hook: str = "", cold_open: bool | None = None, style: str = "", reset: bool = False) -> str:
        """Edit one clip and re-render it in seconds. start_at / end_after are word indices from clips.
        cut removes indices like "12-15,40". hook rewrites the on-screen title. style changes captions."""
        return json.dumps(api.fix(job, clip, start_at, end_after, cut, hook, cold_open, style, reset))

    @mcp.tool()
    def cover(job: str, clip: str) -> str:
        """Make a grid-safe cover image with the hook in big type."""
        return api.make_cover(job, clip)

    @mcp.tool()
    def export(job: str, folder: str = "") -> str:
        """Copy finished clips, covers and ready-to-paste captions into one folder (default Videos/Echo)."""
        return json.dumps(api.export(job, folder))

    @mcp.tool()
    def jobs() -> str:
        """List videos Echo has worked on."""
        return json.dumps(api.list_jobs(), indent=2)

    @mcp.tool()
    def best_time() -> str:
        """Hours when the user's followers are most active, from Instagram insights."""
        return json.dumps(api.best_time(), indent=2)

    @mcp.tool()
    def publish(job: str, clip: str, trial: bool = False) -> str:
        """Post one clip to Instagram. Only call this when the user explicitly asked to post."""
        return json.dumps(api.publish(job, clip, trial))

    return mcp


def main():
    build().run()


if __name__ == "__main__":
    main()
