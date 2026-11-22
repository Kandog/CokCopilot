"""Persistent knowledge base that mirrors a watched folder, plus retrieval.

The folder is the single source of truth: documents added, edited or deleted
in it (by the app or any other tool) are re-indexed by ``sync``. The index
(chunks + vectors) lives in the data dir, never in the user's folder.

Embeddings come from the configured /embeddings endpoint when available,
otherwise a local hashed bag-of-words. Whichever method produced the first
embedding is kept for the whole knowledge base so vectors stay comparable.
The index format is the same as the Rust version (``knowledge_index.json``
version 2 + ``knowledge/<id>.chunks.json``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import config, llm
from .models import Settings, new_id
from .parsers import SUPPORTED_DOC_EXT, extension, extract_document_text

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
LOCAL_HASH_DIM = 2048
INDEX_VERSION = 2

METHOD_API = "api"
METHOD_LOCAL = "local-hash"

STATUS_INDEXED = "indexed"
STATUS_INDEXING = "indexing"
STATUS_ERROR = "error"

IGNORED_PREFIXES = ("~$", ".", "~")
IGNORED_SUFFIXES = (".tmp", ".temp", ".crdownload", ".part", ".partial", ".swp", "~")

STOPWORDS = set(
    """a about after all also an and any are as at be been but by can could did do does for from get give had has
    have how i if in into is it its it's just know let like me more my no not of on or our please show so some tell
    than that the their them then there these they this to up us use was we were what what's when where which who
    why will with would you your""".split()
)


# -- chunking ---------------------------------------------------------------


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph or sentence boundaries where possible."""
    text = text.strip()
    n = len(text)
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            half = start + size // 2
            boundary = text.rfind("\n\n", start, end)
            if boundary <= half:
                boundary = text.rfind(". ", start, end)
            if boundary > half:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


# -- local embedding ----------------------------------------------------------


def tokenize(text: str) -> list[str]:
    return [m.lower() for m in re.findall(r"[A-Za-z0-9_']+", text)]


def content_words(text: str) -> list[str]:
    return sorted({w for w in tokenize(text) if len(w) > 1 and w not in STOPWORDS})


def local_embed_one(text: str) -> list[float]:
    vec = [0.0] * LOCAL_HASH_DIM
    for w in tokenize(text):
        vec[int.from_bytes(hashlib.md5(w.encode(), usedforsecurity=False).digest(), "big") % LOCAL_HASH_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


# -- folder scanning ------------------------------------------------------------


def is_indexable_name(name: str) -> bool:
    lower = name.lower()
    if lower.startswith(IGNORED_PREFIXES) or lower.endswith(IGNORED_SUFFIXES):
        return False
    return extension(lower) in SUPPORTED_DOC_EXT


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class _Found:
    abs: Path
    size: int
    mtime: float


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
    except OSError:
        return True


def _walk_folder(folder: Path, no_links: bool = False, max_files: int | None = None) -> dict[str, _Found]:
    """Indexable documents in ``folder`` and all its subfolders.

    ``no_links``: skip symbolic links and junctions, so a linked shared folder can't
    lead the server to files outside it. ``max_files``: stop after that many.
    """
    found: dict[str, _Found] = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                             and not (no_links and _is_link(Path(dirpath) / d)))
        rel_dir = Path(dirpath).relative_to(folder)
        for name in sorted(filenames):
            if not is_indexable_name(name):
                continue
            p = Path(dirpath) / name
            if no_links and _is_link(p):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            found[(rel_dir / name).as_posix()] = _Found(p, st.st_size, st.st_mtime)
            if max_files is not None and len(found) >= max_files:
                return dict(sorted(found.items()))
    return dict(sorted(found.items()))


# -- data -------------------------------------------------------------------------


@dataclass
class KBFile:
    id: str
    rel_path: str
    source_hash: str = ""
    size: int = -1
    mtime: float = -1.0
    chunk_count: int = 0
    status: str = STATUS_INDEXED
    error: str = ""

    @property
    def filename(self) -> str:
        return self.rel_path.rsplit("/", 1)[-1]


@dataclass
class Hit:
    rel_path: str
    text: str
    score: float


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.errors)

    def summary(self) -> str:
        parts = []
        for items, label in ((self.added, "added"), (self.updated, "updated"), (self.removed, "removed"),
                             (self.errors, "failed")):
            if items:
                parts.append(f"{len(items)} {label}")
        return ", ".join(parts)


class KnowledgeBase:
    def __init__(self, folder: Path, index_file: Path | None = None, chunk_dir: Path | None = None):
        self.folder = folder
        self.index_file = index_file or config.KNOWLEDGE_INDEX_FILE
        self.chunk_dir = chunk_dir or config.KNOWLEDGE_DIR
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_method = METHOD_LOCAL
        self.files: list[KBFile] = []
        self.chunks: dict[str, list[dict[str, Any]]] = {}
        self._legacy: list[dict[str, Any]] = []
        self._sync_lock = asyncio.Lock()
        self.use_trash = True
        self.syncing = False
        self.progress = ""
        # A linked shared folder (server mode): skip links, cap the file count, and a
        # background watcher keeps it in sync (so searches needn't rescan first).
        self.no_links = False
        self.max_files: int | None = None
        self.watched = False
        self.limit_reached = False
        self.listeners: list[Callable[[SyncResult], None]] = []
        self._load_index()

    # -- persistence --

    def _load_index(self) -> None:
        data = config.read_json(self.index_file)
        if not isinstance(data, dict):
            return
        self.embedding_method = data.get("embedding_method") or METHOD_LOCAL
        files = data.get("files") or []
        if data.get("version", 1) < INDEX_VERSION:
            # Old format: files were copied into the chunk dir; moved into the folder on first sync.
            self._legacy = [f for f in files if isinstance(f, dict) and "stored_path" in f]
            return
        if Path(data.get("folder", "")) != self.folder:
            self._drop_chunk_files([f.get("id", "") for f in files if isinstance(f, dict)])
            return
        for f in files:
            try:
                kb = KBFile(**{k: f[k] for k in KBFile.__dataclass_fields__ if k in f})
            except TypeError:
                continue
            loaded = config.read_json(self._chunk_path(kb.id)) or []
            if kb.status == STATUS_INDEXING or (kb.status == STATUS_INDEXED and not loaded):
                kb.source_hash, kb.size = "", -1  # interrupted; redo
            self.files.append(kb)
            self.chunks[kb.id] = loaded

    def _save_index(self) -> None:
        config.write_json(
            self.index_file,
            {
                "version": INDEX_VERSION,
                "embedding_method": self.embedding_method,
                "folder": str(self.folder),
                "files": [asdict(f) for f in self.files],
            },
        )

    def _chunk_path(self, file_id: str) -> Path:
        return self.chunk_dir / f"{file_id}.chunks.json"

    def _save_chunks(self, file_id: str, chunks: list[dict[str, Any]]) -> None:
        self._chunk_path(file_id).write_text(json.dumps(chunks), encoding="utf-8")

    def _drop_chunk_files(self, ids: list[str]) -> None:
        for i in ids:
            if i:
                self._chunk_path(i).unlink(missing_ok=True)

    def _migrate_legacy(self) -> None:
        if not self._legacy:
            return
        legacy, self._legacy = self._legacy, []
        self.folder.mkdir(parents=True, exist_ok=True)
        for entry in legacy:
            src = self.chunk_dir / entry["stored_path"]
            dest = self.folder / (entry.get("filename") or src.name)
            if src.exists() and not dest.exists():
                try:
                    shutil.move(src, dest)
                except OSError:
                    pass
            self._drop_chunk_files([entry.get("id", "")])
        self._save_index()

    # -- embedding --

    def _has_any_chunks(self) -> bool:
        return any(self.chunks.get(f.id) for f in self.files)

    async def _embed(self, settings: Settings | None, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._has_any_chunks():
            result = await llm.embed_texts(settings, texts) if settings else None
            if result:
                self.embedding_method = METHOD_API
                return result
            self.embedding_method = METHOD_LOCAL
            return [local_embed_one(t) for t in texts]
        if self.embedding_method == METHOD_API:
            if settings is None:
                return []
            return await llm.embed_texts(settings, texts) or []
        return [local_embed_one(t) for t in texts]

    # -- folder / files --

    async def set_folder(self, folder: Path) -> None:
        async with self._sync_lock:
            if folder == self.folder:
                return
            self.folder = folder
            self._drop_chunk_files([f.id for f in self.files])
            self.files, self.chunks = [], {}
            self._save_index()
        self._notify(SyncResult(removed=["*"]))

    def list_files(self) -> list[KBFile]:
        return sorted(self.files, key=lambda f: f.rel_path.lower())

    def get_file(self, file_id: str) -> KBFile | None:
        return next((f for f in self.files if f.id == file_id), None)

    def abs_path(self, file_id: str) -> Path | None:
        f = self.get_file(file_id)
        return self.folder / f.rel_path if f else None

    def import_bytes(self, filename: str, data: bytes) -> Path:
        name = Path(filename).name
        if not name or not is_indexable_name(name):
            raise ValueError(f"{filename}: unsupported file type")
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = self.folder / name
        dest.write_bytes(data)
        return dest

    def remove_file(self, file_id: str) -> None:
        p = self.abs_path(file_id)
        if p and p.exists():
            if self.use_trash:
                try:
                    from send2trash import send2trash

                    send2trash(str(p))
                    return
                except Exception:
                    pass
            p.unlink(missing_ok=True)

    def folder_bytes(self) -> int:
        """Total size of the documents in the folder."""
        if not self.folder.is_dir():
            return 0
        return sum(f.size for f in _walk_folder(self.folder).values())

    def mark_for_reindex(self, file_id: str | None = None) -> None:
        for f in self.files:
            if file_id is None or f.id == file_id:
                f.source_hash, f.size = "", -1

    def _notify(self, result: SyncResult) -> None:
        for cb in list(self.listeners):
            try:
                cb(result)
            except Exception:
                pass

    # -- sync --

    async def sync(self, settings: Settings | None) -> SyncResult:
        """Bring the index in line with the folder. Cheap (stat only) when nothing changed."""
        result = SyncResult()
        async with self._sync_lock:
            self.syncing = True
            try:
                self._migrate_legacy()
                folder = self.folder
                if folder.is_dir():
                    try:
                        found = await asyncio.to_thread(_walk_folder, folder, self.no_links, self.max_files)
                    except OSError:
                        return result
                    self.limit_reached = self.max_files is not None and len(found) >= self.max_files
                else:
                    # A share that is briefly unreachable must not wipe the whole index.
                    if self.watched and self.files:
                        return result
                    found = {}
                by_rel = {f.rel_path: f for f in self.files}
                gone = [f for f in self.files if f.rel_path not in found]
                if gone:
                    self.files = [f for f in self.files if f.rel_path in found]
                    for f in gone:
                        self.chunks.pop(f.id, None)
                        result.removed.append(f.rel_path)
                    self._drop_chunk_files([f.id for f in gone])
                    self._save_index()
                for rel, info in found.items():
                    existing = by_rel.get(rel)
                    if existing and existing.size == info.size and existing.mtime == info.mtime:
                        continue
                    await self._index_one(rel, info, existing, settings, result)
            finally:
                self.syncing = False
                self.progress = ""
        if result.changed():
            self._notify(result)
        return result

    async def _index_one(
        self, rel: str, info: _Found, existing: KBFile | None, settings: Settings | None, result: SyncResult
    ) -> None:
        try:
            source_hash = await asyncio.to_thread(_hash_file, info.abs)
        except OSError:
            return  # locked / mid-write: retry next sync
        if existing and existing.source_hash == source_hash and existing.status == STATUS_INDEXED:
            existing.size, existing.mtime = info.size, info.mtime
            self._save_index()
            return

        self.progress = f"Indexing {rel}…"
        file_id = existing.id if existing else new_id()
        chunks: list[dict[str, Any]] = []
        error = ""
        try:
            text = await asyncio.to_thread(extract_document_text, info.abs)
        except PermissionError:
            return
        except Exception as e:  # noqa: BLE001 - any parser failure is reported per file
            error = str(e) or type(e).__name__
        else:
            pieces = chunk_text(text)
            if not pieces:
                error = "No extractable text found in this file."
            else:
                vectors = await self._embed(settings, pieces)
                if not vectors:
                    error = ("Could not compute embeddings (the configured API's /embeddings endpoint "
                             "failed). File was not indexed.")
                else:
                    chunks = [{"text": t, "vector": v} for t, v in zip(pieces, vectors)]

        kb = KBFile(file_id, rel, source_hash, info.size, info.mtime, len(chunks),
                    STATUS_ERROR if error else STATUS_INDEXED, error)
        self._save_chunks(file_id, chunks)
        self.chunks[file_id] = chunks
        for i, f in enumerate(self.files):
            if f.id == file_id:
                self.files[i] = kb
                break
        else:
            self.files.append(kb)
        self._save_index()
        if error:
            result.errors.append((rel, error))
        elif existing:
            result.updated.append(rel)
        else:
            result.added.append(rel)

    # -- retrieval --

    async def retrieve(self, query: str, settings: Settings | None, top_k: int, min_score: float) -> list[Hit]:
        """Relevant chunks, best first; empty = the KB doesn't cover this.

        Raises LLMError if an API-embedded KB can't embed the query (we never mix
        vector spaces by falling back to local hashing).
        """
        if not self.files or not query.strip():
            return []
        use_api = self.embedding_method == METHOD_API and settings is not None
        if use_api:
            vecs = await llm.embed_texts(settings, [query])
            if not vecs:
                raise llm.LLMError("the embeddings endpoint didn't respond")
            qvec = vecs[0]
        else:
            qvec = local_embed_one(query)
        keywords = set(content_words(query))
        if not use_api and not keywords:
            return []
        hits = []
        for f in self.files:
            if f.status != STATUS_INDEXED:
                continue
            for c in self.chunks.get(f.id) or []:
                score = cosine(qvec, c["vector"])
                if use_api:
                    relevant = score >= min_score
                else:
                    relevant = score > 0 and bool(keywords & set(tokenize(c["text"])))
                if relevant:
                    hits.append(Hit(f.rel_path, c["text"], score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


async def watch_folder(kb: KnowledgeBase, get_settings: Callable[[], Settings], stop: asyncio.Event,
                       poll_seconds: int = 10) -> None:
    """Keep ``kb`` in sync: OS file events (debounced) plus a poll every ``poll_seconds`` as a safety net
    (network shares don't always report changes)."""
    from watchfiles import awatch

    while not stop.is_set():
        folder = kb.folder
        try:
            await kb.sync(get_settings())
        except Exception as e:  # noqa: BLE001
            print(f"Knowledge sync failed: {e}")
        if not folder.is_dir():
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
            continue
        try:
            async for _changes in awatch(folder, stop_event=stop, debounce=800, rust_timeout=poll_seconds * 1000,
                                         yield_on_timeout=True, recursive=True):
                if kb.folder != folder:
                    break
                await kb.sync(get_settings())
        except Exception as e:  # noqa: BLE001 - e.g. folder removed while watching
            print(f"Knowledge watcher restarting: {e}")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
