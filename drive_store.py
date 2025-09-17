import io, json, threading
from contextlib import contextmanager
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import streamlit as st

_SCOPES = ["https://www.googleapis.com/auth/drive"]

def _drive():
    # 1) Voorkeur: TOML subtable [drive.service_account] => dict
    sa = st.secrets["drive"].get("service_account")
    if isinstance(sa, dict):
        info = dict(sa)
    else:
        # 2) Back-up: JSON-string in SERVICE_ACCOUNT_JSON
        raw = st.secrets["drive"].get("SERVICE_ACCOUNT_JSON")
        if raw is None:
            raise RuntimeError(
                "Service-account ontbreekt. Zet óf [drive.service_account] (TOML) óf SERVICE_ACCOUNT_JSON (JSON) in Secrets."
            )
        if isinstance(raw, dict):
            info = dict(raw)
        elif isinstance(raw, str):
            s = raw.lstrip("\ufeff").strip()
            try:
                info = json.loads(s)
            except Exception as e:
                raise RuntimeError(
                    "SERVICE_ACCOUNT_JSON is geen geldige JSON. "
                    "Tip: gebruik liever [drive.service_account] als TOML-subtable met echte regeleinden in private_key."
                ) from e
        else:
            raise RuntimeError("Onbekend type voor service-account secrets.")

    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)



def download_db(local_path: str) -> dict:
    """Download DB uit Drive naar local_path. Returnt metadata incl. headRevisionId."""
    file_id = st.secrets["drive"]["DB_FILE_ID"]
    svc = _drive()
    meta = svc.files().get(
        fileId=file_id,
        fields="id,name,mimeType,md5Checksum,headRevisionId"
    ).execute()

    req = svc.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()

    return meta

def upload_db(local_path: str, expect_head_rev: str) -> dict:
    """Upload lokale DB terug naar Drive met revision-check (optimistic concurrency)."""
    file_id = st.secrets["drive"]["DB_FILE_ID"]
    svc = _drive()

    # Check of remote niet intussen is gewijzigd:
    now = svc.files().get(fileId=file_id, fields="headRevisionId").execute()
    if now.get("headRevisionId") != expect_head_rev:
        raise RuntimeError("De database is intussen elders gewijzigd. Herlaad en probeer opnieuw.")

    media = MediaIoBaseUpload(open(local_path, "rb"), mimetype="application/octet-stream", resumable=True)
    updated = svc.files().update(fileId=file_id, media_body=media).execute()
    return updated

# Eenvoudige proces-lock binnen deze app-instance (niet cross-instance).
_lock = threading.Lock()

@contextmanager
def exclusive_writer():
    if not _lock.acquire(timeout=30):
        raise RuntimeError("Kon geen schrijflock krijgen (time-out).")
    try:
        yield
    finally:
        _lock.release()
