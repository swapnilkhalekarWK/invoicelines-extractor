#!/usr/bin/env python3
"""Local MVP web app for reviewing invoice-line extraction output."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sqlite3
import traceback
import urllib.parse
import uuid
import warnings
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="'cgi' is deprecated.*", category=DeprecationWarning)
import cgi

from extract_invoice_lines import extract_invoice_lines, parse_ocr_words, reconstruct_rows
from openai_agent import invoke_analysis_agent


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "mvp.sqlite3"
CONFIDENCE_THRESHOLD = 0.97


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)


def get_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS uploaded_files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                content_text TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                status TEXT NOT NULL,
                min_confidence REAL NOT NULL,
                line_count INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS future_upgrades (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                llm_response TEXT NOT NULL,
                fix_proposal TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(file_id) REFERENCES uploaded_files(id)
            );
            """
        )


def sanitize_filename(filename: str | None) -> str:
    name = filename or "ocr-output.txt"
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "ocr-output.txt"


def row_to_file_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "filename": row["filename"],
        "uploaded_at": row["uploaded_at"],
        "status": row["status"],
        "min_confidence": row["min_confidence"],
        "line_count": row["line_count"],
        "error": row["error"],
    }


def row_to_file_detail(row: sqlite3.Row) -> dict[str, Any]:
    detail = row_to_file_summary(row)
    detail["lines"] = json.loads(row["result_json"])
    detail["diagnostics"] = json.loads(row["diagnostics_json"])
    return detail


def list_files() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, uploaded_at, status, min_confidence, line_count, error
            FROM uploaded_files
            ORDER BY uploaded_at DESC
            """
        ).fetchall()
    return [row_to_file_summary(row) for row in rows]


def get_file(file_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM uploaded_files WHERE id = ?",
            (file_id,),
        ).fetchone()


def save_uploaded_file(filename: str, content: bytes) -> dict[str, Any]:
    file_id = str(uuid.uuid4())
    safe_name = sanitize_filename(filename)
    stored_path = UPLOAD_DIR / f"{file_id}_{safe_name}"
    stored_path.write_bytes(content)
    content_text = content.decode("utf-8-sig", errors="replace")

    extraction = run_extraction(stored_path)
    lines = extraction["lines"]
    min_confidence = extraction["min_confidence"]
    status = "extracted" if lines and min_confidence >= CONFIDENCE_THRESHOLD else "needs_review"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO uploaded_files (
                id, filename, stored_path, content_text, uploaded_at, status,
                min_confidence, line_count, result_json, diagnostics_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                safe_name,
                str(stored_path),
                content_text,
                utc_now(),
                status,
                min_confidence,
                len(lines),
                json.dumps(lines, ensure_ascii=False),
                json.dumps(extraction["diagnostics"], ensure_ascii=False),
                extraction["error"],
            ),
        )

    saved = get_file(file_id)
    if saved is None:
        raise RuntimeError("Could not reload uploaded file record.")
    return row_to_file_detail(saved)


def run_extraction(path: Path) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "threshold": CONFIDENCE_THRESHOLD,
        "rows": [],
        "layout": None,
    }

    try:
        words = parse_ocr_words(path)
        rows = reconstruct_rows(words)
        lines, layout = extract_invoice_lines(rows)
        confidences = [float(line.get("confidence", 0.0)) for line in lines]
        min_confidence = min(confidences) if confidences else 0.0

        diagnostics["word_count"] = len(words)
        diagnostics["row_count"] = len(rows)
        diagnostics["rows"] = [
            {"index": row.index, "page": row.page, "center_y": row.center_y, "text": row.text}
            for row in rows
        ]
        diagnostics["layout"] = {
            "header_row_index": layout.header_row_index,
            "page": layout.page,
            "anchors": layout.anchors,
            "explicit_anchors": sorted(layout.explicit_anchors),
            "has_tax_code": layout.has_tax_code,
        }

        return {
            "lines": lines,
            "min_confidence": min_confidence,
            "diagnostics": diagnostics,
            "error": None,
        }
    except Exception as exc:
        diagnostics["traceback"] = traceback.format_exc(limit=8)
        return {
            "lines": [],
            "min_confidence": 0.0,
            "diagnostics": diagnostics,
            "error": str(exc),
        }


def save_future_upgrade(file_id: str, llm_response: str, fix_proposal: str) -> dict[str, Any]:
    file_row = get_file(file_id)
    if file_row is None:
        raise KeyError("Uploaded file not found.")

    upgrade_id = str(uuid.uuid4())
    created_at = utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO future_upgrades (
                id, file_id, filename, llm_response, fix_proposal, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (upgrade_id, file_id, file_row["filename"], llm_response, fix_proposal, created_at),
        )

    return {
        "id": upgrade_id,
        "file_id": file_id,
        "filename": file_row["filename"],
        "llm_response": llm_response,
        "fix_proposal": fix_proposal,
        "created_at": created_at,
    }


def list_future_upgrades() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, file_id, filename, llm_response, fix_proposal, created_at
            FROM future_upgrades
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


class AppHandler(BaseHTTPRequestHandler):
    server_version = "InvoiceLinesMVP/0.1"

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.send_static_file(STATIC_DIR / "index.html")
        elif path.startswith("/static/"):
            self.send_static_file(STATIC_DIR / path.removeprefix("/static/"))
        elif path == "/api/files":
            self.send_json({"files": list_files()})
        elif path.startswith("/api/files/"):
            file_id = path.split("/")[-1]
            row = get_file(file_id)
            if row is None:
                self.send_error_json(404, "Uploaded file not found.")
                return
            self.send_json({"file": row_to_file_detail(row)})
        elif path == "/api/future-upgrades":
            self.send_json({"upgrades": list_future_upgrades()})
        else:
            self.send_error_json(404, "Not found.")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/files":
            self.handle_upload()
        elif path.startswith("/api/files/") and path.endswith("/understand"):
            file_id = path.split("/")[-2]
            self.handle_understand(file_id)
        elif path == "/api/future-upgrades":
            self.handle_save_upgrade()
        else:
            self.send_error_json(404, "Not found.")

    def handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error_json(400, "Upload must use multipart/form-data.")
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        file_item = form["file"] if "file" in form else None
        if isinstance(file_item, list):
            file_item = file_item[0] if file_item else None
        if file_item is None or not getattr(file_item, "filename", None):
            self.send_error_json(400, "Choose an OCR file to upload.")
            return

        content = file_item.file.read()
        if not content:
            self.send_error_json(400, "Uploaded file is empty.")
            return

        try:
            detail = save_uploaded_file(file_item.filename, content)
            self.send_json({"file": detail}, status=201)
        except Exception as exc:
            self.send_error_json(500, str(exc))

    def handle_understand(self, file_id: str) -> None:
        file_row = get_file(file_id)
        if file_row is None:
            self.send_error_json(404, "Uploaded file not found.")
            return

        try:
            analysis = invoke_analysis_agent(
                file_row,
                confidence_threshold=CONFIDENCE_THRESHOLD,
            )
            self.send_json({"analysis": analysis})
        except Exception as exc:
            self.send_error_json(503, str(exc))

    def handle_save_upgrade(self) -> None:
        try:
            body = self.read_json_body()
            file_id = str(body.get("file_id", ""))
            llm_response = str(body.get("llm_response", "")).strip()
            fix_proposal = str(body.get("fix_proposal", "")).strip()
            if not file_id or not llm_response:
                self.send_error_json(400, "file_id and llm_response are required.")
                return
            saved = save_future_upgrade(file_id, llm_response, fix_proposal)
            self.send_json({"upgrade": saved}, status=201)
        except KeyError as exc:
            self.send_error_json(404, str(exc))
        except Exception as exc:
            self.send_error_json(500, str(exc))

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_static_file(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_DIR.resolve())) or not resolved.is_file():
            self.send_error_json(404, "Static file not found.")
            return

        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        data = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the invoice-line extraction MVP web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Invoice Lines MVP running at http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
