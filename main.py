import sys, os, json
from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QFileDialog, QMessageBox, QToolBar
)

from db_init import init_db
from models import get_engine, get_session
from lockmgr import EditLock
from backupmgr import BackupManager
from ui_year import YearOverview
from ui_upcoming import UpcomingMonths
from ui_resources import ResourcesScreen
from ui_plan import PlanLeave
import updater  # <-- auto-update module

__version__ = "1.4.1"  # verhoog dit bij elke release #feestdagen toegevoegd.
APP_NAME = "VakantieRooster"

# ---------- settings helpers (per-user in LOCALAPPDATA) ----------
SETTINGS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

def _ensure_settings_dir():
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
    except Exception:
        pass

def load_settings() -> dict:
    _ensure_settings_dir()
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    return {}

def save_settings(data: dict):
    _ensure_settings_dir()
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_last_db_path() -> str:
    return (load_settings().get("last_db_path") or "").strip()

def set_last_db_path(path: str):
    s = load_settings()
    s["last_db_path"] = path
    save_settings(s)

# ---- install.json uit %ProgramData% lezen (machine-brede defaults) ----
INSTALL_JSON_PATH = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                                 "VakantieRooster", "install.json")

def load_install_defaults() -> dict:
    try:
        if os.path.exists(INSTALL_JSON_PATH):
            with open(INSTALL_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}

# Default: updates\manifest.json naast de .exe (fallback)
UPDATE_MANIFEST = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "updates", "manifest.json")

_install_defaults = load_install_defaults()
if _install_defaults:
    if not get_last_db_path() and _install_defaults.get("db_path"):
        set_last_db_path(_install_defaults["db_path"])
    if _install_defaults.get("manifest_path"):
        UPDATE_MANIFEST = _install_defaults["manifest_path"]

# ---------- file dialog ----------
def pick_db_path_dialog(parent=None) -> str:
    dlg = QFileDialog(parent, "Selecteer of maak een databasebestand")
    dlg.setFileMode(QFileDialog.AnyFile)
    dlg.setNameFilter("SQLite databases (*.db)")
    dlg.setAcceptMode(QFileDialog.AcceptSave)
    if dlg.exec():
        selected = dlg.selectedFiles()[0]
        if not selected.lower().endswith(".db"):
            selected += ".db"
        return selected
    return ""

def _dir_is_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        testfile = os.path.join(path, ".write_test.tmp")
        with open(testfile, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(testfile)
        return True
    except Exception:
        return False

def ensure_db_path() -> str:
    last = get_last_db_path()
    if last:
        parent = os.path.dirname(last)
        if parent and os.path.isdir(parent) and _dir_is_writable(parent):
            return last
    chosen = pick_db_path_dialog(None)
    if not chosen:
        return ""
    parent = os.path.dirname(chosen)
    if not _dir_is_writable(parent):
        QMessageBox.critical(None, "Pad niet schrijfbaar",
                             f"Je kunt hier geen database aanmaken/schrijven:\n{parent}")
        return ""
    set_last_db_path(chosen)
    return chosen


class MainWindow(QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} – Van Kerkhoven & Van de Ven")

        # DB/ORM
        self.db_path = db_path
        folder = os.path.dirname(self.db_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

        self.engine = get_engine(self.db_path)
        init_db(self.engine)               # tabellen/migraties/seed indien nodig
        self.session = get_session(self.engine)

        # Back-up manager (14 dagen bewaren, max 1x/120s)
        self.backup_mgr = BackupManager(self.db_path, retention_days=14, min_interval_sec=120)
        self.backup_mgr.attach_to_session(self.session)

        # Tabs
        self.tabs = QTabWidget()
        self.year_widget = YearOverview(self.session, date.today().year)
        self.upcoming_widget = UpcomingMonths(self.session, months=2)  # exact 2 maanden
        self.resources_widget = ResourcesScreen(self.session)
        self.plan_widget = PlanLeave(self.session)

        self.tabs.addTab(self.year_widget, "Jaaroverzicht")
        self.tabs.addTab(self.upcoming_widget, "Komende 2 maanden")
        self.tabs.addTab(self.resources_widget, "Resources en Codes")
        self.tabs.addTab(self.plan_widget, "Plan Verlof")
        self.setCentralWidget(self.tabs)

        # Menubalk & Toolbar
        self._build_menu()
        self._build_toolbar()

        # Exclusieve bewerkstand (start read-only)
        self.edit_lock = EditLock(self.db_path)
        self.readonly = True
        self._apply_readonly(True)
        self._start_autorefresh_timer()

        # Signalen voor auto-refresh
        try:
            self.resources_widget.data_changed.connect(self.rebuild_overviews)
        except Exception:
            pass
        try:
            self.plan_widget.planning_committed.connect(self.refresh_all)
        except Exception:
            pass

        # Optioneel: automatische check op update (melding; installatie via menu)
        QTimer.singleShot(60000, self._auto_check_update)

    # ---------- menu ----------
    def _build_menu(self):
        menubar = self.menuBar()
        m_settings = menubar.addMenu("&Instellingen")

        act_choose = m_settings.addAction("Database wijzigen…")
        act_choose.triggered.connect(self._change_database)

        act_refresh = m_settings.addAction("Verversen (F5)")
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self.refresh_all)

        act_backup_now = m_settings.addAction("Backup nu")
        act_backup_now.triggered.connect(self._backup_now)

        m_settings.addSeparator()
        act_quit = m_settings.addAction("Afsluiten")
        act_quit.triggered.connect(self.close)

        m_help = menubar.addMenu("&Help")
        act_about = m_help.addAction("Huidig databasebestand")
        act_about.triggered.connect(self._show_current_db_path)

        # Updates
        act_check = m_help.addAction("Zoek naar updates…")
        act_check.triggered.connect(self._manual_check_update)

        act_install = m_help.addAction("Update installeren vanaf bestand…")
        act_install.triggered.connect(self._manual_install_update)

    # ---------- toolbar ----------
    def _build_toolbar(self):
        tb = QToolBar("Hoofd")
        self.addToolBar(tb)

        # Verversen
        self.refresh_act = tb.addAction("Verversen")
        self.refresh_act.setToolTip("Alles verversen (F5)")
        self.refresh_act.setShortcut("F5")
        self.refresh_act.triggered.connect(self.refresh_all)

        # Exclusieve bewerkstand
        self.toggle_edit_act = tb.addAction("Bewerkstand (exclusief)")
        self.toggle_edit_act.setCheckable(True)
        self.toggle_edit_act.toggled.connect(self._toggle_edit_mode)

        # Status-label
        self.status_label_act = tb.addAction("Status: Alleen-lezen")
        self.status_label_act.setEnabled(False)

    # ---------- status helpers ----------
    def _set_status_text(self, txt: str):
        self.status_label_act.setText(txt)

    # ---------- edit mode ----------
    def _toggle_edit_mode(self, checked):
        if checked:
            if self.edit_lock.acquire():
                self._apply_readonly(False)
                holder = self.edit_lock.holder()
                self._set_status_text(f"Status: Bewerken ({holder or 'ik'})")
            else:
                holder = self.edit_lock.holder() or "iemand anders"
                QMessageBox.information(
                    self, "Bewerkstand bezet",
                    f"De bewerkstand is momenteel in gebruik door: {holder}.\n"
                    f"Je blijft nu in alleen-lezen modus."
                )
                self.toggle_edit_act.setChecked(False)
                self._apply_readonly(True)
                self._set_status_text("Status: Alleen-lezen")
        else:
            self.edit_lock.release()
            self._apply_readonly(True)
            self._set_status_text("Status: Alleen-lezen")

    def _apply_readonly(self, ro: bool):
        self.readonly = ro
        for w in (self.year_widget, self.upcoming_widget, self.resources_widget, self.plan_widget):
            if hasattr(w, "set_readonly"):
                try:
                    w.set_readonly(ro)
                except Exception:
                    pass

    # ---------- auto-refresh ----------
    def _start_autorefresh_timer(self):
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(120000)  # 120 seconden
        self._refresh_timer.timeout.connect(self._maybe_refresh)
        self._refresh_timer.start()

    def _maybe_refresh(self):
        """Alleen in read-only en zonder tabs opnieuw op te bouwen (light refresh)."""
        if self.readonly:
            self.rebuild_overviews()
            try:
                if hasattr(self.plan_widget, "refresh_if_readonly"):
                    self.plan_widget.refresh_if_readonly()
            except Exception:
                pass

    # ---------- backup ----------
    def _backup_now(self):
        dest = self.backup_mgr.run_backup_now()
        if dest:
            QMessageBox.information(self, "Back-up gemaakt", f"Back-up opgeslagen:\n{dest}")
        else:
            QMessageBox.warning(self, "Back-up mislukt", "Kon geen back-up maken. Controleer schrijfrechten en pad.")

    # ---------- DB wisselen ----------
    def _show_current_db_path(self):
        QMessageBox.information(self, "Database", f"Huidig databasebestand:\n{self.db_path or '(onbekend)'}")

    def _change_database(self):
        new_path = pick_db_path_dialog(self)
        if not new_path:
            return

        try:
            folder = os.path.dirname(new_path)
            if folder and not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)

            # lock vrijgeven als we 'm hebben
            try:
                self.edit_lock.release()
            except Exception:
                pass
            self.toggle_edit_act.setChecked(False)
            self._set_status_text("Status: Alleen-lezen")

            # oude session sluiten
            try:
                self.session.close()
            except Exception:
                pass

            # nieuwe engine/session + init
            self.db_path = new_path
            self.engine = get_engine(self.db_path)
            init_db(self.engine)
            self.session = get_session(self.engine)

            # back-up manager opnieuw koppelen
            self.backup_mgr = BackupManager(self.db_path, retention_days=14, min_interval_sec=120)
            self.backup_mgr.attach_to_session(self.session)

            # onthouden
            set_last_db_path(self.db_path)

            # nieuwe lock voor dit bestand
            self.edit_lock = EditLock(self.db_path)
            self._apply_readonly(True)

            # UI opnieuw koppelen
            self._rebind_widgets()

            QMessageBox.information(self, "Database gewisseld",
                                    f"Er wordt nu gewerkt met:\n{self.db_path}")

        except Exception as e:
            QMessageBox.critical(self, "Wisselen mislukt",
                                 f"Er ging iets mis bij het wisselen van database:\n{e}")

    # ---------- UI rebinding / verversen ----------
    def _rebind_widgets(self):
        current_index = self.tabs.currentIndex()

        # Jaaroverzicht
        year_idx = self.tabs.indexOf(self.year_widget)
        if year_idx != -1:
            self.tabs.removeTab(year_idx)
        self.year_widget.deleteLater()
        self.year_widget = YearOverview(self.session, date.today().year)
        self.tabs.insertTab(0, self.year_widget, "Jaaroverzicht")

        # Komende 2 maanden
        up_idx = self.tabs.indexOf(self.upcoming_widget)
        if up_idx != -1:
            self.tabs.removeTab(up_idx)
        self.upcoming_widget.deleteLater()
        self.upcoming_widget = UpcomingMonths(self.session, months=2)
        self.tabs.insertTab(1, self.upcoming_widget, "Komende 2 maanden")

        # Resources
        res_idx = self.tabs.indexOf(self.resources_widget)
        if res_idx != -1:
            self.tabs.removeTab(res_idx)
        self.resources_widget.deleteLater()
        self.resources_widget = ResourcesScreen(self.session)
        self.tabs.insertTab(2, self.resources_widget, "Resources en Codes")

        # Plan Verlof
        plan_idx = self.tabs.indexOf(self.plan_widget)
        if plan_idx != -1:
            self.tabs.removeTab(plan_idx)
        self.plan_widget.deleteLater()
        self.plan_widget = PlanLeave(self.session)
        self.tabs.insertTab(3, self.plan_widget, "Plan Verlof")

        try:
            self.resources_widget.data_changed.connect(self.rebuild_overviews)
        except Exception:
            pass
        try:
            self.plan_widget.planning_committed.connect(self.refresh_all)
        except Exception:
            pass

        self.tabs.setCurrentIndex(min(current_index, self.tabs.count() - 1))

    def rebuild_overviews(self):
        """Lichtgewicht verversing zonder tabs te verwijderen."""
        try:
            if hasattr(self.year_widget, "soft_refresh"):
                self.year_widget.soft_refresh()
        except Exception:
            pass
        try:
            if hasattr(self.upcoming_widget, "soft_refresh"):
                self.upcoming_widget.soft_refresh()
        except Exception:
            pass
        try:
            if hasattr(self.resources_widget, "reload"):
                self.resources_widget.reload()
        except Exception:
            pass
        try:
            if hasattr(self.plan_widget, "refresh_if_readonly"):
                self.plan_widget.refresh_if_readonly()
        except Exception:
            pass

    def refresh_all(self):
        """Handmatige verversing (F5 of na plannen) – ook lichtgewicht."""
        self.rebuild_overviews()

    # ---------- updates ----------
    def _auto_check_update(self):
        try:
            if not os.path.exists(UPDATE_MANIFEST):
                return
            m = updater.check_for_update(UPDATE_MANIFEST, __version__)
            if not m:
                return
            newver = m.get("version", "?")
            QMessageBox.information(
                self, "Update beschikbaar",
                f"Er is een nieuwe versie beschikbaar: {newver}.\n"
                f"Ga naar Help → 'Zoek naar updates…' om te installeren."
            )
        except Exception:
            pass

    def _manual_check_update(self):
        if not os.path.exists(UPDATE_MANIFEST):
            QMessageBox.information(self, "Updates", "Geen manifest gevonden (map 'updates' ontbreekt).")
            return
        m = updater.check_for_update(UPDATE_MANIFEST, __version__)
        if not m:
            QMessageBox.information(self, "Updates", "Je gebruikt de nieuwste versie.")
            return

        newver = m.get("version", "?")
        notes = m.get("notes", "")
        ok = QMessageBox.question(
            self, "Update beschikbaar",
            f"Er is een nieuwe versie beschikbaar: {newver}\n\n{notes}\n\nNu downloaden en installeren?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if ok != QMessageBox.Yes:
            return

        dest = updater.download_update(m)
        if not dest:
            QMessageBox.warning(self, "Update", "Download mislukt of onvolledig (controleer manifest/sha256).")
            return

        updater.launch_installer_and_exit(dest, silent=True)

    def _manual_install_update(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Kies update-installer", "",
            "Installers (*.exe *.msi);;Alle bestanden (*.*)"
        )
        if not path:
            return
        updater.launch_installer_and_exit(path, silent=True)

    # ---------- lifecycle ----------
    def closeEvent(self, e):
        try:
            self.edit_lock.release()
        except Exception:
            pass
        try:
            self.session.close()
        except Exception:
            pass
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)

    db_path = ensure_db_path()
    if not db_path:
        QMessageBox.warning(None, "Afgebroken", "Geen databasebestand gekozen.")
        return

    print(f"[VakantieRooster v{__version__}] databasebestand: {db_path}")

    w = MainWindow(db_path)
    w.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
