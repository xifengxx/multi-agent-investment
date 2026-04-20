"""上传路由：POST /api/upload。"""

from __future__ import annotations

import hashlib
import json
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from app.config import AppConfig
from ingestion.file_scanner import FILE_NAME_PATTERN
from storage.db import init_db, open_sqlite_connection
from storage.repositories.job_repository import JobRepository
from storage.repositories.run_repository import RunRepository
from storage.repositories.snapshot_repository import SnapshotRepository


def _sqlite_now_iso(conn) -> str:
    """通过 SQLite 取 CURRENT_TIMESTAMP，并转换为 ISO 风格字符串（UTC，秒级）。"""
    row = conn.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()
    value = str(row["now"]).strip() if row is not None else ""
    if not value:
        return "1970-01-01T00:00:00+00:00"
    return value.replace(" ", "T") + "+00:00"


def _write_file_and_sha256(*, dst_path: Path, src_file) -> str:
    """将上传文件流写入目标路径，并返回 SHA256。"""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with dst_path.open("wb") as out:
        for chunk in iter(lambda: src_file.read(8192), b""):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()

def _write_bytes_and_sha256(*, dst_path: Path, content: bytes) -> str:
    """将二进制内容写入目标路径，并返回 SHA256。"""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    dst_path.write_bytes(content)
    return digest


def _parse_snapshot_date(file_name: str) -> tuple[str, str]:
    """解析文件名并返回 (snapshot_date, kind)，不匹配则抛 ValueError。"""
    matched = FILE_NAME_PATTERN.match(file_name)
    if not matched:
        raise ValueError(f"invalid_file_name:{file_name}")
    return matched.group("date"), matched.group("kind").lower()

def _extract_boundary(content_type: str) -> str:
    """从 Content-Type 中提取 multipart boundary。"""
    for part in content_type.split(";"):
        seg = part.strip()
        if not seg.lower().startswith("boundary="):
            continue
        boundary = seg.split("=", 1)[1].strip()
        if boundary.startswith('"') and boundary.endswith('"') and len(boundary) >= 2:
            boundary = boundary[1:-1]
        return boundary
    raise ValueError("bad_request:missing_boundary")


def _parse_content_disposition(value: str) -> tuple[str, str]:
    """解析 Content-Disposition，返回 (name, filename)。"""
    name = ""
    filename = ""
    for part in value.split(";"):
        seg = part.strip()
        if seg.lower().startswith("name="):
            v = seg.split("=", 1)[1].strip()
            if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                v = v[1:-1]
            name = v
        if seg.lower().startswith("filename="):
            v = seg.split("=", 1)[1].strip()
            if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                v = v[1:-1]
            filename = v
    return name, filename


def _parse_multipart_formdata(*, content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """解析 multipart/form-data，返回 name -> (filename, content)。

    约定：仅支持表单字段为文件（含 filename）的场景。
    """
    boundary = _extract_boundary(content_type)
    delimiter = (f"--{boundary}").encode("utf-8")
    parts = body.split(delimiter)
    files: dict[str, tuple[str, bytes]] = {}
    for part in parts:
        if not part:
            continue
        if part.startswith(b"--"):
            continue
        part = part.strip(b"\r\n")
        if not part:
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_blob, content = part.split(b"\r\n\r\n", 1)
        header_lines = header_blob.decode("latin-1").split("\r\n")
        headers: dict[str, str] = {}
        for line in header_lines:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        name, filename = _parse_content_disposition(disp)
        if not name or not filename:
            continue
        files[name] = (filename, content)
    return files


def _send_json(handler: BaseHTTPRequestHandler, *, status: int, payload: dict) -> None:
    """发送 JSON 响应。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _send_text(handler: BaseHTTPRequestHandler, *, status: int, text: str) -> None:
    """发送纯文本响应。"""
    raw = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def handle_upload(handler: BaseHTTPRequestHandler, *, config: AppConfig) -> None:
    """处理上传：保存文件、写 runs/file_batches/jobs，并返回 JSON。"""
    content_type = (handler.headers.get("Content-Type") or "").strip()
    if "multipart/form-data" not in content_type:
        _send_text(handler, status=400, text="bad_request:expected_multipart")
        return
    content_length_raw = (handler.headers.get("Content-Length") or "").strip()
    if not content_length_raw:
        _send_text(handler, status=411, text="bad_request:missing_content_length")
        return
    try:
        content_length = int(content_length_raw)
    except ValueError:
        _send_text(handler, status=400, text="bad_request:invalid_content_length")
        return
    if content_length <= 0:
        _send_text(handler, status=400, text="bad_request:invalid_content_length")
        return

    body = handler.rfile.read(content_length)
    try:
        files = _parse_multipart_formdata(content_type=content_type, body=body)
    except ValueError as exc:
        _send_text(handler, status=400, text=str(exc))
        return

    if "stock_file" not in files or "etf_file" not in files:
        _send_text(handler, status=400, text="bad_request:missing_files")
        return

    stock_name, stock_content = files["stock_file"]
    etf_name, etf_content = files["etf_file"]
    if not stock_name or not etf_name:
        _send_text(handler, status=400, text="bad_request:empty_filenames")
        return

    try:
        stock_date, stock_kind = _parse_snapshot_date(stock_name)
        etf_date, etf_kind = _parse_snapshot_date(etf_name)
    except ValueError as exc:
        _send_text(handler, status=400, text=str(exc))
        return

    if stock_kind != "stock" or etf_kind != "etf":
        _send_text(handler, status=400, text="bad_request:invalid_kinds")
        return
    if stock_date != etf_date:
        _send_text(handler, status=400, text="bad_request:snapshot_date_mismatch")
        return

    upload_id = f"upload-{uuid.uuid4().hex}"
    run_id = f"run-{uuid.uuid4().hex}"
    snapshot_date = stock_date

    data_root = Path(config.data_root)
    inbox_dir = data_root / "inbox" / upload_id
    stock_dst = inbox_dir / f"{snapshot_date}_stock.xlsx"
    etf_dst = inbox_dir / f"{snapshot_date}_etf.xlsx"

    stock_sha = _write_bytes_and_sha256(dst_path=stock_dst, content=stock_content)
    etf_sha = _write_bytes_and_sha256(dst_path=etf_dst, content=etf_content)

    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)
        now_iso = _sqlite_now_iso(conn)
        RunRepository(conn).create_run(
            run_id=run_id,
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at=now_iso,
        )
        SnapshotRepository(conn).create_file_batch(
            run_id=run_id,
            snapshot_date=snapshot_date,
            stock_file=str(stock_dst),
            etf_file=str(etf_dst),
            stock_sha256=stock_sha,
            etf_sha256=etf_sha,
        )
        job_id = JobRepository(conn).enqueue(run_id=run_id, snapshot_date=snapshot_date)
    finally:
        conn.close()

    base_url = str(getattr(config, "reports_base_url", "") or "").strip().rstrip("/")
    reports_url = f"{base_url}/reports/{snapshot_date}/{run_id}/"
    summary_url = f"{base_url}/reports/{snapshot_date}/{run_id}/summary.md"

    _send_json(
        handler,
        status=200,
        payload={
            "run_id": run_id,
            "snapshot_date": snapshot_date,
            "job_id": job_id,
            "reports_url": reports_url,
            "summary_url": summary_url,
        },
    )
