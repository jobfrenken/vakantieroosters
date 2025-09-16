# ui_resources.py
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QLabel,
    QPushButton, QLineEdit, QSpinBox, QComboBox, QFrame, QMessageBox, QGridLayout,
    QSizePolicy
)

from models import Role, Resource, LeaveCode, FixedOffDay

DAY_LONG = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]


class ResourcesScreen(QWidget):
    """
    Lay-out (3 kolommen):
    ┌───────────────┬───────────────────────────┬─────────────────────────────────────┐
    │ Functies (rol)│ Medewerkers (lijst; form+knoppen onder) │ Vaste vrije dagen (boven)  │
    │ (lijst; form+ │                                   │ Verlof-codes (onder; gelijk   │
    │ knoppen onder)│                                   │ in breedte aan vaste vrije)    │
    └───────────────┴───────────────────────────┴─────────────────────────────────────┘
    """
    data_changed = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._build_ui()
        self.reload_all()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(12)

        # ===== kolom 1: Rollen =====
        col_roles = QVBoxLayout()
        col_roles.setSpacing(6)

        lbl_roles = QLabel("Functies (rol)")
        lbl_roles.setStyleSheet("font-weight:600;")
        col_roles.addWidget(lbl_roles)

        self.lst_roles = QListWidget()
        self.lst_roles.currentItemChanged.connect(self._on_role_selected)
        self.lst_roles.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        col_roles.addWidget(self.lst_roles, 1)

        # alles onderin: form + knoppen
        col_roles.addStretch(1)

        grid_role = QGridLayout()
        grid_role.setHorizontalSpacing(6)
        grid_role.setVerticalSpacing(4)

        grid_role.addWidget(QLabel("Naam:"), 0, 0)
        self.ed_role_name = QLineEdit(); grid_role.addWidget(self.ed_role_name, 0, 1, 1, 3)

        grid_role.addWidget(QLabel("Min/dag:"), 1, 0)
        self.sp_role_min = QSpinBox(); self.sp_role_min.setRange(0, 9999); grid_role.addWidget(self.sp_role_min, 1, 1)
        grid_role.addWidget(QLabel("Max/dag:"), 1, 2)
        self.sp_role_max = QSpinBox(); self.sp_role_max.setRange(0, 9999); grid_role.addWidget(self.sp_role_max, 1, 3)
        col_roles.addLayout(grid_role)

        row_role_btn = QHBoxLayout()
        self.btn_role_delete = QPushButton("Rol verwijderen"); self.btn_role_delete.clicked.connect(self._delete_role)
        self.btn_role_save = QPushButton("Rol opslaan/aanmaken"); self.btn_role_save.clicked.connect(self._save_role)
        row_role_btn.addWidget(self.btn_role_delete); row_role_btn.addWidget(self.btn_role_save)
        col_roles.addLayout(row_role_btn)

        root.addLayout(col_roles, 1)

        # ===== kolom 2: Medewerkers (lijst boven; form + knoppen onder) =====
        col_mid = QVBoxLayout()
        col_mid.setSpacing(6)

        lbl_res = QLabel("Medewerkers")
        lbl_res.setStyleSheet("font-weight:600;")
        col_mid.addWidget(lbl_res)

        self.lst_resources = QListWidget()
        self.lst_resources.currentItemChanged.connect(self._on_resource_selected)
        self.lst_resources.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        col_mid.addWidget(self.lst_resources, 5)

        # alles onderin
        col_mid.addStretch(1)

        form_res = QGridLayout()
        form_res.setHorizontalSpacing(8)
        form_res.setVerticalSpacing(4)

        form_res.addWidget(QLabel("Voornaam:"), 0, 0)
        self.ed_first = QLineEdit(); form_res.addWidget(self.ed_first, 0, 1, 1, 2)

        form_res.addWidget(QLabel("Achternaam:"), 1, 0)
        self.ed_last = QLineEdit(); form_res.addWidget(self.ed_last, 1, 1, 1, 2)

        form_res.addWidget(QLabel("Functie:"), 2, 0)
        self.cb_role_for_resource = QComboBox(); form_res.addWidget(self.cb_role_for_resource, 2, 1, 1, 2)
        col_mid.addLayout(form_res)

        row_res_btn = QHBoxLayout()
        self.btn_res_save = QPushButton("Medewerker opslaan/aanmaken"); self.btn_res_save.clicked.connect(self._save_resource)
        self.btn_res_del = QPushButton("Verwijder geselecteerde medewerker"); self.btn_res_del.clicked.connect(self._delete_resource)
        row_res_btn.addWidget(self.btn_res_save); row_res_btn.addWidget(self.btn_res_del)
        col_mid.addLayout(row_res_btn)

        root.addLayout(col_mid, 3)

        # ===== kolom 3: Rechts (boven: vaste vrije; onder: codes) =====
        col_right = QVBoxLayout()
        col_right.setSpacing(10)

        # --- vaste vrije dagen ---
        frame_fixed = QFrame()
        frame_fixed.setFrameShape(QFrame.NoFrame)
        vfixed = QVBoxLayout(frame_fixed); vfixed.setSpacing(6)

        lbl_fixed = QLabel("Vaste vrije dagen (selecteer medewerker links)")
        lbl_fixed.setStyleSheet("font-weight:600;")
        vfixed.addWidget(lbl_fixed)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)   # compacter, direct naast labels
        grid.setVerticalSpacing(2)

        self.fixed_combos: list[QComboBox] = []
        for wd in range(7):
            lab = QLabel(DAY_LONG[wd])
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(lab, wd, 0)

            cb = QComboBox()
            cb.addItems(["Geen", "VV (hele dag)", "VO (ochtend)", "VM (middag)"])
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.fixed_combos.append(cb)
            grid.addWidget(cb, wd, 1)

        vfixed.addLayout(grid)

        # knop onderaan dit blok
        self.btn_save_fixed = QPushButton("Opslaan vaste vrije dagen")
        self.btn_save_fixed.clicked.connect(self._save_fixed_off_days)
        vfixed.addWidget(self.btn_save_fixed)

        # --- separator ---
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)

        # --- verlof-codes ---
        frame_codes = QFrame()
        frame_codes.setFrameShape(QFrame.NoFrame)
        vcodes = QVBoxLayout(frame_codes); vcodes.setSpacing(6)

        lbl_codes = QLabel("Verlof-codes")
        lbl_codes.setStyleSheet("font-weight:600;")
        vcodes.addWidget(lbl_codes)

        self.lst_codes = QListWidget()
        self.lst_codes.currentItemChanged.connect(self._on_code_selected)
        self.lst_codes.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vcodes.addWidget(self.lst_codes, 2)

        formc = QGridLayout()
        formc.setHorizontalSpacing(8)
        formc.setVerticalSpacing(4)

        formc.addWidget(QLabel("Code:"), 0, 0)
        self.ed_code = QLineEdit(); formc.addWidget(self.ed_code, 0, 1, 1, 3)

        formc.addWidget(QLabel("Omschrijving:"), 1, 0)
        self.ed_code_label = QLineEdit(); formc.addWidget(self.ed_code_label, 1, 1, 1, 3)

        formc.addWidget(QLabel("Kleur (#RRGGBB):"), 2, 0)
        self.ed_code_color = QLineEdit(); self.ed_code_color.setText("#C6E6C6")
        formc.addWidget(self.ed_code_color, 2, 1)

        self.cb_counts_abs = QComboBox(); self.cb_counts_abs.addItems(["Telt als afwezig", "Telt NIET als afwezig"])
        formc.addWidget(self.cb_counts_abs, 2, 2, 1, 2)

        formc.addWidget(QLabel("Afwezigheid:"), 3, 0)
        self.cb_abs_frac = QComboBox(); self.cb_abs_frac.addItems(["Geen (0.0)", "Half (0.5)", "Hele dag (1.0)"])
        formc.addWidget(self.cb_abs_frac, 3, 1, 1, 3)

        vcodes.addLayout(formc)

        # knoppen onderaan het codes-blok
        rowc = QHBoxLayout()
        self.btn_code_save = QPushButton("Code opslaan/aanmaken"); self.btn_code_save.clicked.connect(self._save_code)
        self.btn_code_del = QPushButton("Code verwijderen"); self.btn_code_del.clicked.connect(self._delete_code)
        rowc.addWidget(self.btn_code_save); rowc.addWidget(self.btn_code_del)
        vcodes.addLayout(rowc)

        # rechts samenstellen (zelfde breedte voor beide blokken)
        col_right.addWidget(frame_fixed, 1)
        col_right.addWidget(sep)
        col_right.addWidget(frame_codes, 2)

        root.addLayout(col_right, 3)

    # ---------------- Loaders / Reload ----------------
    def reload(self):
        self.reload_all()

    def reload_all(self):
        self._load_roles()
        self._load_role_dropdown()
        self._load_resources()
        self._load_codes()
        self._clear_fixed_off_ui()

    def _load_roles(self):
        self.lst_roles.clear()
        roles = self.session.query(Role).order_by(Role.name).all()
        for r in roles:
            txt = f"{r.name} (min {r.min_required_per_day or 0}, max {r.max_allowed_per_day or 999})"
            item = QListWidgetItem(txt); item.setData(Qt.UserRole, r.id)
            self.lst_roles.addItem(item)

    def _load_role_dropdown(self):
        self.cb_role_for_resource.clear()
        for r in self.session.query(Role).order_by(Role.name).all():
            self.cb_role_for_resource.addItem(r.name, r.id)

    def _load_resources(self):
        self.lst_resources.clear()
        q = (
            self.session.query(Resource)
            .join(Resource.role)
            .order_by(Role.name, Resource.last_name, Resource.first_name)
        )
        for r in q.all():
            txt = f"{r.full_name} — {r.role.name if r.role else ''}"
            item = QListWidgetItem(txt); item.setData(Qt.UserRole, r.id)
            self.lst_resources.addItem(item)

    def _load_codes(self):
        self.lst_codes.clear()
        for c in self.session.query(LeaveCode).order_by(LeaveCode.code).all():
            suffix = " [absent]" if c.counts_as_absent else " [aanwezig]"
            col = f" ({c.color_hex})" if c.color_hex else ""
            item = QListWidgetItem(f"{c.code} — {c.label}{col}{suffix}")
            item.setData(Qt.UserRole, c.id)
            self.lst_codes.addItem(item)

    # ---------------- Rollen actions ----------------
    def _on_role_selected(self, cur: QListWidgetItem, _prev):
        if not cur:
            self.ed_role_name.clear(); self.sp_role_min.setValue(0); self.sp_role_max.setValue(999)
            return
        rid = cur.data(Qt.UserRole)
        r = self.session.get(Role, rid)
        if not r: return
        self.ed_role_name.setText(r.name or "")
        self.sp_role_min.setValue(r.min_required_per_day or 0)
        self.sp_role_max.setValue(r.max_allowed_per_day or 999)

    def _save_role(self):
        name = (self.ed_role_name.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Rol", "Naam is verplicht."); return
        minv = self.sp_role_min.value(); maxv = self.sp_role_max.value()
        cur = self.lst_roles.currentItem()
        if cur:
            rid = cur.data(Qt.UserRole); r = self.session.get(Role, rid)
        else:
            r = Role(); self.session.add(r)
        r.name = name; r.min_required_per_day = minv; r.max_allowed_per_day = maxv
        self.session.commit(); self.reload_all(); self.data_changed.emit()

    def _delete_role(self):
        cur = self.lst_roles.currentItem()
        if not cur: return
        rid = cur.data(Qt.UserRole); r = self.session.get(Role, rid)
        if not r: return
        if self.session.query(Resource).filter(Resource.role_id == r.id).count() > 0:
            QMessageBox.warning(self, "Rol", "Er zijn nog medewerkers met deze rol.")
            return
        self.session.delete(r); self.session.commit(); self.reload_all(); self.data_changed.emit()

    # ---------------- Medewerkers actions ----------------
    def _on_resource_selected(self, cur: QListWidgetItem, _prev):
        self._clear_fixed_off_ui()
        if not cur:
            self.ed_first.clear(); self.ed_last.clear()
            if self.cb_role_for_resource.count() > 0: self.cb_role_for_resource.setCurrentIndex(0)
            return
        rid = cur.data(Qt.UserRole); r = self.session.get(Resource, rid)
        if not r: return
        self.ed_first.setText(r.first_name or ""); self.ed_last.setText(r.last_name or "")
        idx = max(0, self.cb_role_for_resource.findData(r.role_id)); self.cb_role_for_resource.setCurrentIndex(idx)

        fods = self.session.query(FixedOffDay).filter_by(resource_id=r.id).all()
        by_wd = {f.weekday: f for f in fods}
        for wd in range(7):
            f = by_wd.get(wd); sel = 0
            if f:
                p = (f.part or "FULL").upper()
                if p == "FULL": sel = 1
                elif p == "AM": sel = 2
                elif p == "PM": sel = 3
            self.fixed_combos[wd].setCurrentIndex(sel)

    def _save_resource(self):
        first = (self.ed_first.text() or "").strip()
        last = (self.ed_last.text() or "").strip()
        role_id = self.cb_role_for_resource.currentData()
        if not last:
            QMessageBox.warning(self, "Medewerker", "Achternaam is verplicht."); return
        cur = self.lst_resources.currentItem()
        if cur:
            rid = cur.data(Qt.UserRole); r = self.session.get(Resource, rid)
        else:
            r = Resource(); self.session.add(r)
        r.first_name = first; r.last_name = last; r.role_id = role_id
        self.session.commit(); self.reload_all(); self.data_changed.emit()

    def _delete_resource(self):
        cur = self.lst_resources.currentItem()
        if not cur: return
        rid = cur.data(Qt.UserRole); r = self.session.get(Resource, rid)
        if not r: return
        self.session.delete(r); self.session.commit(); self.reload_all(); self.data_changed.emit()

    # ---- vaste vrije dagen ----
    def _clear_fixed_off_ui(self):
        for cb in self.fixed_combos:
            cb.setCurrentIndex(0)

    def _save_fixed_off_days(self):
        cur = self.lst_resources.currentItem()
        if not cur:
            QMessageBox.information(self, "Vaste vrije dagen", "Selecteer eerst een medewerker."); return
        rid = cur.data(Qt.UserRole); r = self.session.get(Resource, rid)
        if not r: return
        self.session.query(FixedOffDay).filter_by(resource_id=r.id).delete()
        for wd, cb in enumerate(self.fixed_combos):
            sel = cb.currentIndex()
            if sel == 0: continue
            if sel == 1: part, frac = "FULL", 1.0
            elif sel == 2: part, frac = "AM", 0.5
            else: part, frac = "PM", 0.5
            self.session.add(FixedOffDay(resource_id=r.id, weekday=wd, part=part, absence_fraction=frac))
        self.session.commit()
        self.data_changed.emit()
        QMessageBox.information(self, "Vaste vrije dagen", "Opgeslagen.")

    # ---------------- Codes actions ----------------
    def _on_code_selected(self, cur: QListWidgetItem, _prev):
        if not cur:
            self.ed_code.clear(); self.ed_code_label.clear(); self.ed_code_color.setText("#C6E6C6")
            self.cb_counts_abs.setCurrentIndex(0); self.cb_abs_frac.setCurrentIndex(0); return
        cid = cur.data(Qt.UserRole); c = self.session.get(LeaveCode, cid)
        if not c: return
        self.ed_code.setText(c.code or ""); self.ed_code_label.setText(c.label or "")
        self.ed_code_color.setText(c.color_hex or "#C6E6C6")
        self.cb_counts_abs.setCurrentIndex(0 if c.counts_as_absent else 1)
        frac = (c.absence_fraction if c.absence_fraction is not None else (1.0 if c.counts_as_absent else 0.0))
        self.cb_abs_frac.setCurrentIndex(2 if frac >= 0.99 else (1 if frac >= 0.49 else 0))

    def _save_code(self):
        code = (self.ed_code.text() or "").strip().upper()
        label = (self.ed_code_label.text() or "").strip()
        color = (self.ed_code_color.text() or "").strip() or "#C6E6C6"
        counts = (self.cb_counts_abs.currentIndex() == 0)
        idx = self.cb_abs_frac.currentIndex()
        frac = 1.0 if idx == 2 else (0.5 if idx == 1 else 0.0)
        if not code:
            QMessageBox.warning(self, "Code", "Code is verplicht."); return
        cur = self.lst_codes.currentItem()
        if cur:
            cid = cur.data(Qt.UserRole); c = self.session.get(LeaveCode, cid)
            if not c: c = LeaveCode(); self.session.add(c)
        else:
            c = LeaveCode(); self.session.add(c)
        c.code = code; c.label = label; c.color_hex = color
        c.counts_as_absent = counts; c.absence_fraction = frac
        self.session.commit(); self.reload_all(); self.data_changed.emit()

    def _delete_code(self):
        cur = self.lst_codes.currentItem()
        if not cur: return
        cid = cur.data(Qt.UserRole); c = self.session.get(LeaveCode, cid)
        if not c: return
        self.session.delete(c); self.session.commit(); self.reload_all(); self.data_changed.emit()

    # ---------- readonly ----------
    def set_readonly(self, ro: bool):
        for w in [
            self.btn_role_save, self.btn_role_delete,
            self.btn_res_save, self.btn_res_del,
            self.btn_code_save, self.btn_code_del,
            self.btn_save_fixed,
            self.ed_role_name, self.sp_role_min, self.sp_role_max,
            self.ed_first, self.ed_last, self.cb_role_for_resource,
            self.ed_code, self.ed_code_label, self.ed_code_color,
            self.cb_counts_abs, self.cb_abs_frac,
        ] + self.fixed_combos:
            try: w.setEnabled(not ro)
            except Exception: pass
