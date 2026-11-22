"""Knowledge-Base-only answers: strict prompt, hidden SOURCES line, references footer."""

from __future__ import annotations

import re

from .knowledge import Hit, content_words, tokenize

DONT_KNOW = "I don't know — your knowledge base doesn't contain information about this."
TOP_K = 4
SOURCES_MARKER = "sources:"


def strict_system_prompt(preamble: str, hits: list[Hit]) -> str:
    parts = [preamble.strip()] if preamble.strip() else []
    parts.append(
        "You are answering strictly from the user's knowledge base. Follow these rules:\n"
        "1. Use ONLY the information in the numbered knowledge base excerpts below. Do not use "
        "your own general knowledge or training data, even if you know the answer.\n"
        "2. If the excerpts don't contain the information needed to answer, reply with exactly "
        f"this sentence and nothing else: {DONT_KNOW}\n"
        "3. Never invent facts, numbers, names, quotes or sources that aren't in the excerpts. "
        "If the excerpts only partly answer the question, answer that part and say what is missing.\n"
        "4. End your answer with one final line in exactly this form, listing only the numbers of "
        "the excerpts you actually took information from (ignore excerpts you didn't use, even if "
        "they look related):\n"
        "SOURCES: 1\n"
        "Don't mention excerpt numbers or document names anywhere else, and don't write your own "
        "references list; the app shows one automatically."
    )
    excerpts = "Knowledge base excerpts:"
    for i, hit in enumerate(hits, start=1):
        excerpts += f"\n\n[Excerpt {i} — {hit.rel_path}]\n{hit.text.strip()}"
    parts.append(excerpts)
    return "\n\n".join(parts)


def _normalized(line: str) -> str:
    return "".join(c for c in line if c not in "*_`#").lstrip().lower()


class SourcesFilter:
    """Hides the model's trailing ``SOURCES: n`` line while streaming and records it."""

    def __init__(self) -> None:
        self.held = ""
        self.mid_line = False
        self.sources: list[str] = []

    def feed(self, chunk: str) -> str:
        out = []
        for piece in re.findall(r"[^\n]*\n|[^\n]+", chunk):
            ends_line = piece.endswith("\n")
            if self.mid_line:
                out.append(piece)
            else:
                self.held += piece
                norm = _normalized(self.held)
                if norm.startswith(SOURCES_MARKER):
                    if ends_line:
                        self.sources.append(norm[len(SOURCES_MARKER) :].strip())
                        self.held = ""
                    continue
                if not ends_line and SOURCES_MARKER.startswith(norm.rstrip()):
                    continue  # could still become the marker
                out.append(self.held)
                self.held = ""
                self.mid_line = not ends_line
            if ends_line:
                self.mid_line = False
        return "".join(out)

    def finish(self) -> tuple[str, list[int] | None]:
        held, self.held = self.held, ""
        tail = ""
        norm = _normalized(held)
        if norm.startswith(SOURCES_MARKER):
            self.sources.append(norm[len(SOURCES_MARKER) :].strip())
        else:
            tail = held
        if not self.sources:
            return tail, None
        return tail, [int(n) for s in self.sources for n in re.findall(r"\d+", s)]


def cited_hits(hits: list[Hit], answer: str, cited: list[int] | None) -> list[Hit]:
    if cited is not None:
        valid = [hits[n - 1] for n in cited if 1 <= n <= len(hits)]
        if valid:
            return valid
    answer_words = content_words(answer)
    overlaps = []
    for h in hits:
        chunk = set(tokenize(h.text))
        overlaps.append(sum(1 for w in answer_words if w in chunk))
    best = max(overlaps, default=0)
    if best == 0:
        return hits[:1]
    return [h for h, o in zip(hits, overlaps) if o * 10 >= best * 6]


def references_footer(hits: list[Hit]) -> str:
    names: list[str] = []
    for h in hits:
        if h.rel_path not in names:
            names.append(h.rel_path)
    if not names:
        return ""
    return "\n\n---\n**References**\n" + "\n".join(f"- {n}" for n in names)


def is_dont_know(answer: str) -> bool:
    return answer.lstrip()[:16].lower().replace("’", "'").startswith("i don't know")
