"""`echoclip connect`: register Echo's MCP server with every agent on this machine.
Existing configs are merged, never replaced, and backed up first."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

H = Path.home()


def _entry() -> dict:
    # absolute interpreter path: GUI apps often don't inherit the shell PATH
    return {"command": sys.executable, "args": ["-m", "echoclip.cli", "mcp"]}


def _app_support(name: str) -> Path:
    sysname = platform.system()
    if sysname == "Windows":
        return Path(os.environ.get("APPDATA", H / "AppData" / "Roaming")) / name
    if sysname == "Darwin":
        return H / "Library" / "Application Support" / name
    return H / ".config" / name


TARGETS = {
    "claude-desktop": [lambda: _app_support("Claude") / "claude_desktop_config.json"],
    "cursor": [lambda: H / ".cursor" / "mcp.json"],
    "antigravity": [lambda: H / ".gemini" / "config" / "mcp_config.json",
                    lambda: H / ".gemini" / "antigravity" / "mcp_config.json"],
    "gemini": [lambda: H / ".gemini" / "settings.json"],
    "windsurf": [lambda: H / ".codeium" / "windsurf" / "mcp_config.json"],
}


def _merge_json(path: Path, key: str = "mcpServers") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8") or "{}")
        except json.JSONDecodeError:
            return f"  skipped {path} (not valid JSON, edit it by hand)"
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    data.setdefault(key, {})["echo"] = _entry()
    path.write_text(json.dumps(data, indent=2), "utf-8")
    return f"  wrote {path}"


def _codex(write: bool) -> list[str]:
    path = H / ".codex" / "config.toml"
    e = _entry()
    block = ('\n[mcp_servers.echo]\ncommand = ' + json.dumps(e["command"]) +
             '\nargs = ' + json.dumps(e["args"]) + '\nstartup_timeout_sec = 30\ntool_timeout_sec = 600\n')
    if not write:
        return [f"Codex: add to {path}", block]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text("utf-8") if path.exists() else ""
    if "[mcp_servers.echo]" in text:
        return [f"Codex: already connected ({path})"]
    if text:
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    path.write_text(text + block, "utf-8")
    return [f"Codex: wrote {path}"]


def _claude_code(write: bool) -> list[str]:
    e = _entry()
    cmd = ["claude", "mcp", "add", "--scope", "user", "echo", "--", e["command"], *e["args"]]
    exe = shutil.which("claude")
    if not write or not exe:
        return ["Claude Code: run this once:", "  " + " ".join(f'"{c}"' if " " in c else c for c in cmd)]
    subprocess.run([exe, "mcp", "remove", "--scope", "user", "echo"], capture_output=True)
    p = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True)
    return ["Claude Code: connected" if p.returncode == 0 else f"Claude Code: {p.stderr.strip()[:200]}"]


def _vscode() -> list[str]:
    e = _entry()
    snippet = json.dumps({"servers": {"echo": {"type": "stdio", **e}}}, indent=2)
    return ["VS Code (Copilot agent mode): save this as .vscode/mcp.json in your workspace:", snippet]


def connect(agent: str = "all", write: bool = True) -> list[str]:
    agent = agent.lower()
    names = ["claude-code", "claude-desktop", "antigravity", "cursor", "codex", "gemini", "windsurf", "vscode"]
    if agent != "all" and agent not in names:
        return [f"Unknown agent '{agent}'. Use one of: {', '.join(names)} or all"]
    picked = names if agent == "all" else [agent]
    out: list[str] = []
    for n in picked:
        if n == "claude-code":
            out += _claude_code(write)
        elif n == "codex":
            if agent == "all" and write and not (H / ".codex").exists():
                continue
            out += _codex(write)
        elif n == "vscode":
            out += _vscode()
        else:
            if not write:
                out += [f"{n}: add this under \"mcpServers\" in {TARGETS[n][0]()}",
                        json.dumps({"echo": _entry()}, indent=2)]
                continue
            # when writing "all", only touch agents that look installed
            for target in TARGETS[n]:
                path = target()
                if agent == "all" and not path.parent.exists():
                    continue
                out.append(f"{n}:" + _merge_json(path)[1:])
    out.append("Restart your agent, then ask it: \"Use echo to make 4 Reels from <link>\".")
    return out
