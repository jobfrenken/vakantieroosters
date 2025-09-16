# lockmgr.py
import os, socket, getpass, json, time
import portalocker  # pip install portalocker

LOCK_FILENAME = "vakantierooster.lock"

def _lock_path_from_db_file(db_path: str) -> str:
    if not db_path:
        return ""
    folder = os.path.dirname(db_path)
    return os.path.join(folder, LOCK_FILENAME) if folder else ""

def _editor_info() -> dict:
    return {"host": socket.gethostname(), "user": getpass.getuser(), "ts": int(time.time())}

class EditLock:
    """
    Eenvoudige exclusieve lock via een klein .lock-bestand naast de SQLite .db.
    Geschikt voor gedeelde netwerkschijven (SMB). Werkt advisory: alleen onze app respecteert hem.
    """
    def __init__(self, db_file_path: str):
        self.db_file_path = db_file_path
        self.lock_file_path = _lock_path_from_db_file(db_file_path)
        self._fh = None

    def acquire(self) -> bool:
        """Probeer exclusieve lock te nemen. True bij succes, False als al bezet of geen pad."""
        if not self.lock_file_path:
            return True  # niets te locken
        os.makedirs(os.path.dirname(self.lock_file_path), exist_ok=True)
        try:
            self._fh = open(self.lock_file_path, "a+")
            portalocker.lock(self._fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
            self._fh.seek(0); self._fh.truncate()
            self._fh.write(json.dumps(_editor_info()))
            self._fh.flush(); os.fsync(self._fh.fileno())
            return True
        except portalocker.exceptions.LockException:
            if self._fh:
                try: self._fh.close()
                except Exception: pass
                self._fh = None
            return False

    def release(self):
        if not self.lock_file_path:
            return
        try:
            if self._fh:
                portalocker.unlock(self._fh)
                self._fh.close()
                self._fh = None
            try:
                os.remove(self.lock_file_path)
            except OSError:
                pass
        except Exception:
            pass

    def holder(self) -> str:
        """Geef 'HOST\\user' terug van de huidige lockhouder, of ''."""
        p = self.lock_file_path
        if not p or not os.path.exists(p):
            return ""
        try:
            with open(p, "r") as f:
                data = json.loads(f.read() or "{}")
            return f"{data.get('host','?')}\\{data.get('user','?')}"
        except Exception:
            return ""

    def is_locked(self) -> bool:
        """True als lock bestaat en niet door ons te nemen is."""
        p = self.lock_file_path
        if not p:
            return False
        try:
            fh = open(p, "a+")
            portalocker.lock(fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
            portalocker.unlock(fh); fh.close()
            return False
        except portalocker.exceptions.LockException:
            return True
