"""Connections to *monitored* MongoDB instances (never the internal DB)."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.uri_parser import parse_uri

from app.config import get_settings
from app.core.encryption import decrypt_secret
from app.core.exceptions import ConnectionFailed, ValidationFailed
from app.utils.redaction import scrub_text

log = logging.getLogger("dbpulse.connection")
SYSTEM_DBS = {"admin", "local", "config"}
CLIENT_APPNAME = "dbpulse-monitor"


def validate_uri(uri: str) -> None:
    """Syntactic validation. Error text never echoes the URI (it may hold credentials)."""
    if not isinstance(uri, str) or not uri.strip():
        raise ValidationFailed("Connection string is required")
    if len(uri) > 2048:
        raise ValidationFailed("Connection string is too long")
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ValidationFailed("Connection string must start with mongodb:// or mongodb+srv://")
    if uri.startswith("mongodb+srv://"):
        return  # SRV validation needs DNS; it is validated when the connection is attempted
    try:
        parse_uri(uri, validate=True, warn=False)
    except (pe.InvalidURI, pe.ConfigurationError, ValueError) as exc:
        raise ValidationFailed(f"Invalid MongoDB connection string: {scrub_text(str(exc))}") from exc


def build_client(uri: str, *, timeout_ms: int | None = None) -> MongoClient:
    s = get_settings()
    t = timeout_ms or s.mongo_server_selection_timeout_ms
    return MongoClient(
        uri,
        serverSelectionTimeoutMS=t,
        connectTimeoutMS=s.mongo_connect_timeout_ms,
        socketTimeoutMS=max(t, s.benchmark_op_timeout_ms + 5000),
        appname=CLIENT_APPNAME,
        tz_aware=True,
        retryReads=False,
        maxPoolSize=max(20, s.benchmark_max_concurrency + 4),
    )


def classify_error(exc: Exception) -> tuple[str, str]:
    """Map a PyMongo exception to (code, safe human message). Never includes the URI."""
    if isinstance(exc, pe.OperationFailure) and exc.code in (18, 13, 11):
        if exc.code == 18:
            return "auth_failed", "Authentication failed: check the username, password and authSource"
        return "not_authorized", "The user is not authorized to run this operation"
    if isinstance(exc, pe.ServerSelectionTimeoutError):
        return "unreachable", "Cannot reach the MongoDB server within the timeout (check host/port/network)"
    if isinstance(exc, pe.AutoReconnect):
        return "unreachable", "Connection to the MongoDB server was lost"
    if isinstance(exc, (pe.ConfigurationError, pe.InvalidURI)):
        return "invalid_config", "Invalid connection configuration"
    if isinstance(exc, pe.ExecutionTimeout):
        return "timeout", "The operation exceeded its time limit"
    if isinstance(exc, pe.OperationFailure):
        return "operation_failed", scrub_text(
            str(exc.details.get("errmsg", exc))[:300] if exc.details else str(exc)[:300]
        )
    return "error", scrub_text(str(exc)[:300])


def test_connection(uri: str, *, timeout_ms: int | None = None) -> dict[str, Any]:
    """Connect, ping, read server version. Always returns a dict (ok true/false)."""
    validate_uri(uri)
    started = time.perf_counter()
    client = build_client(uri, timeout_ms=timeout_ms)
    try:
        client.admin.command("ping")
        info = client.server_info()
        latency = (time.perf_counter() - started) * 1000
        return {
            "ok": True,
            "version": info.get("version"),
            "latency_ms": round(latency, 1),
            "error": None,
            "error_code": None,
        }
    except Exception as exc:
        code, msg = classify_error(exc)
        log.info("connection test failed: %s", code)
        return {"ok": False, "version": None, "latency_ms": None, "error": msg, "error_code": code}
    finally:
        client.close()


class ClientPool:
    """One MongoClient per registered instance, rebuilt if the stored credential changes."""

    def __init__(self) -> None:
        self._clients: dict[str, tuple[str, MongoClient]] = {}
        self._lock = threading.Lock()

    def get(self, instance: dict) -> MongoClient:
        iid = str(instance["_id"])
        token = hashlib.sha256(instance["uri_enc"].encode()).hexdigest()
        with self._lock:
            cached = self._clients.get(iid)
            if cached and cached[0] == token:
                return cached[1]
            if cached:
                cached[1].close()
            client = build_client(decrypt_secret(instance["uri_enc"]))
            self._clients[iid] = (token, client)
            return client

    def evict(self, instance_id: str) -> None:
        with self._lock:
            cached = self._clients.pop(instance_id, None)
        if cached:
            cached[1].close()

    def close_all(self) -> None:
        with self._lock:
            items, self._clients = list(self._clients.values()), {}
        for _, c in items:
            c.close()


pool = ClientPool()


def get_monitored_client(instance: dict) -> MongoClient:
    return pool.get(instance)


def list_databases(client: MongoClient, *, include_system: bool = False) -> list[dict]:
    try:
        rows = client.list_databases()
        return [
            {"name": d["name"], "size_on_disk": d.get("sizeOnDisk"), "empty": d.get("empty")}
            for d in rows
            if include_system or d["name"] not in SYSTEM_DBS
        ]
    except pe.OperationFailure:
        # Users without listDatabases privilege may still be allowed to list their own.
        try:
            names = client.list_database_names()
            return [
                {"name": n, "size_on_disk": None, "empty": None}
                for n in names
                if include_system or n not in SYSTEM_DBS
            ]
        except pe.PyMongoError as exc:
            raise ConnectionFailed(classify_error(exc)[1]) from exc
    except pe.PyMongoError as exc:
        raise ConnectionFailed(classify_error(exc)[1]) from exc


def list_collections(client: MongoClient, db_name: str) -> list[dict]:
    db = client[db_name]
    try:
        specs = list(db.list_collections())
    except pe.PyMongoError as exc:
        raise ConnectionFailed(classify_error(exc)[1]) from exc
    out = []
    for spec in specs:
        name = spec["name"]
        if name.startswith("system."):
            continue
        entry: dict[str, Any] = {
            "name": name,
            "type": spec.get("type", "collection"),
            "document_count": None,
            "size_bytes": None,
            "avg_obj_size": None,
            "index_count": None,
            "total_index_size": None,
            "stats_available": False,
        }
        if entry["type"] == "collection":
            try:
                st = db.command("collStats", name)
                entry.update(
                    document_count=st.get("count"),
                    size_bytes=st.get("size"),
                    avg_obj_size=st.get("avgObjSize"),
                    index_count=st.get("nindexes"),
                    total_index_size=st.get("totalIndexSize"),
                    stats_available=True,
                )
            except pe.PyMongoError:
                log.debug("collStats unavailable for %s.%s", db_name, name, exc_info=True)
        out.append(entry)
    return out
