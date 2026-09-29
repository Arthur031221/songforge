"""Optional lyric writer through a local Ollama server."""

from __future__ import annotations

import re

import httpx

from . import config

PROMPT = """Write original song lyrics.
Topic: {topic}
Style: {style}
Rules:
- Use only these section tags on their own lines: [Verse], [Chorus], [Bridge], [Outro].
- Structure: [Verse], [Chorus], [Verse], [Chorus], [Bridge], [Chorus].
- 4 lines per section, 6 to 10 syllables per line, simple words that are easy to sing.
- No title, no notes, no markdown, no numbering. Output the lyrics only."""


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
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.replace("**", "").replace("```", "")
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        tag = re.fullmatch(
            r"\[?\(?(verse|chorus|bridge|outro|intro|pre-chorus)[^\]\)]*\]?\)?:?", line, flags=re.I
        )
        if tag:
            word = tag.group(1).title()
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{word}]")
        elif line:
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


def write(topic: str, style: str, timeout: float = 120.0) -> str:
    topic = topic.strip() or "a night drive with an old friend"
    body = {
        "model": config.lyrics_model(),
        "prompt": PROMPT.format(topic=topic, style=style.strip() or "pop"),
        "stream": False,
        "think": False,
        "options": {"temperature": 0.8, "num_predict": 600},
    }
    try:
        response = httpx.post(f"{config.ollama_url()}/api/generate", json=body, timeout=timeout)
        response.raise_for_status()
        text = response.json().get("response", "")
    except httpx.HTTPError as error:
        raise LyricsError(f"Ollama request failed: {error}") from error
    lyrics = clean(text)
    if "[" not in lyrics or len(lyrics) < 40:
        raise LyricsError("The lyric model returned no usable lyrics. Try again.")
    return lyrics
