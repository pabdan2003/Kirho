from __future__ import annotations

import os

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
    QApplication, QInputDialog,
)

from kirho.external_libraries import ExternalLibraryManager


class SettingsDialog(QDialog):
    """
    Ventana de configuración general de la app.

    Está organizada en secciones:
      • Apariencia → Tema (combo + carpeta de temas externos).
    """

    def __init__(self, theme_manager, colors, parent=None,
                 current_theme_id: str = 'dark',
                 on_theme_change=None,
                 current_language: str = 'en',
                 on_language_change=None,
                 library_manager: ExternalLibraryManager | None = None):
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.colors = colors
        self._current_theme_id = current_theme_id
        self._on_theme_change = on_theme_change
        self._current_language = current_language
        self._on_language_change = on_language_change
        self.library_manager = library_manager or ExternalLibraryManager()
        self.setWindowTitle(self.tr("Settings"))
        self.setMinimumSize(560, 520)
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(10)

        gb_theme = QGroupBox(self.tr("Appearance"))
        gl = QVBoxLayout(gb_theme)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(self.tr("Theme:")))
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(240)
        self.theme_combo.setToolTip(
            self.tr("Changes the app color scheme.\n"
                    "It applies immediately and is remembered between sessions."))
        self._populate_theme_combo()
        self.theme_combo.currentIndexChanged.connect(self._on_combo_changed)
        row1.addWidget(self.theme_combo)
        row1.addStretch()
        gl.addLayout(row1)

        self.theme_desc = QLabel("")
        self.theme_desc.setWordWrap(True)
        self.theme_desc.setFont(QFont('Menlo', 9))
        self.theme_desc.setStyleSheet(f"color: {self.colors['text_dim']};")
        gl.addWidget(self.theme_desc)

        row2 = QHBoxLayout()
        btn_open = QPushButton(self.tr("📁  Open themes folder"))
        btn_open.setToolTip(
            self.tr("Opens the folder where you can place .json files\n"
                    "to add your own themes."))
        btn_open.clicked.connect(self._open_themes_folder)
        row2.addWidget(btn_open)

        btn_reload = QPushButton(self.tr("🔄  Reload list"))
        btn_reload.setToolTip(
            self.tr("Rescans theme folders after adding or removing\n"
                    ".json files without restarting the app."))
        btn_reload.clicked.connect(self._reload_themes)
        row2.addWidget(btn_reload)

        btn_export = QPushButton(self.tr("💾  Export current theme…"))
        btn_export.setToolTip(
            self.tr("Saves the selected theme as a .json template\n"
                    "that you can modify to create your own."))
        btn_export.clicked.connect(self._export_current_theme)
        row2.addWidget(btn_export)

        row2.addStretch()
        gl.addLayout(row2)

        hint = QLabel(
            self.tr("To add a separately installable theme, place a .json file\n"
                    "using the format described in themes/README.md in that folder,\n"
                    "then click “Reload list” (or restart the app).")
        )
        hint.setWordWrap(True)
        hint.setFont(QFont('Menlo', 8))
        hint.setStyleSheet(f"color: {self.colors['text_dim']};")
        gl.addWidget(hint)

        main.addWidget(gb_theme)

        gb_libraries = QGroupBox(self.tr("External libraries"))
        libraries_layout = QVBoxLayout(gb_libraries)
        self.external_libraries_path = QLabel()
        self.external_libraries_path.setWordWrap(True)
        libraries_layout.addWidget(self.external_libraries_path)

        self.external_libraries_list = QLabel()
        self.external_libraries_list.setWordWrap(True)
        self.external_libraries_list.setFont(QFont('Menlo', 8))
        libraries_layout.addWidget(self.external_libraries_list)

        libraries_buttons = QHBoxLayout()
        btn_open_libraries = QPushButton(self.tr("📁  Open libraries folder"))
        btn_open_libraries.clicked.connect(self._open_libraries_folder)
        libraries_buttons.addWidget(btn_open_libraries)

        btn_install_library = QPushButton(self.tr("Install package…"))
        btn_install_library.clicked.connect(self._install_external_library)
        libraries_buttons.addWidget(btn_install_library)

        btn_reload_libraries = QPushButton(self.tr("Reload list"))
        btn_reload_libraries.clicked.connect(self._refresh_external_libraries)
        libraries_buttons.addWidget(btn_reload_libraries)
        libraries_buttons.addStretch()
        libraries_layout.addLayout(libraries_buttons)

        libraries_hint = QLabel(self.tr(
            "Optional simulator backends are installed separately and loaded "
            "only when needed. Install only packages you trust."))
        libraries_hint.setWordWrap(True)
        libraries_hint.setFont(QFont('Menlo', 8))
        libraries_hint.setStyleSheet(f"color: {self.colors['text_dim']};")
        libraries_layout.addWidget(libraries_hint)
        main.addWidget(gb_libraries)

        gb_language = QGroupBox(self.tr("Language"))
        language_layout = QHBoxLayout(gb_language)
        language_layout.addWidget(QLabel(self.tr("Interface language:")))
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Español", "es")
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(self._current_language)))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_layout.addWidget(self.language_combo)
        language_layout.addStretch()
        main.addWidget(gb_language)
        main.addStretch()

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = bbox.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText(self.tr("Close"))
        bbox.rejected.connect(self.accept)
        bbox.accepted.connect(self.accept)
        main.addWidget(bbox)

        self._refresh_theme_description()
        self._refresh_external_libraries()

    def _refresh_external_libraries(self):
        path = self.library_manager.ensure_user_dir()
        self.external_libraries_path.setText(
            self.tr("Folder: {path}").format(path=path))
        packages = self.library_manager.list_installed()
        backends = self.library_manager.list_backends()
        lines = [f"{p['name']} {p['version']}" for p in packages]
        if backends:
            lines.append(self.tr("Backends: ") + ", ".join(
                backend['name'] for backend in backends))
        self.external_libraries_list.setText(
            "\n".join(lines) if lines else self.tr("No external libraries installed."))

    def _open_libraries_folder(self):
        path = self.library_manager.ensure_user_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _install_external_library(self):
        spec, ok = QInputDialog.getText(
            self,
            self.tr("Install external library"),
            self.tr("Package name, local wheel, or URL:"))
        if not ok or not spec.strip():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = self.library_manager.install(spec)
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Installation error"), str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if result.returncode:
            detail = (result.stderr or result.stdout or
                      self.tr("pip returned an error.")).strip()
            QMessageBox.critical(self, self.tr("Installation error"), detail[-3000:])
            return
        self.library_manager.activate()
        self._refresh_external_libraries()
        QMessageBox.information(
            self, self.tr("Library installed"),
            self.tr("The external library was installed successfully."))

    def _on_language_changed(self, _index: int):
        language = self.language_combo.currentData()
        if not language or language == self._current_language:
            return
        self._current_language = language
        if self._on_language_change:
            self._on_language_change(language)

    def _populate_theme_combo(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for entry in self.theme_manager.list_themes():
            label = entry['name']
            if entry['source'] != 'builtin':
                label += self.tr('  (external)')
            self.theme_combo.addItem(label, entry['id'])
        idx = self.theme_combo.findData(self._current_theme_id)
        if idx < 0:
            idx = 0
        self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def _on_combo_changed(self, _index: int):
        tid = self.theme_combo.currentData()
        if not tid or tid == self._current_theme_id:
            self._refresh_theme_description()
            return
        self._current_theme_id = tid
        if self._on_theme_change:
            self._on_theme_change(tid)
        self._refresh_theme_description()

    def _refresh_theme_description(self):
        tid = self.theme_combo.currentData()
        meta = self.theme_manager.get_theme_meta(tid) if tid else None
        if meta is None:
            self.theme_desc.setText("")
            return
        src = (self.tr("Source: built-in") if meta['source'] == 'builtin'
               else self.tr("Source: {path}").format(path=meta['source']))
        desc = meta.get('description', '')
        self.theme_desc.setText(f"  {desc}\n  {src}" if desc else f"  {src}")

    def _open_themes_folder(self):
        path = self.theme_manager.ensure_user_themes_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _reload_themes(self):
        self.theme_manager.refresh()
        self._populate_theme_combo()
        self._refresh_theme_description()
        QMessageBox.information(
            self, self.tr("Themes reloaded"),
            self.tr("Discovered {count} themes in total.").format(
                count=len(self.theme_manager.list_themes())))

    def _export_current_theme(self):
        tid = self.theme_combo.currentData()
        if not tid:
            return
        copy_label = self.tr("copy")
        suggested = f'{tid}_{copy_label}.json'
        default_dir = self.theme_manager.ensure_user_themes_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export theme as template"),
            os.path.join(default_dir, suggested),
            self.tr("JSON Theme (*.json)"))
        if not path:
            return
        ok = self.theme_manager.export_theme_template(tid, path)
        if ok:
            QMessageBox.information(
                self, self.tr("Theme exported"),
                self.tr("Template saved to:\n{path}\n\n"
                        "Edit the colors and click “Reload list” to see it in the selector.").format(path=path))
        else:
            QMessageBox.warning(
                self, self.tr("Error"),
                self.tr("Could not save the file:\n{path}").format(path=path))
