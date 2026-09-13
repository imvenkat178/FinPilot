"""Reject recognizable credentials before prompts, proposals or conversation storage."""
import re
_SECRET_KEY = re.compile(r"^(?:password|passwd|api[_ -]?key|secret|access[_ -]?token|refresh[_ -]?token|authorization|cookie|private[_ -]?key)$", re.I)
_SECRET_TEXT = re.compile(r"(?:\b(?:password|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret)\s*[:=]\s*\S+|\bBearer\s+[A-Za-z0-9._~-]{10,}|-----BEGIN .*PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,})", re.I)
def reject_credentials(value):
    if isinstance(value, dict):
        if any(_SECRET_KEY.fullmatch(str(k)) for k in value):
            raise ValueError("Enter credentials in the secure connection form, outside chat.")
        for child in value.values():
            reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child)
    elif isinstance(value, str) and _SECRET_TEXT.search(value):
        raise ValueError("Enter credentials in the secure connection form, outside chat.")
