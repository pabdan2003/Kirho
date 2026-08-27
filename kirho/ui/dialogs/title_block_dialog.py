"""Editor pequeño para los datos del cajetín de la hoja."""

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt

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

        self._logo_data = str(values.get('logo_data', '') or '')
        self._logo_image = self._decode_logo(self._logo_data)
        self._logo_mode = QComboBox()
        self._logo_mode.addItem(self.tr('Color'), 'color')
        self._logo_mode.addItem(self.tr('Vectorized / monochrome'), 'monochrome')
        self._logo_mode.setCurrentIndex(
            1 if values.get('logo_mode') == 'monochrome' else 0)

        logo_widget = QWidget(self)
        logo_layout = QHBoxLayout(logo_widget)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        self._logo_preview = QLabel(self.tr('No logo'))
        self._logo_preview.setFixedSize(96, 64)
        self._update_logo_preview()
        logo_layout.addWidget(self._logo_preview)

        load_button = QPushButton(self.tr('Load…'))
        load_button.clicked.connect(self._load_logo)
        logo_layout.addWidget(load_button)
        clear_button = QPushButton(self.tr('Clear'))
        clear_button.clicked.connect(self._clear_logo)
        logo_layout.addWidget(clear_button)
        logo_layout.addWidget(self._logo_mode)
        layout.addRow(self.tr('Logo:'), logo_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _decode_logo(data: str) -> QImage:
        if not data:
            return QImage()
        try:
            return QImage.fromData(
                QByteArray.fromBase64(QByteArray(data.encode('ascii'))))
        except (UnicodeEncodeError, ValueError):
            return QImage()

    @staticmethod
    def _encode_logo(image: QImage) -> str:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        return bytes(buffer.data().toBase64()).decode('ascii')

    def _update_logo_preview(self):
        if self._logo_image.isNull():
            self._logo_preview.setText(self.tr('No logo'))
            self._logo_preview.setPixmap(QPixmap())
            return
        pixmap = QPixmap.fromImage(self._logo_image)
        self._logo_preview.setPixmap(
            pixmap.scaled(
                self._logo_preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _load_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr('Load Logo'),
            '',
            self.tr('Images (*.png *.jpg *.jpeg)'),
        )
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            return
        self._logo_image = image
        self._logo_data = self._encode_logo(image)
        self._update_logo_preview()

    def _clear_logo(self):
        self._logo_image = QImage()
        self._logo_data = ''
        self._update_logo_preview()

    def values(self) -> dict:
        return {
            **{key: edit.text().strip() for key, edit in self._edits.items()},
            'logo_data': self._logo_data,
            'logo_mode': self._logo_mode.currentData(),
        }
