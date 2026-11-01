"""One function: prompt in, JSON out. Default backend is your Claude Code login (`claude -p`),
so selection runs on your subscription. Set llm.backend = "api" to use ANTHROPIC_API_KEY instead."""
from __future__ import annotations

import json
import os
import re
import tempfile

import requests

from .util import HookcutError, run, which


def _claude_code(prompt: str, cfg: dict) -> str:
    exe = which("claude")
    if not exe:
        raise HookcutError("Claude Code CLI not found. Install it and run `claude` once to log in, "
                           "or set llm.backend to \"api\".")
    with tempfile.TemporaryDirectory() as empty:  # empty cwd: nothing for it to go read
        out = run([exe, "-p", "--model", cfg["cli_model"], "--output-format", "json"],
                  cwd=empty, timeout=cfg.get("timeout", 600), input=prompt)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return out
    if data.get("is_error"):
        raise HookcutError(f"Claude Code error: {data.get('result')}")
    return data.get("result", "")


def _api(prompt: str, cfg: dict) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise HookcutError("Set ANTHROPIC_API_KEY to use llm.backend = \"api\".")
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=cfg.get("timeout", 600),
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": cfg["api_model"], "max_tokens": 8000,
                            "messages": [{"role": "user", "content": prompt}]})
    if r.status_code >= 400:
        raise HookcutError(f"Anthropic API {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def complete(prompt: str, cfg: dict) -> str:
    return _api(prompt, cfg) if cfg.get("backend") == "api" else _claude_code(prompt, cfg)


def extract_json(text: str):
    text = re.sub(r"```(?:json)?", "", text).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found")


def complete_json(prompt: str, cfg: dict):
    text = complete(prompt, cfg)
    try:
        return extract_json(text)
    except ValueError:
        text = complete(prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON.", cfg)
        try:
            return extract_json(text)
        except ValueError as e:
            raise HookcutError(f"Model did not return JSON: {text[:300]}") from e
