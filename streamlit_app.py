import os, tempfile, datetime
import streamlit as st
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from drive_store import download_db, upload_db, exclusive_writer
from db_init import init_db
from models import get_engine, get_session, Role, Resource, FixedOffDay, LeaveCode, Vacation
from logic import presence_count, ensure_public_holidays, clear_leave_range, set_leave_range

st.set_page_config(page_title="Vakantie Rooster", layout="wide")

# ---------------------- DB bootstrap (Drive -> lokaal) ----------------------
@st.cache_resource
# --- Secrets quick self-test ---

def _bootstrap_db():
    tmpdir = tempfile.mkdtemp(prefix="vakantie-rooster_")
    local_db_path = os.path.join(tmpdir, "vakantierooster.db")
    meta = download_db(local_db_path)  # bevat o.a. headRevisionId
    engine = get_engine(local_db_path)
    init_db(engine)
    return {"local_db_path": local_db_path, "engine": engine, "meta": meta}
    try:
        raw = st.secrets["drive"].get("SERVICE_ACCOUNT_JSON")
        assert raw, "SERVICE_ACCOUNT_JSON ontbreekt."
        # Als het een string is: proberen te parsen
        if isinstance(raw, str):
            _ = json.loads(raw.lstrip("\ufeff").strip())
        st.success("Secrets check: OK (SERVICE_ACCOUNT_JSON leesbaar).")
    except Exception as e:
        st.error(f"Secrets check: FOUT — {e}")
        st.stop()
bootstrap = _bootstrap_db()
LOCAL_DB = bootstrap["local_db_path"]
ENGINE = bootstrap["engine"]
REMOTE_REV = bootstrap["meta"].get("headRevisionId")

def _session() -> Session:
    return get_session(ENGINE)

# ---------------------- UI: header & acties ----------------------
st.title("Vakantie Rooster – Web")
st.caption(
    "Werkt met een SQLite-bestand dat bij start uit Google Drive is gedownload. "
    "Gebruik **Opslaan naar Drive** om wijzigingen terug te schrijven (met revision-check)."
)

c1, c2, c3 = st.columns([1,1,2], vertical_alignment="center")
with c1:
    if st.button("🔄 Herladen vanaf Drive"):
        st.cache_resource.clear()
        st.success("App wordt opnieuw geïnitialiseerd. Herlaad de pagina (Ctrl/Cmd+R).")
with c2:
    if st.button("💾 Opslaan naar Drive"):
        try:
            with exclusive_writer():
                upload_db(LOCAL_DB, expect_head_rev=REMOTE_REV)
            st.success("Database is geüpload naar Google Drive.")
        except Exception as e:
            st.error(f"Opslaan mislukt: {e}")
with c3:
    with _session() as ses:
        try:
            roles_cnt = ses.execute(text("SELECT COUNT(*) FROM role")).scalar() or 0
            res_cnt = ses.execute(text("SELECT COUNT(*) FROM resource")).scalar() or 0
        except Exception:
            roles_cnt, res_cnt = 0, 0
    st.info(f"Rollen: **{roles_cnt}** · Medewerkers: **{res_cnt}**")

# ---------------------- Tabs ----------------------
tab_overview, tab_plan, tab_admin = st.tabs(["📊 Overzicht", "🗓️ Plan Verlof", "⚙️ Resources & Codes"])

# ---------------------- Tab: Overzicht ----------------------
with tab_overview:
    st.subheader("Aanwezigheid per dag/rol")
    today = datetime.date.today()
    col_a, col_b = st.columns(2)
    with col_a:
        year = st.number_input("Jaar", min_value=2020, max_value=2035, value=today.year, step=1)
    with col_b:
        month = st.number_input("Maand", min_value=1, max_value=12, value=today.month, step=1)

    start = datetime.date(int(year), int(month), 1)
    if month == 12:
        end = datetime.date(int(year)+1, 1, 1) - datetime.timedelta(days=1)
    else:
        end = datetime.date(int(year), int(month)+1, 1) - datetime.timedelta(days=1)

    with _session() as ses:
        ensure_public_holidays(ses, int(year))
        roles = [r.name for r in ses.query(Role).order_by(Role.name).all()]

        rows = []
        cur = start
        while cur <= end:
            counts = presence_count(cur, ses) if cur.weekday() < 5 else {}
            row = {"datum": cur.strftime("%Y-%m-%d"), "weekdag": ["Ma","Di","Wo","Do","Vr","Za","Zo"][cur.weekday()]}
            for rn in roles:
                row[rn] = counts.get(rn, 0) if cur.weekday() < 5 else ""
            rows.append(row)
            cur += datetime.timedelta(days=1)

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

# ---------------------- Tab: Plan Verlof ----------------------
with tab_plan:
    st.subheader("Plan of verwijder verlof")
    today = datetime.date.today()

    with _session() as ses:
        resources = ses.query(Resource).order_by(Resource.first_name, Resource.last_name).all()
        codes = ses.query(LeaveCode).order_by(LeaveCode.code).all()

    col1, col2 = st.columns([2,1])
    with col1:
        res_map = {f"{r.full_name} — {r.role.name}": r.id for r in resources}
        res_label = st.selectbox("Medewerker", options=list(res_map.keys())) if resources else None
        res_id = res_map.get(res_label) if res_label else None

        dcol1, dcol2, dcol3 = st.columns(3)
        with dcol1:
            d_from = st.date_input("Van", value=today)
        with dcol2:
            d_to = st.date_input("T/m", value=today)
        with dcol3:
            code_map = {f"{c.code} — {c.label}": c.code for c in codes}
            code_label = st.selectbox("Code", options=list(code_map.keys())) if codes else None
            code = code_map.get(code_label) if code_label else None

        bcol1, bcol2 = st.columns([1,1])
        with bcol1:
            if st.button("Plan"):
                if not (res_id and d_from and d_to and code):
                    st.warning("Selecteer medewerker, periode en code.")
                else:
                    d0, d1 = (d_from, d_to) if d_from <= d_to else (d_to, d_from)
                    try:
                        with _session() as ses:
                            set_leave_range(res_id, d0, d1, code, ses)
                        st.success("Verlof ingepland.")
                    except Exception as e:
                        st.error(f"Fout bij plannen: {e}")
        with bcol2:
            if st.button("Verwijder"):
                if not (res_id and d_from and d_to):
                    st.warning("Selecteer medewerker en periode.")
                else:
                    d0, d1 = (d_from, d_to) if d_from <= d_to else (d_to, d_from)
                    try:
                        with _session() as ses:
                            clear_leave_range(res_id, d0, d1, ses)
                        st.success("Verlof verwijderd.")
                    except Exception as e:
                        st.error(f"Fout bij verwijderen: {e}")

    st.divider()
    st.markdown("### Ingepland (huidig + volgend jaar)")
    with _session() as ses:
        if res_id:
            y0 = today.year
            q = ses.query(Vacation).filter(
                Vacation.resource_id == res_id,
                Vacation.date >= datetime.date(y0,1,1),
                Vacation.date <= datetime.date(y0+1,12,31),
            ).order_by(Vacation.date.asc())
            data = [{"datum": v.date.strftime("%Y-%m-%d"), "code": v.code} for v in q.all()]
            st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)
        else:
            st.info("Kies een medewerker om het overzicht te tonen.")

# ---------------------- Tab: Resources & Codes ----------------------
with tab_admin:
    st.subheader("Beheer – rollen, medewerkers, vaste vrije dagen, codes")

    # Rollen
    st.markdown("#### Rollen")
    colr1, colr2 = st.columns([2,1])
    with colr1:
        with _session() as ses:
            roles = ses.query(Role).order_by(Role.name).all()
            df_roles = pd.DataFrame([{
                "naam": r.name, "min/dag": r.min_required_per_day, "max/dag": r.max_allowed_per_day
            } for r in roles])
        st.dataframe(df_roles, hide_index=True, use_container_width=True) if not df_roles.empty else st.info("Nog geen rollen.")
    with colr2:
        with st.form("frm_role", clear_on_submit=True):
            name = st.text_input("Naam")
            minv = st.number_input("Min/dag", min_value=0, max_value=100, value=0, step=1)
            maxv = st.number_input("Max/dag", min_value=0, max_value=1000, value=999, step=1)
            if st.form_submit_button("Rol opslaan/aanmaken"):
                if not name.strip():
                    st.warning("Naam is verplicht.")
                else:
                    with _session() as ses:
                        r = ses.query(Role).filter(Role.name == name.strip()).one_or_none()
                        if not r: r = Role(name=name.strip())
                        r.min_required_per_day, r.max_allowed_per_day = int(minv), int(maxv)
                        ses.add(r); ses.commit()
                    st.success("Rol opgeslagen.")

    st.divider()

    # Medewerkers
    st.markdown("#### Medewerkers")
    with _session() as ses:
        roles = ses.query(Role).order_by(Role.name).all()
        role_opts = {r.name: r.id for r in roles}
    colm1, colm2 = st.columns([2,1])
    with colm1:
        with _session() as ses:
            res = ses.query(Resource).order_by(Resource.first_name, Resource.last_name).all()
            df_res = pd.DataFrame([{
                "voornaam": r.first_name, "achternaam": r.last_name, "rol": r.role.name
            } for r in res])
        st.dataframe(df_res, hide_index=True, use_container_width=True) if not df_res.empty else st.info("Nog geen medewerkers.")
    with colm2:
        with st.form("frm_res", clear_on_submit=True):
            fn = st.text_input("Voornaam")
            ln = st.text_input("Achternaam")
            role_name = st.selectbox("Rol", options=list(role_opts.keys())) if role_opts else None
            if st.form_submit_button("Medewerker opslaan/aanmaken"):
                if not (fn.strip() and role_name):
                    st.warning("Voornaam en rol zijn verplicht.")
                else:
                    with _session() as ses:
                        rid = role_opts[role_name]
                        r = Resource(first_name=fn.strip(), last_name=ln.strip(), role_id=rid)
                        ses.add(r); ses.commit()
                    st.success("Medewerker opgeslagen.")

    st.divider()

    # Vaste vrije dagen
    st.markdown("#### Vaste vrije dagen")
    with _session() as ses:
        res_all = ses.query(Resource).order_by(Resource.first_name, Resource.last_name).all()
    res_map2 = {f"{r.full_name} — {r.role.name}": r.id for r in res_all}
    lbl = st.selectbox("Medewerker (vaste vrij)", options=list(res_map2.keys()) if res_map2 else [])
    if lbl:
        rid = res_map2[lbl]
        wd_labels = ["Ma","Di","Wo","Do","Vr","Za","Zo"]
        with _session() as ses:
            current_wd = {f.weekday for f in ses.query(FixedOffDay).filter(FixedOffDay.resource_id==rid).all()}
        cols = st.columns(7)
        new_wd = set()
        for i, c in enumerate(cols):
            with c:
                if st.checkbox(wd_labels[i], value=(i in current_wd), key=f"wd_{rid}_{i}"):
                    new_wd.add(i)
        if st.button("Opslaan vaste vrije dagen"):
            with _session() as ses:
                ses.query(FixedOffDay).filter(FixedOffDay.resource_id==rid).delete()
                for i in sorted(new_wd):
                    ses.add(FixedOffDay(resource_id=rid, weekday=i))
                ses.commit()
            st.success("Vaste vrije dagen opgeslagen.")

    st.divider()

    # Codes
    st.markdown("#### Verlof-codes")
    colc1, colc2 = st.columns([2,1])
    with colc1:
        with _session() as ses:
            codes = ses.query(LeaveCode).order_by(LeaveCode.code).all()
            df_codes = pd.DataFrame([{
                "code": c.code, "label": c.label, "kleur": c.color_hex,
                "afwezig?": "ja" if (c.absence_fraction or 0) > 0 else "nee",
                "fractie": c.absence_fraction or 1.0
            } for c in codes])
        st.dataframe(df_codes, hide_index=True, use_container_width=True) if not df_codes.empty else st.info("Nog geen codes.")
    with colc2:
        with st.form("frm_code", clear_on_submit=True):
            code = st.text_input("Code")
            label = st.text_input("Omschrijving")
            color = st.text_input("Kleur (#RRGGBB)", value="#C6E6C6")
            frac = st.selectbox("Afwezigheid", options=["Geen (0.0)","Halve dag (0.5)","Hele dag (1.0)"])
            if st.form_submit_button("Code opslaan/aanmaken"):
                if not code.strip():
                    st.warning("Code is verplicht.")
                else:
                    frac_val = {"Geen (0.0)":0.0,"Halve dag (0.5)":0.5,"Hele dag (1.0)":1.0}[frac]
                    with _session() as ses:
                        c = ses.query(LeaveCode).filter(LeaveCode.code == code.strip()).one_or_none()
                        if not c:
                            c = LeaveCode(code=code.strip())
                        c.label = label.strip() or code.strip()
                        c.color_hex = color.strip() or "#C6E6C6"
                        c.absence_fraction = float(frac_val)
                        c.counts_as_absent = (float(frac_val) > 0.0)
                        ses.add(c); ses.commit()
                    st.success("Code opgeslagen.")
