"""Versioned, allowlisted domain serialization. No pickle or dynamic imports.

The aggregate retains shared mandate identity: a policy and its payment legs
must consume the same authorization budget after a restart.
"""
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .. import dates, models, money
from ..engine import tax
from ..execution import engine

TYPES = {}
for module in (dates, models, money, tax, engine):
    for name, value in vars(module).items():
        if isinstance(value, type) and (is_dataclass(value) or issubclass(value, Enum)):
            TYPES[value.__module__ + "." + value.__name__] = value


def encode(value):
    seen = {}

    def visit(v):
        if isinstance(v, Enum):
            return {"$enum": v.__class__.__module__ + "." + v.__class__.__name__, "value": v.value}
        if isinstance(v, Decimal):
            return {"$decimal": str(v)}
        if isinstance(v, datetime):
            return {"$datetime": v.isoformat()}
        if isinstance(v, date):
            return {"$date": v.isoformat()}
        if isinstance(v, dates.Calendar):
            return {"$calendar": v.jurisdiction}
        if is_dataclass(v):
            if id(v) in seen:
                return {"$ref": seen[id(v)]}
            key = str(len(seen))
            seen[id(v)] = key
            return {"$type": v.__class__.__module__ + "." + v.__class__.__name__,
                    "$id": key, "fields": {f.name: visit(getattr(v, f.name)) for f in fields(v)}}
        if isinstance(v, dict):
            return {str(k): visit(item) for k, item in v.items()}
        if isinstance(v, (set, tuple)):
            return {"$collection": type(v).__name__, "items": [visit(x) for x in v]}
        if isinstance(v, list):
            return [visit(x) for x in v]
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        raise TypeError(f"Unsupported persisted type: {type(v).__name__}")

    return {"schema_version": 1, "data": visit(value)}


def decode(payload):
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported household schema version; run migrations")
    seen = {}

    def visit(v):
        if isinstance(v, list):
            return [visit(x) for x in v]
        if not isinstance(v, dict):
            return v
        if "$ref" in v:
            return seen[v["$ref"]]
        if "$decimal" in v:
            return Decimal(v["$decimal"])
        if "$datetime" in v:
            return datetime.fromisoformat(v["$datetime"])
        if "$date" in v:
            return date.fromisoformat(v["$date"])
        if "$calendar" in v:
            return dates.Calendar(v["$calendar"])
        if "$enum" in v:
            return TYPES[v["$enum"]](v["value"])
        if "$collection" in v:
            return {"set": set, "tuple": tuple}[v["$collection"]](visit(v["items"]))
        if "$type" in v:
            cls = TYPES[v["$type"]]
            values = {k: visit(x) for k, x in v["fields"].items()}
            obj = cls(**{f.name: values[f.name] for f in fields(cls) if f.init and f.name in values})
            for f in fields(cls):
                if not f.init and f.name in values:
                    object.__setattr__(obj, f.name, values[f.name])
            seen[v["$id"]] = obj
            return obj
        return {k: visit(x) for k, x in v.items()}

    return visit(payload["data"])
