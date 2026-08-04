from __future__ import annotations

import os

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)


class SettingsDialog(QDialog):
    """
    Ventana de configuración general de la app.

    Está organizada en secciones:
      • Apariencia → Tema (combo + carpeta de temas externos).
    """

    def __init__(self, theme_manager, colors, parent=None,
                 current_theme_id: str = 'dark',
                 on_theme_change=None):
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.colors = colors
        self._current_theme_id = current_theme_id
        self._on_theme_change = on_theme_change
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 380)
        self._build_ui()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(10)

        gb_theme = QGroupBox("Appearance")
        gl = QVBoxLayout(gb_theme)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.setMinimumWidth(240)
        self.theme_combo.setToolTip(
            "Changes the app color scheme.\n"
            "It applies immediately and is remembered between sessions.")
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
        btn_open = QPushButton("📁  Open themes folder")
        btn_open.setToolTip(
            "Opens the folder where you can place .json files\n"
            "to add your own themes.")
        btn_open.clicked.connect(self._open_themes_folder)
        row2.addWidget(btn_open)

        btn_reload = QPushButton("🔄  Reload list")
        btn_reload.setToolTip(
            "Rescans theme folders after adding or removing\n"
            ".json files without restarting the app.")
        btn_reload.clicked.connect(self._reload_themes)
        row2.addWidget(btn_reload)

        btn_export = QPushButton("💾  Export current theme…")
        btn_export.setToolTip(
            "Saves the selected theme as a .json template\n"
            "that you can modify to create your own.")
        btn_export.clicked.connect(self._export_current_theme)
        row2.addWidget(btn_export)

        row2.addStretch()
        gl.addLayout(row2)

        hint = QLabel(
            "To add a separately installable theme, place a .json file\n"
            "using the format described in themes/README.md in that folder,\n"
            "then click “Reload list” (or restart the app)."
        )
        hint.setWordWrap(True)
        hint.setFont(QFont('Menlo', 8))
        hint.setStyleSheet(f"color: {self.colors['text_dim']};")
        gl.addWidget(hint)

        main.addWidget(gb_theme)
        main.addStretch()

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = bbox.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText("Close")
        bbox.rejected.connect(self.accept)
        bbox.accepted.connect(self.accept)
        main.addWidget(bbox)

        self._refresh_theme_description()

    def _populate_theme_combo(self):
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for entry in self.theme_manager.list_themes():
            label = entry['name']
            if entry['source'] != 'builtin':
                label += '  (external)'
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
        src = ("Source: built-in" if meta['source'] == 'builtin'
               else f"Source: {meta['source']}")
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
            self, "Themes reloaded",
            f"Discovered {len(self.theme_manager.list_themes())} themes in total."
        )

    def _export_current_theme(self):
        tid = self.theme_combo.currentData()
        if not tid:
            return
        suggested = f"{tid}_copia.json"
        default_dir = self.theme_manager.ensure_user_themes_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export theme as template",
            os.path.join(default_dir, suggested),
            "JSON Theme (*.json)")
        if not path:
            return
        ok = self.theme_manager.export_theme_template(tid, path)
        if ok:
            QMessageBox.information(
                self, "Theme exported",
                f"Template saved to:\n{path}\n\n"
                "Edit the colors and click “Reload list” to see it in the selector.")
        else:
            QMessageBox.warning(
                self, "Error",
                f"Could not save the file:\n{path}")
