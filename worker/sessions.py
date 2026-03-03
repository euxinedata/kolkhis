import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field

import duckdb

from executor import setup_connection


@dataclass
class Session:
    id: str
    conn: duckdb.DuckDBPyConnection
    lock: threading.Lock
    temp_dir: str
    created_at: float
    last_used_at: float


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(
        self,
        catalog_objects: list[dict],
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
        s3_region: str,
    ) -> str:
        session_id = uuid.uuid4().hex
        temp_dir = os.path.join(tempfile.gettempdir(), f".session_{session_id}")
        os.makedirs(temp_dir, exist_ok=True)

        conn = duckdb.connect()
        conn.execute(f"SET temp_directory='{temp_dir}'")
        if os.path.isdir("/opt/kolkhis-worker"):
            conn.execute("SET home_directory='/opt/kolkhis-worker'")

        setup_connection(
            conn, catalog_objects,
            s3_endpoint, s3_access_key, s3_secret_key, s3_region,
        )

        now = time.time()
        session = Session(
            id=session_id,
            conn=conn,
            lock=threading.Lock(),
            temp_dir=temp_dir,
            created_at=now,
            last_used_at=now,
        )

        with self._lock:
            self._sessions[session_id] = session

        return session_id

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def execute(self, session_id: str, sql: str, fetch_results: bool = True) -> dict:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")

        with session.lock:
            session.last_used_at = time.time()
            try:
                result = session.conn.execute(sql)
                if fetch_results:
                    columns = [
                        {"name": desc[0], "type": str(desc[1])}
                        for desc in result.description
                    ]
                    rows = [list(row) for row in result.fetchall()]
                    return {
                        "status": "completed",
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                    }
                else:
                    row_count = result.fetchone()
                    return {
                        "status": "completed",
                        "columns": None,
                        "rows": None,
                        "row_count": row_count[0] if row_count else 0,
                    }
            except Exception as exc:
                return {"status": "failed", "error": str(exc)}

    def keepalive(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        session.last_used_at = time.time()
        return True

    def close(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        try:
            session.conn.close()
        except Exception:
            pass
        shutil.rmtree(session.temp_dir, ignore_errors=True)
        return True

    def cleanup_expired(self, max_idle_seconds: int = 1800) -> int:
        now = time.time()
        expired: list[str] = []
        with self._lock:
            for sid, session in self._sessions.items():
                if now - session.last_used_at > max_idle_seconds:
                    expired.append(sid)

        count = 0
        for sid in expired:
            if self.close(sid):
                count += 1
        return count


session_manager = SessionManager()
