from __future__ import annotations

import os, json, configparser
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, Date, ForeignKey, Index, UniqueConstraint, create_engine, Float, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

# ---------- Base ----------
Base = declarative_base()

# ---------- Tabellen ----------

class Role(Base):
    __tablename__ = "role"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    min_required_per_day = Column(Integer, default=0)
    max_allowed_per_day = Column(Integer, default=999)
    resources = relationship("Resource", back_populates="role", cascade="all, delete-orphan")

class Resource(Base):
    __tablename__ = "resource"
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, default="")
    role_id = Column(Integer, ForeignKey("role.id"), nullable=False)
    role = relationship("Role", back_populates="resources")
    fixed_off_days = relationship(
        "FixedOffDay",
        back_populates="resource",
        cascade="all, delete-orphan"
    )
    vacations = relationship("Vacation", back_populates="resource", cascade="all, delete-orphan")
    @property
    def full_name(self) -> str:
        fn = self.first_name or ""
        ln = self.last_name or ""
        return (fn + " " + ln).strip()

class FixedOffDay(Base):
    __tablename__ = "fixed_off_day"
    id = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey("resource.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=ma .. 6=zo

    # NIEUW: type vaste vrije dag
    part = Column(String(8), nullable=False, default="FULL")   # FULL | AM | PM
    absence_fraction = Column(Float, nullable=False, default=1.0)

    # 🔧 BELANGRIJK: spiegelrelatie voor back_populates
    resource = relationship("Resource", back_populates="fixed_off_days")

    def code(self) -> str:
        return {"FULL": "VV", "AM": "VO", "PM": "VM"}.get((self.part or "FULL").upper(), "VV")

class PublicHoliday(Base):
    __tablename__ = "public_holiday"
    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True, nullable=False)
    name = Column(String, nullable=False)

class LeaveCode(Base):
    __tablename__ = "leave_code"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False)
    color_hex = Column(String, default="#C6E6C6")
    counts_as_absent = Column(Boolean, default=True)
    absence_fraction = Column(Float, default=1.0)

class Vacation(Base):
    __tablename__ = "vacation"
    id = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey("resource.id"), nullable=False)
    date = Column(Date, nullable=False)
    code = Column(String, nullable=False)
    resource = relationship("Resource", back_populates="vacations")
    __table_args__ = (UniqueConstraint("resource_id", "date", name="uq_vacation_res_date"),)

class FixedOffException(Base):
    """
    Eenmalige uitzondering op vaste vrije dag:
    - part: "NONE"  => vaste vrije dag (weekpatroon) wordt voor deze datum uitgeschakeld
    - part: "FULL"  => eenmalig vaste vrije dag (hele dag)
    - part: "AM"    => eenmalig vaste vrije ochtend
    - part: "PM"    => eenmalig vaste vrije middag
    """
    __tablename__ = "fixed_off_exception"
    id = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey("resource.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    part = Column(String(8), nullable=False, default="NONE")

    __table_args__ = (
        UniqueConstraint("resource_id", "date", name="uq_fixedoff_exception_res_date"),
    )

# ---------- Indexes ----------
Index("ix_vacation_date", Vacation.date)
Index("ix_vacation_res_date", Vacation.resource_id, Vacation.date)
Index("ix_fixedoff_res", FixedOffDay.resource_id)
Index("ix_fixedoffex_res_date", FixedOffException.resource_id, FixedOffException.date)


# ---------- Engine & Session helpers ----------
_DEF_DB_PATH = Path(__file__).resolve().parent / "vakantierooster.db"
_SETTINGS = os.path.join(os.path.dirname(__file__), "settings.json")
_current_db_url = None  # wordt gezet in get_engine()

def _db_url(db_path: Optional[str] = None) -> str:
    if db_path:
        return f"sqlite:///{db_path}"
    return f"sqlite:///{_DEF_DB_PATH}"

def _load_settings_url() -> Optional[str]:
    if os.path.exists(_SETTINGS):
        try:
            with open(_SETTINGS, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = (data or {}).get("database_url", "").strip()
            return url or None
        except Exception:
            return None
    return None

def set_database_url_persisted(url: str):
    """Schrijf database-URL naar settings.json (gebruikt door DatabasePathDialog)."""
    data = {}
    if os.path.exists(_SETTINGS):
        try:
            with open(_SETTINGS, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    data["database_url"] = url
    with open(_SETTINGS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_current_db_url() -> str:
    return _current_db_url or ""

def sqlite_path_from_url(url: str) -> str:
    """
    Haal bij sqlite-URL's het filesystem-pad op.
    - sqlite:///C:/pad/file.db → C:/pad/file.db
    - sqlite://///server/share/file.db → //server/share/file.db (UNC)
    Anders: return "".
    """
    if not url or not url.startswith("sqlite:"):
        return ""
    # strip 'sqlite:'
    rest = url[len("sqlite:"):]
    # rest begint met /// of //// (UNC)
    # Zet naar OS-pad (Windows snapt //server/share/...)
    return rest.replace("\\", "/")

def get_engine(db_path: Optional[str] = None):
    """
    Volgorde:
    1) settings.json -> database_url
    2) config.ini     -> [database] url
    3) fallback SQLite naast de app
    """
    global _current_db_url

    # 1) settings.json
    url = _load_settings_url()
    if not url:
        # 2) config.ini (compat)
        cfg_path = os.path.join(os.path.dirname(__file__), "config.ini")
        if os.path.exists(cfg_path):
            cp = configparser.ConfigParser()
            cp.read(cfg_path, encoding="utf-8")
            if cp.has_option("database", "url"):
                url = cp.get("database", "url").strip()

    # 3) fallback
    if not url:
        url = _db_url(db_path)

    _current_db_url = url

    # Engine
    engine = create_engine(url, future=True, pool_pre_ping=True)

    # SQLite pragmas
    if url.startswith("sqlite:"):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
                conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass

    return engine

def get_session(engine=None) -> Session:
    engine = engine or get_engine()
    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()
