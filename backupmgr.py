# backupmgr.py
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional, Callable

class BackupManager:
    """
    SQLite back-upmanager:
    - Maakt een consistente back-up met sqlite3 Connection.backup(...)
    - Slaat op in <db_dir>/backup/vakantierooster_YYYYMMDD_HHMMSS.db
    - Verwijdert back-ups ouder dan 'retention_days'
    - Rate-limit via 'min_interval_sec'
    - Koppelt zichzelf aan een SQLAlchemy Session via after_commit
    """

    def __init__(self, db_path: str, retention_days: int = 14, min_interval_sec: int = 120):
        self.db_path = db_path
        self.retention_days = retention_days
        self.min_interval_sec = min_interval_sec
        self._last_run = 0.0
        self._attached_session = None

    # ---------- helpers ----------
    def _backup_dir(self) -> str:
        base_dir = os.path.dirname(self.db_path)
        bdir = os.path.join(base_dir, "backup")
        os.makedirs(bdir, exist_ok=True)
        return bdir

    def _now(self) -> datetime:
        return datetime.now()

    def _backup_filename(self) -> str:
        ts = self._now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(self._backup_dir(), f"vakantierooster_{ts}.db")

    def _should_run(self) -> bool:
        return (time.time() - self._last_run) >= self.min_interval_sec

    def _rotate(self):
        """Verwijder back-ups ouder dan retention_days."""
        cutoff = self._now() - timedelta(days=self.retention_days)
        bdir = self._backup_dir()
        for name in os.listdir(bdir):
            if not name.lower().endswith(".db"):
                continue
            fp = os.path.join(bdir, name)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    os.remove(fp)
            except Exception:
                # stil falen – back-up opruimen is best effort
                pass

    # ---------- core ----------
    def run_backup_now(self) -> Optional[str]:
        """
        Forceer een back-up (negeert rate-limit).
        Retourneert pad van back-upbestand of None bij mislukking.
        """
        try:
            dest = self._backup_filename()
            # Open bron en doel; copy via sqlite backup API
            src_conn = sqlite3.connect(self.db_path)
            dst_conn = sqlite3.connect(dest)
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()

            # rotate
            self._rotate()
            self._last_run = time.time()
            return dest
        except Exception:
            return None

    def maybe_backup_after_commit(self):
        """Back-up uitvoeren als de rate-limit het toelaat."""
        if not self._should_run():
            return
        self.run_backup_now()

    # ---------- SQLAlchemy integratie ----------
    def attach_to_session(self, session):
        """
        Koppel aan een SQLAlchemy Session; back-up na iedere commit.
        Je kunt dit veilig meerdere keren doen; vorige attach wordt losgekoppeld.
        """
        from sqlalchemy import event

        # loskoppelen oude
        if self._attached_session is not None:
            try:
                event.remove(self._attached_session, "after_commit", self._after_commit_handler)
            except Exception:
                pass
            self._attached_session = None

        self._attached_session = session

        # define handler als bound method zodat event.remove exact dezelfde ref kan krijgen
        def _handler(ses):
            # Alleen back-uppen als dit een SQLite pad is
            # (Wanneer je ooit een server-DB gebruikt, kun je deze manager overslaan of vervangen)
            if self.db_path and os.path.isfile(self.db_path):
                self.maybe_backup_after_commit()

        self._after_commit_handler = _handler
        event.listen(session, "after_commit", self._after_commit_handler)
