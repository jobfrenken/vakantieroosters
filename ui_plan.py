# ui_plan.py
from __future__ import annotations

import calendar
from datetime import date, timedelta
from collections import defaultdict

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QDateEdit, QCheckBox, QScrollArea, QFrame, QGridLayout, QMessageBox,
    QSizePolicy
)

from models import Resource, LeaveCode, Vacation, FixedOffDay, PublicHoliday


WEEKDAY_FULL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]


def _daterange(d0: date, d1: date):
    cur = d0
    step = timedelta(days=1)
    while cur <= d1:
        yield cur
        cur += step


class PlanLeave(QWidget):
    """
    Tab 'Plan Verlof':
    - Selecteer medewerker
    - Datum van/tot + code
    - Plan / Verwijder
    - Overzicht van maanden waarin verlof is ingepland
      (geen verlof? toon altijd huidige + volgende maand)
    - Signals:
        * planning_committed: na commit → MainWindow.refresh_all()
    """

    planning_committed = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._readonly = True
        self._building = False

        self._build_ui()
        self._load_initials()

    # ----------------------------- UI -----------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Top: medewerker + historie
        row0 = QHBoxLayout()
        row0.setSpacing(8)
        row0.addWidget(QLabel("Medewerker:"), 0)
        self.cb_resource = QComboBox()
        self.cb_resource.currentIndexChanged.connect(self._on_resource_changed)
        self.cb_resource.setMinimumWidth(260)
        row0.addWidget(self.cb_resource, 1)

        self.chk_history = QCheckBox("Historie tonen")
        self.chk_history.toggled.connect(self._rebuild_overview)
        row0.addWidget(self.chk_history, 0)

        row0.addStretch(1)
        root.addLayout(row0)

        # Vaste vrije dagen – tekst
        self.lbl_fixed_days = QLabel("")
        self.lbl_fixed_days.setWordWrap(True)
        self.lbl_fixed_days.setStyleSheet("color: #444;")
        root.addWidget(self.lbl_fixed_days)

        # Plan-balk
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Van:"), 0)
        self.de_from = QDateEdit()
        self.de_from.setCalendarPopup(True)
        self.de_from.setDate(QDate.currentDate())
        bar.addWidget(self.de_from, 0)

        bar.addWidget(QLabel("Tot en met:"), 0)
        self.de_to = QDateEdit()
        self.de_to.setCalendarPopup(True)
        self.de_to.setDate(QDate.currentDate())
        bar.addWidget(self.de_to, 0)

        bar.addWidget(QLabel("Code:"), 0)
        self.cb_code = QComboBox()
        bar.addWidget(self.cb_code, 0)

        bar.addStretch(1)

        self.apply_btn = QPushButton("Plan")
        self.apply_btn.clicked.connect(self.apply_leave)
        bar.addWidget(self.apply_btn, 0)

        self.clear_btn = QPushButton("Verwijder")
        self.clear_btn.setToolTip("Verwijder verlof binnen de gekozen datums voor de geselecteerde medewerker.")
        self.clear_btn.clicked.connect(self.clear_leave)
        bar.addWidget(self.clear_btn, 0)

        root.addLayout(bar)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # Titel voor overzicht
        self.lbl_overview_title = QLabel("Ingepland verlof (huidig + volgend jaar)")
        self.lbl_overview_title.setStyleSheet("font-weight: 600;")
        root.addWidget(self.lbl_overview_title)

        # Scroll area met maand-rasters
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(12)
        self.container.setStyleSheet("""
            QLabel { font-size: 12px; }
        """)
        self.scroll.setWidget(self.container)

        root.addWidget(self.scroll, 1)

        # Statusregel
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color:#666;")
        root.addWidget(self.lbl_status)

        # Init readonly state
        self.set_readonly(True)

    # ----------------------------- Loaders -----------------------------
    def _load_initials(self):
        # Resources
        self.cb_resource.blockSignals(True)
        self.cb_resource.clear()
        q = (
            self.session.query(Resource)
            .join(Resource.role)
            .order_by(Resource.role_id, Resource.last_name, Resource.first_name)
        )
        self._resources = q.all()
        for r in self._resources:
            self.cb_resource.addItem(r.full_name or f"#{r.id}", r.id)
        self.cb_resource.blockSignals(False)

        # Codes
        self.cb_code.clear()
        codes = self.session.query(LeaveCode).order_by(LeaveCode.code).all()
        self._codes_by_code = {c.code: c for c in codes}
        for c in codes:
            label = f"{c.code} – {c.label}"
            if c.absence_fraction and c.absence_fraction < 1.0:
                label += f" ({c.absence_fraction:.1f} dag)"
            self.cb_code.addItem(label, c.code)

        # Init resource
        if self.cb_resource.count() > 0:
            self.cb_resource.setCurrentIndex(0)
        else:
            self._render_no_resource()
            return

        self._rebuild_overview()

    # ----------------------------- Helpers -----------------------------
    def _current_resource(self) -> Resource | None:
        if self.cb_resource.count() == 0:
            return None
        rid = self.cb_resource.currentData()
        if rid is None:
            return None
        return self.session.get(Resource, rid)

    def _resource_fixed_days_text(self, r: Resource) -> str:
        wds = sorted(set(f.weekday for f in r.fixed_off_days))
        if not wds:
            return "Vaste vrije dagen: (geen)"
        names = [WEEKDAY_FULL[w] for w in wds]
        return "Vaste vrije dagen: " + ", ".join(names)

    def _is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5

    def _is_public_holiday(self, d: date) -> bool:
        return (
            self.session.query(PublicHoliday)
            .filter(PublicHoliday.date == d)
            .count()
            > 0
        )

    def _is_fixed_off_for(self, r: Resource, d: date) -> bool:
        wd = d.weekday()
        return any(f.weekday == wd for f in r.fixed_off_days)

    # ----------------------------- Overview build -----------------------------
    def _on_resource_changed(self, _idx: int):
        self._update_fixed_days_label()
        self._rebuild_overview()

    def _update_fixed_days_label(self):
        r = self._current_resource()
        if r:
            self.lbl_fixed_days.setText(self._resource_fixed_days_text(r))
        else:
            self.lbl_fixed_days.setText("")

    def _render_no_resource(self):
        """Toon nette lege staat wanneer er nog geen medewerkers zijn."""
        # wis rasters
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.lbl_overview_title.setText("Ingepland verlof")
        self.lbl_fixed_days.setText("")
        self.lbl_status.setText("Er zijn nog geen medewerkers aangemaakt. Ga naar 'Resources en Codes' om medewerkers toe te voegen.")
        placeholder = QLabel("Geen medewerker(s) beschikbaar.")
        placeholder.setStyleSheet("color:#666;")
        self.container_layout.addWidget(placeholder)

    def _month_box(self, year: int, month: int, days_dict: dict[date, str]) -> QWidget:
        """
        Bouwt een compact raster (7 kolommen) met codes als badges.
        """
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        box.setStyleSheet("""
            QFrame { border: 1px solid #e0e0e0; border-radius: 6px; }
            QLabel.monthTitle { font-weight: 600; font-size: 14px; }
            QLabel.weekHdr { font-weight: 600; font-size: 12px; }
            QLabel.day { font-size: 12px; }
            QLabel.badge {
                background:#cde6ff; border:1px solid #9ac7f7;
                padding:0px 4px; border-radius:3px; font-weight:600; font-size: 11px;
            }
        """)
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        title = QLabel(f"{calendar.month_name[month]} {year}")
        title.setObjectName("monthTitle")
        title.setProperty("class", "monthTitle")
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        v.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)

        # Weekdagkoppen
        for i, wd in enumerate(["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]):
            lbl = QLabel(wd)
            lbl.setObjectName("weekHdr")
            lbl.setProperty("class", "weekHdr")
            grid.addWidget(lbl, 0, i, Qt.AlignLeft)

        # Kalender
        cal = calendar.Calendar(firstweekday=0)  # 0=ma
        row = 1
        for week in cal.monthdatescalendar(year, month):
            for col, d in enumerate(week):
                if d.month != month:
                    grid.addWidget(QLabel(""), row, col)
                    continue
                day_lbl = QLabel(str(d.day))
                day_lbl.setObjectName("day")
                day_lbl.setProperty("class", "day")
                if d in days_dict:
                    code = days_dict[d]
                    badge = QLabel(code)
                    badge.setObjectName("badge")
                    badge.setProperty("class", "badge")
                    badge.setAlignment(Qt.AlignCenter)
                    cell = QVBoxLayout()
                    cell.setSpacing(0)
                    cell.setContentsMargins(0, 0, 0, 0)
                    w = QWidget()
                    w.setLayout(cell)
                    cell.addWidget(day_lbl, 0, Qt.AlignLeft)
                    cell.addWidget(badge, 0, Qt.AlignLeft)
                    grid.addWidget(w, row, col)
                else:
                    grid.addWidget(day_lbl, row, col)
            row += 1

        v.addLayout(grid)

        # Zorg dat de box niet “oprekt” tot schermhoogte
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        box.setMaximumHeight(box.sizeHint().height())

        return box

    def _rebuild_overview(self):
        if self._building:
            return
        self._building = True
        try:
            # Wis container
            while self.container_layout.count():
                item = self.container_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            r = self._current_resource()
            if not r:
                self._render_no_resource()
                return

            # Query vacations
            q = self.session.query(Vacation).filter(Vacation.resource_id == r.id)

            if not self.chk_history.isChecked():
                y0 = date.today().year
                q = q.filter(Vacation.date >= date(y0, 1, 1), Vacation.date <= date(y0 + 1, 12, 31))
                self.lbl_overview_title.setText("Ingepland verlof (huidig + volgend jaar)")
            else:
                self.lbl_overview_title.setText("Ingepland verlof (alle jaren)")

            vacations = q.all()
            days_by_month = defaultdict(dict)  # (year, month) -> {date: code}
            for v in vacations:
                ym = (v.date.year, v.date.month)
                days_by_month[ym][v.date] = v.code

            # 1) Als er maanden met verlof zijn → toon die maanden.
            # 2) Zo niet → toon ALTIJD huidige maand + volgende maand (leeg raster).
            if days_by_month:
                for y, m in sorted(days_by_month.keys()):
                    box = self._month_box(y, m, days_by_month[(y, m)])
                    self.container_layout.addWidget(box)
                self.lbl_status.setText("")
            else:
                # Geen verlof → toon huidige + volgende maand
                today = date.today()
                y1, m1 = today.year, today.month
                if m1 == 12:
                    y2, m2 = y1 + 1, 1
                else:
                    y2, m2 = y1, m1 + 1

                self.container_layout.addWidget(self._month_box(y1, m1, {}))
                self.container_layout.addWidget(self._month_box(y2, m2, {}))
                self.lbl_status.setText("Er is nog geen verlof ingepland voor deze medewerker.")

            # Update vaste vrije dagen label
            self._update_fixed_days_label()

        finally:
            self._building = False

    # ----------------------------- Actions -----------------------------
    def _selected_range(self) -> tuple[date, date] | None:
        d0 = self.de_from.date().toPython()
        d1 = self.de_to.date().toPython()
        if d1 < d0:
            d0, d1 = d1, d0
        return (d0, d1)

    def _working_day_for(self, r: Resource, d: date) -> bool:
        """Alleen dagen die meetellen als werkdagen (geen weekend/feestdag/vaste vrije dag)."""
        if d.weekday() >= 5:
            return False
        if self.session.query(PublicHoliday).filter(PublicHoliday.date == d).count() > 0:
            return False
        if any(f.weekday == d.weekday() for f in r.fixed_off_days):
            return False
        return True

    def apply_leave(self):
        r = self._current_resource()
        if not r:
            QMessageBox.warning(self, "Plan", "Geen medewerker geselecteerd.")
            return

        rng = self._selected_range()
        if not rng:
            QMessageBox.warning(self, "Plan", "Selecteer een geldige periode (van/tot).")
            return

        code = self.cb_code.currentData()
        if not code:
            QMessageBox.warning(self, "Plan", "Selecteer een verlofcode.")
            return

        d0, d1 = rng
        made = 0
        for d in _daterange(d0, d1):
            if not self._working_day_for(r, d):
                continue
            existing = (
                self.session.query(Vacation)
                .filter(Vacation.resource_id == r.id, Vacation.date == d)
                .one_or_none()
            )
            if existing:
                existing.code = code
            else:
                self.session.add(Vacation(resource_id=r.id, date=d, code=code))
            made += 1

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            QMessageBox.critical(self, "Plan", f"Opslaan mislukt:\n{e}")
            return

        self.planning_committed.emit()
        self._rebuild_overview()
        self.lbl_status.setText(f"Gepland: {made} dag(en).")

    def clear_leave(self):
        r = self._current_resource()
        if not r:
            QMessageBox.warning(self, "Verwijderen", "Geen medewerker geselecteerd.")
            return

        rng = self._selected_range()
        if not rng:
            QMessageBox.warning(self, "Verwijderen", "Selecteer een geldige periode (van/tot).")
            return

        d0, d1 = rng
        q = (
            self.session.query(Vacation)
            .filter(
                Vacation.resource_id == r.id,
                Vacation.date >= d0,
                Vacation.date <= d1,
            )
        )
        count = q.count()
        if count == 0:
            self.lbl_status.setText("Niets te verwijderen in de gekozen periode.")
            return

        try:
            q.delete(synchronize_session=False)
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            QMessageBox.critical(self, "Verwijderen", f"Verwijderen mislukt:\n{e}")
            return

        self.planning_committed.emit()
        self._rebuild_overview()
        self.lbl_status.setText(f"Verwijderd: {count} dag(en).")

    # ----------------------------- Public API -----------------------------
    def set_readonly(self, ro: bool):
        self._readonly = ro
        try:
            self.apply_btn.setEnabled(not ro)
        except Exception:
            pass
        try:
            self.clear_btn.setEnabled(not ro)
        except Exception:
            pass

    def refresh_if_readonly(self):
        """Door MainWindow elke 30s aangeroepen voor kijkers."""
        if self._readonly:
            self._rebuild_overview()

    def hard_refresh(self):
        """Handmatige/centrale verversing (F5)."""
        self._load_initials()
