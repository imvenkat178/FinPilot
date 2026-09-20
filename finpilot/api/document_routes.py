"""Session-authenticated private source library endpoints."""
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from ..services.documents import DocumentService, MAX_BYTES
from .dependencies import P, R

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get("")
def list_documents(p: P, r: R):
    return {"documents": DocumentService(r.db).list(p)}


@router.post("", status_code=201)
def upload_document(p: P, r: R, file: UploadFile = File(...)):
    r.auth.throttle("documents:" + p.user_id, limit=10, seconds=60)
    try:
        content = file.file.read(MAX_BYTES + 1)
    finally:
        file.file.close()
    return DocumentService(r.db).ingest_file(p, file.filename or "", content)


# The question travels in the JSON body so it never appears in URLs or request logs.
class DocumentSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=4000)
    document_id: list[str] | None = Field(None, max_length=100)
    limit: int = Field(6, ge=1, le=8)


@router.post("/search")
def search_documents(body: DocumentSearch, p: P, r: R):
    return {"sources": DocumentService(r.db).retrieve(p, body.q, body.document_id, body.limit)}


@router.get("/{document_id}")
def document_detail(document_id: str, p: P, r: R):
    return DocumentService(r.db).detail(p, document_id)


@router.delete("/{document_id}")
def delete_document(document_id: str, p: P, r: R):
    return {"deleted": DocumentService(r.db).delete(p, document_id)}
