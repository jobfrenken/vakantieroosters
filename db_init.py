from datetime import date, timedelta
from models import Base, Role, Resource, PublicHoliday, LeaveCode, FixedOffDay, get_session
from sqlalchemy import inspect, text


def _migrate_schema(engine):
    """Lichte, idempotente migraties voor bestaande DB's."""
    with engine.begin() as conn:
        # role: min/max kolommen
        rows = conn.exec_driver_sql("PRAGMA table_info(role)").fetchall()
        colnames = {r[1] for r in rows}
        if "min_required_per_day" not in colnames:
            conn.exec_driver_sql("ALTER TABLE role ADD COLUMN min_required_per_day INTEGER DEFAULT 0")
        if "max_allowed_per_day" not in colnames:
            conn.exec_driver_sql("ALTER TABLE role ADD COLUMN max_allowed_per_day INTEGER DEFAULT 999")

        # leave_code: absence_fraction
        rows = conn.exec_driver_sql("PRAGMA table_info(leave_code)").fetchall()
        colnames = {r[1] for r in rows}
        if "absence_fraction" not in colnames:
            conn.exec_driver_sql("ALTER TABLE leave_code ADD COLUMN absence_fraction REAL DEFAULT 1.0")
            conn.exec_driver_sql(
                "UPDATE leave_code SET counts_as_absent = CASE WHEN IFNULL(absence_fraction,1.0) > 0 THEN 1 ELSE 0 END"
            )

        # indexes
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_vacation_date ON vacation (date)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_vacation_res_date ON vacation (resource_id, date)")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_fixedoff_res ON fixed_off_day (resource_id)")
def _easter_sunday(year: int) -> date:
    """Gregoriaanse berekening van Pasen (Meeus/Jones/Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = 1 + ((h + l - 7 * m + 114) % 31)
    return date(year, month, day)

def _koningsdag(year: int) -> date:
    """
    Koningsdag is 27 april.
    Valt 27/4 op zondag, dan wordt het 26/4 (zaterdag).
    """
    d = date(year, 4, 27)
    if d.weekday() == 6:  # zondag
        return date(year, 4, 26)
    return d

def nl_holidays_for_year(year: int) -> list[tuple[date, str]]:
    """Standaard NL-feestdagen voor een jaar (naam en datum)."""
    res: list[tuple[date, str]] = []

    # Vaste dagen
    res.append((date(year, 1, 1), "Nieuwjaarsdag"))
    res.append((date(year, 12, 25), "Eerste Kerstdag"))
    res.append((date(year, 12, 26), "Tweede Kerstdag"))

    # Koningsdag (met zondag→zaterdag regel)
    res.append((_koningsdag(year), "Koningsdag"))

    # Paascyclus
    easter = _easter_sunday(year)              # Eerste Paasdag
    good_friday = easter - timedelta(days=2)   # Goede Vrijdag
    easter_monday = easter + timedelta(days=1) # Tweede Paasdag
    ascension = easter + timedelta(days=39)    # Hemelvaartsdag
    pentecost = easter + timedelta(days=49)    # Eerste Pinksterdag
    pentecost_monday = easter + timedelta(days=50)  # Tweede Pinksterdag

    res.extend([
        (good_friday, "Goede Vrijdag"),
        (easter, "Eerste Paasdag"),
        (easter_monday, "Tweede Paasdag"),
        (ascension, "Hemelvaartsdag"),
        (pentecost, "Eerste Pinksterdag"),
        (pentecost_monday, "Tweede Pinksterdag"),
    ])

    # (Optioneel) Bevrijdingsdag – jaarlijks. Wil je die niet? comment de volgende regel uit.
    res.append((date(year, 5, 5), "Bevrijdingsdag"))

    return res

def seed_public_holidays(session, start_year: int, end_year: int):
    """
    Vul public_holiday voor [start_year .. end_year] aan.
    Idempotent: bestaande datums worden overgeslagen.
    """
    existing = {
        ph.date for ph in session.query(PublicHoliday.date).all()
    }
    to_add = []
    for y in range(start_year, end_year + 1):
        for d, name in nl_holidays_for_year(y):
            if d not in existing:
                to_add.append(PublicHoliday(date=d, name=name))
                existing.add(d)
    if to_add:
        session.add_all(to_add)
        session.commit()

def init_db(engine):
    Base.metadata.create_all(engine)
    session = get_session(engine)
    year_now = date.today().year
    seed_public_holidays(session, year_now - 3, year_now + 10)
    # ---- MIGRATIE: kolommen toevoegen aan fixed_off_day indien ze ontbreken
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("fixed_off_day")}
    with engine.begin() as conn:
        if "part" not in cols:
            conn.execute(text("ALTER TABLE fixed_off_day ADD COLUMN part TEXT NOT NULL DEFAULT 'FULL'"))
        if "absence_fraction" not in cols:
            conn.execute(text("ALTER TABLE fixed_off_day ADD COLUMN absence_fraction REAL NOT NULL DEFAULT 1.0"))
            # update bestaande rijen op basis van part
            conn.execute(text(
                "UPDATE fixed_off_day SET absence_fraction=CASE part "
                "WHEN 'AM' THEN 0.5 WHEN 'PM' THEN 0.5 ELSE 1.0 END"
            ))

    # ---- Leave codes seeden (bestonden eerder al?)
    def ensure_code(code, label, color, counts=True, frac=1.0):
        lc = session.query(LeaveCode).filter(LeaveCode.code == code).one_or_none()
        if not lc:
            lc = LeaveCode(code=code, label=label, color_hex=color,
                           counts_as_absent=counts, absence_fraction=frac)
            session.add(lc)

    # bestaande: VV (hele dag)
    ensure_code("VV", "Vaste vrije dag (hele dag)", "#c0c0c0", True, 1.0)
    # nieuw: VO/VM halve dag
    ensure_code("VO", "Vaste vrije dag (ochtend)", "#b0e0ff", True, 0.5)
    ensure_code("VM", "Vaste vrije dag (middag)", "#ffd0b0", True, 0.5)

    session.commit()
    session.close()

    return engine
