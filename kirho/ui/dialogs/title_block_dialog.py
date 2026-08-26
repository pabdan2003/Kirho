"""Editor pequeño para los datos del cajetín de la hoja."""

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit

from kirho.ui.scene import TITLE_BLOCK_FIELDS


class TitleBlockDialog(QDialog):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Title Block"))
        self.setMinimumWidth(420)

        layout = QFormLayout(self)
        self._edits = {}
        for key, label in TITLE_BLOCK_FIELDS:
            edit = QLineEdit(str(values.get(key, '') or ''))
            edit.setMaxLength(80)
            layout.addRow(self.tr(f"{label}:"), edit)
            self._edits[key] = edit

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> dict:
        return {key: edit.text().strip() for key, edit in self._edits.items()}
