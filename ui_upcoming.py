from datetime import date
from calendar import monthrange

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QScrollArea, QVBoxLayout
)
from PySide6.QtCore import Qt

from models import Resource, LeaveCode, Role, FixedOffDay
from ui_year import MonthGrid, MONTHS, _vv_human  # hergebruik MonthGrid en helper
from logic import holidays_between  # alleen voor consistent import

class UpcomingMonths(QWidget):
    """
    Compact overzicht voor komende N maanden (standaard 2).
    Gebruikt dezelfde MonthGrid als jaaroverzicht (incl. VV/VO/VM tooltips).
    """
    def __init__(self, session, months=2):
        super().__init__()
        self.session = session
        self.months = max(1, int(months))

        v = QVBoxLayout(self)

        lbl = QLabel(f"Komende {self.months} maanden")
        lbl.setStyleSheet("font-weight:600; padding:6px;")
        v.addWidget(lbl)

        # Data
        self.resources = (
            self.session.query(Resource)
            .join(Resource.role)
            .order_by(Role.name, Resource.last_name, Resource.first_name)
            .all()
        )
        code_lookup = {c.code: c for c in self.session.query(LeaveCode).all()}

        # Scroll container
        self.scroll = QScrollArea()
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        # begin bij huidige maand
        today = date.today()
        y, m = today.year, today.month

        self.month_widgets = []
        for i in range(self.months):
            mm = m + i
            yy = y + (mm - 1) // 12
            real_m = ((mm - 1) % 12) + 1

            cap = QLabel(f"{MONTHS[real_m]} {yy}")
            cap.setStyleSheet("font-weight:600; padding:4px;")
            inner_layout.addWidget(cap)

            mg = MonthGrid(self.session, yy, real_m, self.resources, code_lookup)
            mg.setMouseTracking(True)
            self.month_widgets.append(mg)
            inner_layout.addWidget(mg)

        inner_layout.addStretch()
        self.scroll.setWidget(inner)
        self.scroll.setWidgetResizable(True)
        v.addWidget(self.scroll)

    def set_readonly(self, ro: bool):
        # upcoming is informatief; MonthGrid zelf behandelt selectie,
        # maar we doen hier niets extra's.
        pass
