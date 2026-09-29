"""Optional lyric writer through a local Ollama server."""

from __future__ import annotations

import json
import re

import httpx

from . import config

SYSTEM = "You write song lyrics."

PROMPT = """Write original song lyrics about: {topic}
Style: {style}
Sections in order: Verse, Chorus, Verse, Chorus, Bridge, Chorus.
Four lines per section, two for the Bridge. Short lines, 6 to 10 syllables,
simple words that are easy to sing."""

# Structured output: the grammar leaves no room for commentary or visible reasoning.
SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "minItems": 3,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "enum": ["Verse", "Chorus", "Bridge", "Outro"]},
                    "lines": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 80},
                    },
                },
                "required": ["tag", "lines"],
            },
        }
    },
    "required": ["sections"],
}

TAG = re.compile(r"\[?\(?(verse|chorus|bridge|outro|intro|pre-chorus)[^\]\)]*\]?\)?:?", re.I)


class LyricsError(RuntimeError):
    pass


def status(timeout: float = 1.5) -> dict:
    """Report whether Ollama answers and whether the lyric model is pulled."""
    model = config.lyrics_model()
    try:
        response = httpx.get(f"{config.ollama_url()}/api/tags", timeout=timeout)
        response.raise_for_status()
        names = [m.get("name", "") for m in response.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return {"available": False, "model": model, "reason": "Ollama is not running"}
    wanted = model if ":" in model else f"{model}:latest"
    if wanted not in names:
        return {
            "available": False,
            "model": model,
            "reason": f"Model {model} is not pulled. Run: ollama pull {model}",
        }
    return {"available": True, "model": model}


def clean(text: str) -> str:
    """Keep the lyrics, drop anything a chatty model wrote before or around them."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.replace("**", "").replace("```", "").replace("#", "")
    raw = [line.strip() for line in text.splitlines()]
    first = next((i for i, line in enumerate(raw) if TAG.fullmatch(line)), None)
    if first is None:
        return ""
    lines: list[str] = []
    for line in raw[first:]:
        tag = TAG.fullmatch(line)
        if tag:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{tag.group(1).title()}]")
        elif line and len(line) <= 80 and not line.lower().startswith(("note", "title")):
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


def from_json(text: str) -> str:
    """Turn the structured reply into tagged lyrics. Falls back to text cleanup."""
    try:
        data = json.loads(text)
        sections = data["sections"]
        blocks = []
        for section in sections:
            lines = [str(line).strip() for line in section["lines"] if str(line).strip()]
            if lines:
                blocks.append(f"[{str(section['tag']).title()}]\n" + "\n".join(lines))
        return "\n\n".join(blocks) + "\n" if blocks else ""
    except (ValueError, KeyError, TypeError):
        return clean(text)


def write(topic: str, style: str, timeout: float = 180.0) -> str:
    topic = topic.strip() or "a night drive with an old friend"
    body = {
        "model": config.lyrics_model(),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(topic=topic, style=style.strip() or "pop")},
        ],
        "stream": False,
        "think": False,
        "format": SCHEMA,
        "options": {"temperature": 0.8, "num_predict": 900},
    }
    try:
        response = httpx.post(f"{config.ollama_url()}/api/chat", json=body, timeout=timeout)
        response.raise_for_status()
        text = response.json().get("message", {}).get("content", "")
    except httpx.HTTPError as error:
        raise LyricsError(f"Ollama request failed: {error}") from error
    lyrics = from_json(text)
    if lyrics.count("[") < 2 or len(lyrics) < 60:
        raise LyricsError("The lyric model returned no usable lyrics. Try again.")
    return lyrics
