"""Bounded private document ingestion and indexed BM25-style retrieval.

Sources are untrusted reference material. Ingestion never changes financial records.
Only extracted text is retained; source uploads are never served as active content.
"""
from __future__ import annotations

from collections import Counter
from datetime import timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from threading import BoundedSemaphore
import unicodedata
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from ..persistence.database import MembershipRow, UserRow
from ..persistence.document_models import DocumentRow, DocumentChunkRow, DocumentTermRow
from .auth import Principal

MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 500_000
MAX_CHUNKS = 400
MAX_DOCUMENTS = 50
MAX_LIBRARY_BYTES = 20_000_000
MAX_SELECTION = 20
_PDF_SLOTS = BoundedSemaphore(2)
_STOP = frozenset("a an and are as at be by do does for from how i in is it me my of on or our that the their this to was we what when where which who will with would you your tell about according document documents file files please".split())
_TOKEN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)


def tokens(value: str) -> list[str]:
    return [term for term in _TOKEN.findall(unicodedata.normalize("NFKC", value).casefold())
            if 1 < len(term) <= 80 and term not in _STOP]


def _clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.replace("\r\n", "\n").replace("\r", "\n"))
    value = "".join(c for c in value if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cs"})
    return re.sub(r"[ \t]+", " ", value).strip()


def _title(value: str) -> str:
    value = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    value = _clean_text(value).replace("\n", " ")[:200].strip()
    if not value:
        raise ValueError("Give the document a title.")
    return value


def _chunks(pages):
    result = []
    for page, original in pages:
        text = _clean_text(original)
        start = 0
        while start < len(text):
            end = min(start + 2200, len(text))
            if end < len(text):
                split = max(text.rfind("\n", start + 1400, end), text.rfind(" ", start + 1400, end))
                if split > start:
                    end = split
            chunk = text[start:end].strip()
            if chunk:
                result.append((page, chunk))
            if len(result) > MAX_CHUNKS:
                raise ValueError("The document contains too much text. Split it into smaller files.")
            if end == len(text):
                break
            start = max(start + 1, end - 150)
    if not result:
        raise ValueError("The document does not contain readable text.")
    return result


def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _document(row):
    return {"id": row.id, "title": row.title, "status": "ready", "media_type": row.media_type,
            "source_type": row.source_type, "source_metadata": row.source_metadata,
            "byte_size": row.byte_size, "page_count": row.page_count,
            "chunk_count": row.chunk_count, "created_at": _iso(row.created_at)}


def _source(row, document, *, score=None):
    result = {"id": document.id, "document_id": document.id, "title": document.title,
              "page": row.page, "chunk_id": row.id, "text": row.text,
              "source_type": document.source_type, "source_metadata": document.source_metadata}
    if score is not None:
        result["score"] = round(score, 4)
    return result


class DocumentService:
    def __init__(self, database):
        self.db = database

    @staticmethod
    def _member(session, p: Principal):
        if session.get(MembershipRow, (p.user_id, p.household_id)) is None:
            raise KeyError("Workspace not found")

    @staticmethod
    def _scope(p):
        return (DocumentRow.household_id == p.household_id, DocumentRow.user_id == p.user_id)

    def _selected(self, session, p, document_ids):
        if document_ids is None:
            return None
        if not isinstance(document_ids, (list, tuple)) or len(document_ids) > MAX_SELECTION:
            raise ValueError("Select at most 20 documents.")
        if any(not isinstance(value, str) or len(value) > 40 for value in document_ids):
            raise ValueError("Invalid document selection.")
        ids = list(dict.fromkeys(document_ids))
        found = session.scalars(select(DocumentRow.id).where(*self._scope(p), DocumentRow.id.in_(ids))).all()
        if set(found) != set(ids):
            raise KeyError("Document not found")
        return ids

    def validate_ids(self, p, document_ids):
        with self.db.sessions() as session:
            self._member(session, p)
            return self._selected(session, p, document_ids)

    def list(self, p):
        with self.db.sessions() as session:
            self._member(session, p)
            return [_document(row) for row in session.scalars(select(DocumentRow).where(*self._scope(p))
                    .order_by(DocumentRow.created_at.desc(), DocumentRow.id).limit(MAX_DOCUMENTS))]

    def ingest_file(self, p, filename: str, content: bytes):
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Upload a non-empty file smaller than 2 MB.")
        title = _title(filename)
        suffix = Path(title).suffix.casefold()
        if suffix == ".pdf":
            if not content.lstrip().startswith(b"%PDF-"):
                raise ValueError("The file is not a valid PDF.")
            if not _PDF_SLOTS.acquire(blocking=False):
                raise ValueError("Document processing is busy. Try again shortly.")
            try:
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
                proc = subprocess.run([sys.executable, "-m", "finpilot.services.document_extract"],
                    input=content, capture_output=True, timeout=15, check=False,
                    cwd=str(Path(__file__).resolve().parents[2]), **options)
                if proc.returncode != 0:
                    raise ValueError("This PDF could not be read within the document processing limits.")
                extracted = json.loads(proc.stdout.decode("utf-8"))
                if extracted.get("error"):
                    raise ValueError(extracted["error"])
                pages = extracted["pages"]
            except subprocess.TimeoutExpired:
                raise ValueError("This PDF took too long to read. Export a simpler text PDF and retry.") from None
            finally:
                _PDF_SLOTS.release()
            media_type = "application/pdf"
        elif suffix in {".txt", ".md"}:
            try:
                value = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("Text documents must use UTF-8 encoding.") from None
            if "\x00" in value:
                raise ValueError("Binary files are not supported. Upload UTF-8 text or a text PDF.")
            if len(value) > MAX_TEXT_CHARS:
                raise ValueError("The document contains too much text. Split it into smaller files.")
            pages, media_type = [(1, value)], "text/markdown" if suffix == ".md" else "text/plain"
        else:
            raise ValueError("Supported document types are PDF, TXT, and Markdown (.md).")
        return self._ingest(p, title, pages, content, media_type, "upload", {})

    def ingest_text(self, p, title, text, source_type="mcp", source_metadata=None):
        if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
            raise ValueError("Imported text must contain at most 500,000 characters.")
        content = text.encode("utf-8")
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Imported text must be non-empty and smaller than 2 MB.")
        if source_type not in {"mcp", "upload"}:
            raise ValueError("Unsupported document source.")
        metadata = source_metadata or {}
        if not isinstance(metadata, dict) or len(json.dumps(metadata, allow_nan=False).encode("utf-8")) > 8192:
            raise ValueError("Document provenance is too large.")
        return self._ingest(p, _title(title), [(1, text)], content, "text/plain", source_type, metadata)

    def _ingest(self, p, title, pages, content, media_type, source_type, metadata):
        chunks = _chunks(pages)
        # Keep identical remote bytes with different provenance distinguishable.
        digest = hashlib.sha256(content + b"\0" + source_type.encode("utf-8") + b"\0" +
                                json.dumps(metadata, sort_keys=True).encode("utf-8")).hexdigest()
        def duplicate(session):
            row = session.scalar(select(DocumentRow).where(*self._scope(p), DocumentRow.sha256 == digest))
            return {"document": _document(row), "duplicate": True} if row else None
        try:
            with self.db.sessions.begin() as session:
                self._member(session, p)
                # Serialize each user's ingestion quota check on PostgreSQL.
                session.execute(select(UserRow.id).where(UserRow.id == p.user_id).with_for_update()).scalar_one()
                existing = duplicate(session)
                if existing:
                    return existing
                count, size = session.execute(select(func.count(DocumentRow.id), func.coalesce(func.sum(DocumentRow.byte_size), 0))
                                              .where(*self._scope(p))).one()
                if count >= MAX_DOCUMENTS or size + len(content) > MAX_LIBRARY_BYTES:
                    raise ValueError("Your private library is full (50 documents / 20 MB). Delete a document first.")
                row = DocumentRow(id="doc_" + uuid4().hex, household_id=p.household_id, user_id=p.user_id,
                    title=title, media_type=media_type, source_type=source_type, source_metadata=metadata,
                    sha256=digest, byte_size=len(content), page_count=max(page for page, _ in pages), chunk_count=len(chunks))
                session.add(row)
                session.flush()
                postings = []
                for position, (page, text) in enumerate(chunks):
                    # Title terms improve short topic queries while citations remain exact text.
                    counts = Counter(tokens(title + " " + text))
                    chunk = DocumentChunkRow(id="chk_" + uuid4().hex, document_id=row.id, page=page,
                        position=position, text=text, token_count=sum(counts.values()))
                    session.add(chunk)
                    postings.extend({"chunk_id": chunk.id, "term": term, "household_id": p.household_id,
                        "user_id": p.user_id, "frequency": frequency} for term, frequency in counts.items())
                session.flush()
                if postings:
                    session.execute(DocumentTermRow.__table__.insert(), postings)
                return {"document": _document(row), "duplicate": False}
        except IntegrityError:
            # Concurrent duplicate uploads converge on the unique owner/content key.
            with self.db.sessions() as session:
                self._member(session, p)
                existing = duplicate(session)
                if existing:
                    return existing
            raise

    def detail(self, p, document_id):
        with self.db.sessions() as session:
            self._member(session, p)
            row = session.scalar(select(DocumentRow).where(*self._scope(p), DocumentRow.id == document_id))
            if row is None:
                raise KeyError("Document not found")
            chunks = session.scalars(select(DocumentChunkRow).where(DocumentChunkRow.document_id == row.id)
                                    .order_by(DocumentChunkRow.position)).all()
            return {"document": _document(row), "chunks": [_source(chunk, row) for chunk in chunks]}

    def delete(self, p, document_id):
        from .private_commands import delete_private
        with self.db.sessions.begin() as session:
            delete_private(session,p,"delete_document",document_id)
            return True

    def overview(self, p, document_ids, limit=6):
        """First excerpts for an explicit summary request, spread across selections."""
        limit = max(1, min(int(limit), 8))
        with self.db.sessions() as session:
            self._member(session, p)
            ids = self._selected(session, p, document_ids)
            if not ids:
                return []
            rows = session.execute(select(DocumentChunkRow, DocumentRow).join(DocumentRow)
                .where(*self._scope(p), DocumentRow.id.in_(ids))
                .order_by(DocumentChunkRow.position, DocumentRow.id).limit(limit)).all()
            result, size = [], 0
            for chunk, document in rows:
                if size + len(chunk.text) > 10_000:
                    break
                result.append(_source(chunk, document, score=0))
                size += len(chunk.text)
            return result

    def retrieve(self, p, query: str, document_ids=None, limit=6):
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError("Search queries must contain at most 4,000 characters.")
        limit = max(1, min(int(limit), 8))
        terms = list(dict.fromkeys(tokens(query)))[:32]
        with self.db.sessions() as session:
            self._member(session, p)
            ids = self._selected(session, p, document_ids)
            if ids == [] or not terms:
                return []
            owned = select(DocumentRow.id).where(*self._scope(p))
            if ids is not None:
                owned = owned.where(DocumentRow.id.in_(ids))
            match = (DocumentTermRow.household_id == p.household_id, DocumentTermRow.user_id == p.user_id,
                     DocumentTermRow.term.in_(terms), DocumentChunkRow.document_id.in_(owned))
            # Candidate selection uses the owner/term index; never load the whole corpus.
            candidates = session.scalars(select(DocumentTermRow.chunk_id).join(DocumentChunkRow,
                    DocumentTermRow.chunk_id == DocumentChunkRow.id).where(*match)
                .group_by(DocumentTermRow.chunk_id)
                .order_by(func.count(DocumentTermRow.term).desc(), func.sum(DocumentTermRow.frequency).desc(), DocumentTermRow.chunk_id)
                .limit(200)).all()
            if not candidates:
                return []
            total = session.scalar(select(func.count(DocumentChunkRow.id)).where(DocumentChunkRow.document_id.in_(owned))) or 1
            df = dict(session.execute(select(DocumentTermRow.term, func.count(DocumentTermRow.chunk_id))
                .join(DocumentChunkRow, DocumentTermRow.chunk_id == DocumentChunkRow.id)
                .where(*match).group_by(DocumentTermRow.term)).all())
            frequencies = {}
            for chunk_id, term, frequency in session.execute(select(DocumentTermRow.chunk_id, DocumentTermRow.term, DocumentTermRow.frequency)
                    .where(DocumentTermRow.chunk_id.in_(candidates), DocumentTermRow.term.in_(terms))):
                frequencies.setdefault(chunk_id, {})[term] = frequency
            scored = []
            for chunk, document in session.execute(select(DocumentChunkRow, DocumentRow).join(DocumentRow)
                    .where(DocumentChunkRow.id.in_(candidates), *self._scope(p))):
                score = sum(math.log(1 + (total - df[term] + .5) / (df[term] + .5)) *
                    frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * max(chunk.token_count, 1) / 280))
                    for term, frequency in frequencies[chunk.id].items())
                scored.append((score, chunk, document))
            scored.sort(key=lambda item: (-item[0], item[1].position, item[1].id))
            # A 10k-character evidence budget bounds model context and response latency.
            result, size = [], 0
            for score, chunk, document in scored:
                if len(result) >= limit or size + len(chunk.text) > 10_000:
                    break
                result.append(_source(chunk, document, score=score))
                size += len(chunk.text)
            return result
