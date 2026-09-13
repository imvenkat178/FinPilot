"""Session-authenticated private source library endpoints."""
from fastapi import APIRouter, File, Query, UploadFile
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


@router.get("/search")
def search_documents(p: P, r: R, q: str = Query(min_length=1, max_length=4000),
                     document_id: list[str] | None = Query(default=None), limit: int = Query(6, ge=1, le=8)):
    return {"sources": DocumentService(r.db).retrieve(p, q, document_id, limit)}


@router.get("/{document_id}")
def document_detail(document_id: str, p: P, r: R):
    return DocumentService(r.db).detail(p, document_id)


@router.delete("/{document_id}")
def delete_document(document_id: str, p: P, r: R):
    return {"deleted": DocumentService(r.db).delete(p, document_id)}
