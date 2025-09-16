from datetime import date, timedelta
from calendar import monthrange
from collections import defaultdict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QPushButton, QComboBox, QMessageBox, QScrollArea, QSizePolicy,
    QAbstractItemView, QMenu
)
from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtGui import QColor, QBrush

from models import Resource, LeaveCode, Role, Vacation
from logic import (
    is_weekend, holidays_between, leave_on, set_leave_range, check_min_max,
    fixed_off_effect_for
)

# optioneel: voor rol-bezetting met halve dagen
try:
    from logic import presence_count  # dict rolnaam -> aanwezig aantal (met 0.5)
    _HAS_PRESENCE = True
except Exception:
    _HAS_PRESENCE = False

MONTHS = ["", "Januari", "Februari", "Maart", "April", "Mei", "Juni",
          "Juli", "Augustus", "September", "Oktober", "November", "December"]


def _vv_human(vv_code: str) -> str:
    if vv_code == "VO": return "Vaste vrije dag (ochtend)"
    if vv_code == "VM": return "Vaste vrije dag (middag)"
    if vv_code == "VV": return "Vaste vrije dag (hele dag)"
    return "Vaste vrije dag"


class MonthGrid(QTableWidget):
    """
    Raster met een extra naamkolom (kolom 0).
    - Kolom 0: functienaam (vet) voor groepsrijen / medewerkernaam voor rijen eronder.
    - Kolommen 1..N: dagen van de maand.
    """
    def __init__(self, session, year, month, resources, code_lookup, presence_provider=None):
        self.session = session
        self.year = year
        self.month = month
        self.code_lookup = code_lookup
        # callable(d: date) -> dict(rolnaam -> float) (gecachete presence), of None
        self._presence_provider = presence_provider

        self._build_rows(resources)  # zet self.group_rows, self.row_to_resource

        days = monthrange(year, month)[1]
        super().__init__(self.row_count(), days + 1)  # +1 voor naamkolom

        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(True)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)

        # contextmenu voor uitzonderingen vaste vrije dagen
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_context_menu)

        # headers: kolom 0 leeg (naamkolom), daarna dagnummers in 1..days
        self.setHorizontalHeaderItem(0, QTableWidgetItem(""))
        for d in range(1, days + 1):
            self.setHorizontalHeaderItem(d, QTableWidgetItem(str(d)))

        self.refresh_cells()
        self.cellEntered.connect(self._show_tooltip)

    # ------- contextmenu (eenmalige uitzonderingen) -------
    def _open_context_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return
        row, col = index.row(), index.column()
        # alleen dagkolommen en medewerker-rijen
        if col == 0:
            return
        res = self.row_to_resource[row]
        if res is None:
            return

        d = date(self.year, self.month, col)

        # Huidige status (alleen tbv context, selectie niet strikt nodig)
        cur_code, _cur_frac = fixed_off_effect_for(self.session, res.id, d)

        menu = QMenu(self)
        act_add_full = menu.addAction("Eenmalig: vaste vrije dag (hele dag) toevoegen")
        act_add_am   = menu.addAction("Eenmalig: vaste vrije ochtend toevoegen")
        act_add_pm   = menu.addAction("Eenmalig: vaste vrije middag toevoegen")
        act_cancel   = menu.addAction("Eenmalig: vaste vrije status annuleren (aanwezig)")
        act_remove   = menu.addAction("Uitzondering verwijderen (terug naar patroon)")

        # "Uitzondering verwijderen" alleen als er al een exception bestaat
        from models import FixedOffException
        ex = self.session.query(FixedOffException).filter_by(resource_id=res.id, date=d).one_or_none()
        if ex is None:
            act_remove.setEnabled(False)

        chosen = menu.exec_(self.viewport().mapToGlobal(pos))
        if not chosen:
            return

        if chosen is act_remove:
            if ex:
                self.session.delete(ex)
                self.session.commit()
                self.refresh_cells()
            return

        # Mappen van acties naar exception 'part'
        part = None
        if chosen is act_add_full: part = "FULL"
        elif chosen is act_add_am: part = "AM"
        elif chosen is act_add_pm: part = "PM"
        elif chosen is act_cancel: part = "NONE"
        if part is None:
            return

        # Upsert exception
        if ex is None:
            ex = FixedOffException(resource_id=res.id, date=d, part=part)
            self.session.add(ex)
        else:
            ex.part = part

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            QMessageBox.warning(self, "Fout", f"Kon uitzondering niet opslaan:\n{e}")
            return

        self.refresh_cells()

    # ------- helpers voor rijenopbouw -------
    def _build_rows(self, resources):
        # groepeer resources per rol
        by_role: dict[int, list[Resource]] = defaultdict(list)
        for r in resources:
            if r.role_id:
                by_role[r.role_id].append(r)
            else:
                by_role[-1].append(r)  # zonder rol

        # sorteer rollen op naam
        all_roles = {r.id: r for r in self.session.query(Role).all()}
        ordered = []
        def role_sort_key(rid):
            role = all_roles.get(rid)
            return role.name if role else "ZZZ"
        for rid in sorted(by_role.keys(), key=role_sort_key):
            role = all_roles.get(rid)
            ordered.append((role, sorted(by_role[rid], key=lambda r: (r.last_name or "", r.first_name or ""))))

        self.row_to_resource: list[Resource | None] = []
        self.group_rows: dict[int, Role | None] = {}  # rij-index -> rol (None voor 'zonder rol')

        # 1 groepsrij + N medewerker-rijen per rol
        for role, lst in ordered:
            # groepsrij
            self.row_to_resource.append(None)
            self.group_rows[len(self.row_to_resource) - 1] = role
            # medewerkers
            for r in lst:
                self.row_to_resource.append(r)

    def row_count(self) -> int:
        return len(self.row_to_resource)

    # ------- rendering -------
    def refresh_cells(self):
        days_in_month = self.columnCount() - 1  # zonder naamkolom
        start = date(self.year, self.month, 1)
        end = date(self.year, self.month, days_in_month)
        hol = holidays_between(start, end, self.session)

        # vooraf: map rolnaam voor presence_count()
        role_name_for_row: dict[int, str] = {}
        for row, role in self.group_rows.items():
            role_name_for_row[row] = role.name if role else "(zonder rol)"

        # vul cellen
        for row, res in enumerate(self.row_to_resource):
            is_group = res is None
            role = self.group_rows.get(row) if is_group else None

            # kolom 0: naam
            if is_group:
                nm = role.name if role else "(zonder rol)"
                name_item = QTableWidgetItem(nm)
                font = name_item.font(); font.setBold(True); name_item.setFont(font)
                name_item.setFlags(Qt.ItemIsEnabled)
                name_item.setBackground(QBrush(QColor("#EFEFEF")))
                name_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            else:
                nm = self._resource_display_name(res)
                name_item = QTableWidgetItem(nm)
                name_item.setFlags(Qt.ItemIsEnabled)
                name_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.setItem(row, 0, name_item)

            # dagen (1..days_in_month) zitten op kolommen 1..N
            for c in range(1, days_in_month + 1):
                d = date(self.year, self.month, c)

                if is_group:
                    # groepsrij: toon bezetting per dag (indien helper aanwezig)
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setBackground(QBrush(QColor("#EFEFEF")))
                    item.setTextAlignment(Qt.AlignCenter)
                    if not is_weekend(d) and _HAS_PRESENCE:
                        if self._presence_provider is not None:
                            pr = self._presence_provider(d)  # geachete presence
                        else:
                            pr = presence_count(d, self.session)  # {rolnaam: aantal}
                        nm_role = role_name_for_row[row]
                        if nm_role in pr:
                            txt = f"{pr[nm_role]:.1f}".rstrip("0").rstrip(".")
                            item.setText(txt)
                    self.setItem(row, c, item)
                    continue

                # medewerker-rij
                txt = leave_on(res.id, d, self.session)
                item = QTableWidgetItem(txt or "")
                item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignCenter)

                # kleuren + vaste vrije dag (incl. uitzonderingen)
                if is_weekend(d):
                    item.setBackground(QBrush(QColor("#BDBDBD")))    # weekend
                elif d in hol:
                    item.setBackground(QBrush(QColor("#FFD6D6")))    # feestdag
                    if not txt:
                        item.setText("F")
                else:
                    if txt:
                        code = self.code_lookup.get(txt)
                        if code and code.color_hex:
                            item.setBackground(QBrush(QColor(code.color_hex)))
                    else:
                        # geen vacation → check vaste vrije dag (met uitzondering)
                        vv_code, _frac = fixed_off_effect_for(self.session, res.id, d)
                        if vv_code:
                            item.setText(vv_code)
                            code = self.code_lookup.get(vv_code)
                            if code and code.color_hex:
                                item.setBackground(QBrush(QColor(code.color_hex)))

                self.setItem(row, c, item)

        self.resizeColumnsToContents()
        self.resizeRowsToContents()

        # zorg dat naamkolom iets breder is
        self.setColumnWidth(0, max(140, self.columnWidth(0)))

        # hoogte passend maken
        h = self.horizontalHeader().height()
        for r in range(self.rowCount()):
            h += self.rowHeight(r)
        h += 2 * self.frameWidth() + 4
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)

    def _resource_display_name(self, r: Resource) -> str:
        parts = []
        if r.first_name: parts.append(r.first_name)
        if r.last_name: parts.append(r.last_name)
        return " ".join(parts) if parts else (r.full_name or "Onbekend")

    # ------- selectie helpers (alleen medewerker-rijen) -------
    def _selected_range_dates(self):
        ranges = self.selectedRanges()
        if not ranges:
            return None
        sr = ranges[0]
        row = sr.topRow()
        if sr.bottomRow() != row:
            return None
        res = self.row_to_resource[row]
        if res is None:
            return None  # geen groepsrij
        # selectie moet binnen dagkolommen liggen (>=1)
        if sr.leftColumn() < 1:
            return None
        start_day = sr.leftColumn()              # kolom 1 -> dag 1
        end_day = sr.rightColumn()
        start = date(self.year, self.month, start_day)
        end = date(self.year, self.month, end_day)
        return res, start, end

    # ------- acties -------
    def apply_code_to_selection(self, code: str):
        sel = self._selected_range_dates()
        if not sel:
            QMessageBox.warning(self, "Selectie", "Selecteer binnen één medewerker (niet op groepsrij) een reeks dagen in de dagkolommen.")
            return
        res, start, end = sel
        set_leave_range(res.id, start, end, code, self.session)

        # waarschuwingen per dag (min/max)
        warn_msgs = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                issues = check_min_max(cur, self.session)
                if issues:
                    joined = "; ".join(f"{role}: {msg}" for role, msg in issues.items())
                    warn_msgs.append(f"{cur}: {joined}")
            cur += timedelta(days=1)
        if warn_msgs:
            QMessageBox.information(self, "Bezettingswaarschuwing", "\n".join(warn_msgs[:15]))

        self.refresh_cells()

    def clear_code_on_selection(self):
        sel = self._selected_range_dates()
        if not sel:
            QMessageBox.warning(self, "Selectie", "Selecteer binnen één medewerker (niet op groepsrij) dagen om te wissen (in de dagkolommen).")
            return
        res, start, end = sel
        # verwijder Vacation records voor bereik
        self.session.query(Vacation).filter(
            Vacation.resource_id == res.id,
            Vacation.date >= start,
            Vacation.date <= end
        ).delete(synchronize_session=False)
        self.session.commit()
        self.refresh_cells()

    # ------- UX -------
    def _show_tooltip(self, row, col):
        # naamkolom: geen tooltip nodig
        if col == 0:
            self.setToolTip("")
            return

        d = date(self.year, self.month, col)  # kolom==dag
        res = self.row_to_resource[row]
        if res is None:
            # groepsrij: toon bezetting per rol
            if _HAS_PRESENCE and not is_weekend(d):
                nm = (self.group_rows[row].name if self.group_rows[row] else "(zonder rol)")
                if self._presence_provider is not None:
                    pr = self._presence_provider(d)
                else:
                    pr = presence_count(d, self.session)
                val = pr.get(nm, None)
                if val is not None:
                    self.setToolTip(f"{nm}: {f'{val:.1f}'.rstrip('0').rstrip('.')} aanwezig")
                else:
                    self.setToolTip(nm)
            else:
                self.setToolTip("")
            return

        code = leave_on(res.id, d, self.session)
        if is_weekend(d):
            text = "Weekend"
        else:
            hol = holidays_between(d, d, self.session)
            if d in hol:
                text = hol[d]
            elif code:
                c = self.code_lookup.get(code)
                text = f"{code} – {c.label if c else ''}".strip(" –")
            else:
                vv_code, _ = fixed_off_effect_for(self.session, res.id, d)
                if vv_code:
                    text = _vv_human(vv_code)
                else:
                    text = "Aanwezig"
        self.setToolTip(text)


class YearOverview(QWidget):
    """Jaaroverzicht met 12 maanden. Groepsrij per functie + telling per dag."""
    def __init__(self, session, year):
        super().__init__()
        self.session = session
        self.year = year

        # cache voor presence per datum (scheelt heel veel calls)
        self._presence_cache = {}

        self._build_ui()
        self._build_months()

    # ---- UI skeleton ----
    def _build_ui(self):
        self.root = QVBoxLayout(self)

        # Toolbar
        toolbar = QHBoxLayout()

        self.prev_btn = QPushButton("◀ Vorig jaar")
        self.prev_btn.clicked.connect(self._prev_year)

        self.next_btn = QPushButton("Volgend jaar ▶")
        self.next_btn.clicked.connect(self._next_year)

        self.year_label = QLabel(f"{self.year} – Jaaroverzicht")
        self.year_label.setStyleSheet("font-weight:600;")

        self.code_cb = QComboBox()
        self._reload_codes()
        self.apply_btn = QPushButton("Plan verlof")
        self.apply_btn.clicked.connect(self._apply_code)
        self.clear_btn = QPushButton("Verwijder verlof")
        self.clear_btn.clicked.connect(self._clear_code)

        self.month_jump = QComboBox()
        self.month_jump.addItems(MONTHS[1:])
        self.month_jump.currentIndexChanged.connect(self._jump_to_month)

        toolbar.addWidget(self.prev_btn)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.year_label)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.next_btn)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Ga naar maand:"))
        toolbar.addWidget(self.month_jump)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("Code:"))
        toolbar.addWidget(self.code_cb)
        toolbar.addWidget(self.apply_btn)
        toolbar.addWidget(self.clear_btn)
        self.root.addLayout(toolbar)

        # Scroll container
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.root.addWidget(self.scroll)

    # presence-cache helper
    def _presence_for(self, d: date):
        val = self._presence_cache.get(d)
        if val is None:
            val = presence_count(d, self.session) if _HAS_PRESENCE else {}
            self._presence_cache[d] = val
        return val

    def _build_months(self):
        # Data (resources + codes) ophalen
        self.resources = (
            self.session.query(Resource)
            .join(Resource.role)
            .order_by(Role.name, Resource.last_name, Resource.first_name)
            .all()
        )
        self.code_lookup = {c.code: c for c in self.session.query(LeaveCode).all()}

        # Inner widget met 12 maanden
        self.inner = QWidget()
        self.inner_layout = QVBoxLayout(self.inner)
        self.month_widgets = []
        self.month_labels = []

        # tijdens opbouw UI-updates uitzetten om thrash/lag te voorkomen
        self.setUpdatesEnabled(False)

        for m in range(1, 13):
            lbl = QLabel(MONTHS[m])
            lbl.setStyleSheet("font-weight:600; padding:6px;")
            self.inner_layout.addWidget(lbl)
            mg = MonthGrid(self.session, self.year, m, self.resources, self.code_lookup,
                           presence_provider=self._presence_for)
            mg.setMouseTracking(True)
            self.month_widgets.append(mg)
            self.month_labels.append(lbl)
            self.inner_layout.addWidget(mg)

        self.inner_layout.addStretch()
        self.scroll.setWidget(self.inner)

        # updates weer aan + event loop laten bijwerken
        self.setUpdatesEnabled(True)
        QCoreApplication.processEvents()

    # ---- toolbar actions ----
    def _reload_codes(self):
        self.code_cb.clear()
        for c in self.session.query(LeaveCode).all():
            self.code_cb.addItem(f"{c.code} – {c.label}", c.code)

    def _apply_code(self):
        code = self.code_cb.currentData()
        for mg in self.month_widgets:
            if mg.selectedRanges():
                mg.apply_code_to_selection(code)
                break

    def _clear_code(self):
        for mg in self.month_widgets:
            if mg.selectedRanges():
                mg.clear_code_on_selection()
                break

    def _jump_to_month(self, idx: int):
        if 0 <= idx < len(self.month_labels):
            target = self.month_labels[idx]
            self.scroll.ensureWidgetVisible(target, 0, 10)

    # ---- jaar wisselen ----
    def _rebuild_months_for_year(self):
        # label alvast bijwerken
        self.year_label.setText(f"{self.year} – Jaaroverzicht")

        # presence cache resetten (nieuw jaar = nieuwe datums)
        self._presence_cache.clear()

        # UI updates tijdelijk uit
        self.setUpdatesEnabled(False)

        # oude inner loskoppelen
        old = self.scroll.takeWidget()
        if old:
            old.setParent(None)

        # opnieuw opbouwen
        self._build_months()

        # updates weer aan + event loop flush
        self.setUpdatesEnabled(True)
        QCoreApplication.processEvents()

    def _prev_year(self):
        self.year -= 1
        self._rebuild_months_for_year()

    def _next_year(self):
        self.year += 1
        self._rebuild_months_for_year()

    def soft_refresh(self):
        """
        Ververs alleen de celinhoud van alle maandrasters.
        Geen herbouw van widgets, geen tabwissel, geen flicker.
        """
        # geen dure presence-herberekening per call: cache mag blijven
        for mg in getattr(self, "month_widgets", []):
            try:
                mg.setUpdatesEnabled(False)
                mg.refresh_cells()
            finally:
                mg.setUpdatesEnabled(True)
        # laat Qt de repaint afronden
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
