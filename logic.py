from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import and_
from models import Vacation, PublicHoliday, LeaveCode, FixedOffDay, FixedOffException, Role, Resource

# ---------- Basis helpers ----------
def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=Za, 6=Zo

def holidays_between(start: date, end: date, session):
    rows = (
        session.query(PublicHoliday)
        .filter(PublicHoliday.date >= start, PublicHoliday.date <= end)
        .all()
    )
    return {r.date: r.name for r in rows}

def ensure_public_holidays(session, year: int):
    """Plaats hier jouw eigen lijst met NL feestdagen; skeleton blijft."""
    # Als er al feestdagen voor dit jaar zijn, niets doen
    existing = session.query(PublicHoliday).filter(
        PublicHoliday.date >= date(year,1,1),
        PublicHoliday.date <= date(year,12,31)
    ).count()
    if existing:
        return
    # Voorbeeld (minimaal) – vul gerust aan
    base = [
        (date(year, 1, 1), "Nieuwjaarsdag"),
        (date(year, 4, 27), "Koningsdag"),
        (date(year, 12, 25), "Eerste Kerstdag"),
        (date(year, 12, 26), "Tweede Kerstdag"),
    ]
    session.add_all(PublicHoliday(date=d, name=n) for d,n in base)
    session.commit()

# ---------- Verlof mutaties ----------
def leave_on(resource_id: int, day: date, session):
    v = session.query(Vacation).filter_by(resource_id=resource_id, date=day).first()
    return v.code if v else None

def set_leave_range(resource_id: int, start: date, end: date, code: str, session):
    # verwijder bestaande in range
    session.query(Vacation).filter(
        and_(Vacation.resource_id == resource_id,
             Vacation.date >= start,
             Vacation.date <= end)
    ).delete(synchronize_session=False)
    # voeg werkdagen toe; weekenden niet
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            session.add(Vacation(resource_id=resource_id, date=cur, code=code))
        cur += timedelta(days=1)
    session.commit()

def clear_leave_range(resource_id: int, start_date, end_date, session):
    """Verwijder alle Vacation records voor de resource in [start_date, end_date]."""
    cur = start_date
    while cur <= end_date:
        session.query(Vacation).filter(
            Vacation.resource_id == resource_id,
            Vacation.date == cur
        ).delete(synchronize_session=False)
        cur += timedelta(days=1)
    session.commit()
# ---------- Aanwezigheid / bezetting ----------
def fixed_off_lookup(resource_id: int, session):
    """Geeft een callable(d) terug die True is als d vaste vrije dag is.
       Werkt zowel met als zonder effective_from/effective_to kolommen."""
    rows = session.query(FixedOffDay).filter_by(resource_id=resource_id).all()
    def is_fixed(d: date) -> bool:
        wd = d.weekday()
        for f in rows:
            if f.weekday != wd:
                continue
            # velden kunnen ontbreken of None zijn; vang robuust af
            ef = getattr(f, "effective_from", None)
            et = getattr(f, "effective_to", None)
            if (ef is None or ef <= d) and (et is None or et >= d):
                return True
        return False
    return is_fixed

def fixed_off_exception_for(session, resource_id: int, d: date):
    """Geef eventueel de uitzondering voor (resource, datum) terug (of None)."""
    return session.query(FixedOffException).filter(
        FixedOffException.resource_id == resource_id,
        FixedOffException.date == d
    ).one_or_none()

def fixed_off_weekly_for(session, resource_id: int, d: date):
    """Geeft (code, fraction) voor het weekpatroon VV/VO/VM of (None, 0.0) als niet van toepassing."""
    if d.weekday() >= 5:
        return (None, 0.0)
    f = session.query(FixedOffDay).filter(
        FixedOffDay.resource_id == resource_id,
        FixedOffDay.weekday == d.weekday()
    ).one_or_none()
    if not f:
        return (None, 0.0)
    part = (f.part or "FULL").upper()
    code = {"FULL": "VV", "AM": "VO", "PM": "VM"}.get(part, "VV")
    frac = f.absence_fraction or (1.0 if part == "FULL" else 0.5)
    return (code, frac)

def fixed_off_effect_for(session, resource_id: int, d: date):
    """
    Combineer uitzondering + weekpatroon:
    - Als uitzondering bestaat:
        * part == "NONE"  -> geen vaste vrij (aanwezig)
        * part in {"FULL","AM","PM"} -> forceer die code
    - Anders: gebruik weekpatroon (indien aanwezig)
    Retourneert (code, fraction) of (None, 0.0).
    """
    ex = fixed_off_exception_for(session, resource_id, d)
    if ex:
        p = (ex.part or "NONE").upper()
        if p == "NONE":
            return (None, 0.0)
        code = {"FULL": "VV", "AM": "VO", "PM": "VM"}.get(p, "VV")
        frac = 1.0 if p == "FULL" else 0.5
        return (code, frac)
    # geen uitzondering -> weekpatroon
    return fixed_off_weekly_for(session, resource_id, d)

def presence_count(d: date, session):
    """
    Retourneert dict {rolnaam: aantal_aanwezig} voor werkdag d.
    Logica:
      aanwezig start op 1.0
      verlofcode met counts_as_absent en fraction f  -> aanwezig -= f
      vaste vrije (weekpatroon of uitzondering)      -> aanwezig -= fraction (1.0 of 0.5)
      ondergrens 0.0
    """
    # lokale imports om circular imports te vermijden
    from models import Resource, Role, LeaveCode, Vacation
    # holidays_between staat in ditzelfde logic.py bestand; we kunnen het direct aanroepen

    # Preload leave codes: code -> (absent_bool, fraction)
    code_map = {}
    for c in session.query(LeaveCode).all():
        frac = c.absence_fraction
        if frac is None:
            frac = 1.0 if c.counts_as_absent else 0.0
        code_map[c.code] = (bool(c.counts_as_absent), float(frac))

    # Rollenkaart
    roles = {r.id: r for r in session.query(Role).all()}
    out = {}
    for r in roles.values():
        out[r.name] = 0.0
    out.setdefault("(zonder rol)", 0.0)

    # Alle vacations op datum d in één keer
    vacs = {
        v.resource_id: v.code
        for v in session.query(Vacation).filter(Vacation.date == d).all()
    }

    # Door alle medewerkers
    res_list = session.query(Resource).all()
    for r in res_list:
        rolnaam = roles.get(r.role_id).name if r.role_id in roles else "(zonder rol)"
        aanwezig = 1.0

        # Verlof?
        vcode = vacs.get(r.id)
        if vcode:
            absent, frac = code_map.get(vcode, (False, 0.0))
            if absent:
                aanwezig -= frac

        # Vaste vrije dag (incl. uitzonderingen)
        # fixed_off_effect_for staat in logic.py; rechtstreeks aanroepen
        code_vv, frac_vv = fixed_off_effect_for(session, r.id, d)
        if code_vv:
            aanwezig -= frac_vv

        if aanwezig < 0.0:
            aanwezig = 0.0

        out[rolnaam] = out.get(rolnaam, 0.0) + aanwezig

    return out
    
def check_min_max(day: date, session):
    """Geef waarschuwingen per rol als min/max overschreden wordt."""
    present = presence_count(day, session)
    issues = {}
    for role in session.query(Role).all():
        n = present.get(role.name, 0)
        if n < role.min_required_per_day:
            issues[role.name] = f"Onder min ({n}/{role.min_required_per_day})"
        elif n > role.max_allowed_per_day:
            issues[role.name] = f"Boven max ({n}/{role.max_allowed_per_day})"
    return issues

# ==== Halve-dagen bezettingslogica ====
from datetime import date
from models import Vacation, LeaveCode, FixedOffDay, PublicHoliday, Resource, Role

def absence_fraction_for_day(session, resource: Resource, d: date) -> float:
    """
    Geeft de afwezigheid in 'dagen' terug voor resource op datum d.
    - Geplande Vacation + LeaveCode (counts_as_absent, absence_fraction)
    - Vaste vrije dag: FULL = 1.0, AM/PM = 0.5
    - Maximaal 1.0 (clamped)
    - Weekenden en feestdagen tellen in principe niet als werktijd, maar
      deze functie rekent puur 'afwezigheid' (zodat je zelf kunt beslissen
      wat je met weekenden/feestdagen doet in je UI).
    """
    total = 0.0

    # Vacation (ingepland)
    vac = session.query(Vacation).filter(
        Vacation.resource_id == resource.id,
        Vacation.date == d
    ).one_or_none()
    if vac:
        lc = session.query(LeaveCode).filter(LeaveCode.code == vac.code).one_or_none()
        if lc and lc.counts_as_absent:
            frac = float(lc.absence_fraction or 1.0)
            total += frac

    # Vaste vrije dag (alleen doordeweeks relevant)
    if d.weekday() < 5:  # 0=ma..4=vr
        fods = session.query(FixedOffDay).filter(
            FixedOffDay.resource_id == resource.id,
            FixedOffDay.weekday == d.weekday()
        ).all()
        for f in fods:
            if (f.absence_fraction is not None) and (f.absence_fraction > 0):
                total += float(f.absence_fraction)
            else:
                # fallback: FULL=1.0, AM/PM=0.5
                part = (f.part or "FULL").upper()
                total += 1.0 if part == "FULL" else 0.5

    # Clamp tussen 0..1
    if total > 1.0:
        total = 1.0
    elif total < 0.0:
        total = 0.0
    return total


def present_fraction_for_day(session, resource: Resource, d: date) -> float:
    """
    1.0 - absence_fraction (met clamp 0..1).
    Gebruik dit om aanwezigheid (hele of halve dag) op te tellen.
    """
    return 1.0 - absence_fraction_for_day(session, resource, d)


def role_presence_for_date(session, resources: list[Resource], d: date) -> dict[int, float]:
    """
    Som van 'aanwezigheid' per rol op datum d.
    Retourneert: {role_id: som_aanwezigheid}, waarbij aanwezigheid 0.0..1.0 is per persoon.
    """
    per_role = {}
    for r in resources:
        if r.role_id is None:
            # overslaan of opnemen onder key None
            continue
        val = present_fraction_for_day(session, r, d)
        per_role[r.role_id] = per_role.get(r.role_id, 0.0) + val
    return per_role
