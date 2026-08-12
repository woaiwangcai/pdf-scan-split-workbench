from pathlib import Path
import os
import sys

try:
    from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, Signal
    from PySide6.QtGui import QColor, QDrag, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QButtonGroup,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QRadioButton,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpacerItem,
        QStackedWidget,
        QSpinBox,
        QStyle,
        QToolButton,
        QVBoxLayout,
        QWidget,
        QListView,
        QStyledItemDelegate,
    )
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        raise SystemExit(
            "缺少 PySide6。请先在项目目录运行：\n\n"
            "PY -m pip install -r requirements.txt\n\n"
            "或只安装界面依赖：\n\n"
            "PY -m pip install PySide6"
        ) from exc
    raise

from app_state import AppState
from exporter import MaterialInput, export_materials
from naming_rules import (
    load_config,
    load_workspace_rows,
    save_config,
    save_workspace_rows,
    split_numbered_material,
)
from page_workspace import PageWorkspace
from pdf_engine import remove_blank_pages, save_ordered_pdf


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOCAL_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PDFScanSplitWorkbench"
USER_CONFIG_PATH = LOCAL_DATA_DIR / "config.json"
WORKSPACE_STATE_PATH = LOCAL_DATA_DIR / "workspace.json"


class PatternBackground(QWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#dff3fb"))
        pen = QPen(QColor(57, 144, 179, 34), 1)
        painter.setPen(pen)
        step = 20
        for x in range(-self.height(), self.width(), step):
            painter.drawLine(x, 0, x + self.height(), self.height())


class PillButton(QPushButton):
    def __init__(self, text: str, color: str = "#7808d0"):
        super().__init__(f"↗  {text}")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)
        self.setStyleSheet(
            f"""
            QPushButton {{
                border: none;
                border-radius: 21px;
                background-color: {color};
                color: white;
                font-family: "Microsoft YaHei";
                font-size: 14px;
                font-weight: 600;
                padding: 10px 22px;
                text-align: center;
            }}
            QPushButton:hover {{
                background-color: #000000;
            }}
            QPushButton:disabled {{
                background-color: #9aa3ad;
                color: #edf2f7;
            }}
            """
        )


class StyledLineEdit(QLineEdit):
    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(36)
        self.setStyleSheet(
            """
            QLineEdit {
                color: #132033;
                font-family: "Microsoft YaHei";
                font-size: 13px;
                background: transparent;
                border: none;
                border-bottom: 1px solid rgba(80, 95, 110, 70);
                padding: 7px 8px;
            }
            QLineEdit:hover {
                background: rgba(73, 133, 224, 20);
            }
            QLineEdit:focus {
                border-bottom: 2px solid #5891ff;
                background: rgba(73, 133, 224, 25);
            }
            QLineEdit[emptyMaterial="true"] {
                color: #8d98a5;
                background: rgba(120, 132, 145, 18);
            }
            """
        )

    def set_empty_material(self, empty: bool):
        self.setProperty("emptyMaterial", empty)
        self.style().unpolish(self)
        self.style().polish(self)


class MaterialNameLineEdit(StyledLineEdit):
    moveToPageRequested = Signal(object)

    def __init__(self):
        super().__init__("材料名")

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key_Right
            and event.modifiers() == Qt.NoModifier
            and not self.hasSelectedText()
            and self.cursorPosition() == len(self.text())
        ):
            self.moveToPageRequested.emit(self)
            event.accept()
            return
        super().keyPressEvent(event)


class PageRangeLineEdit(StyledLineEdit):
    lastUsed = Signal(object)
    moveRequested = Signal(object, int)
    moveToMaterialRequested = Signal(object)

    def __init__(self):
        super().__init__("页码")
        self.saved_cursor_position = 0

    def focusOutEvent(self, event):
        self.saved_cursor_position = self.cursorPosition()
        super().focusOutEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.lastUsed.emit(self)

    def restore_saved_cursor(self):
        self.setFocus(Qt.ShortcutFocusReason)
        self.setCursorPosition(min(self.saved_cursor_position, len(self.text())))

    def keyPressEvent(self, event):
        if (
            event.key() == Qt.Key_Left
            and event.modifiers() == Qt.NoModifier
            and not self.hasSelectedText()
            and self.cursorPosition() == 0
        ):
            self.moveToMaterialRequested.emit(self)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            self.moveRequested.emit(self, 1)
            event.accept()
            return
        if event.key() == Qt.Key_Up and event.modifiers() == Qt.NoModifier:
            self.moveRequested.emit(self, -1)
            event.accept()
            return
        super().keyPressEvent(event)


class NamingBlock(QLabel):
    activated = Signal()

    def __init__(self, text: str, block_type: str = "fixed", locked: bool = False):
        super().__init__()
        self.block_type = block_type
        self.locked = locked
        self.raw_text = text
        self.setCursor(Qt.PointingHandCursor if text == "新建" else Qt.ArrowCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(34)
        self.setMinimumWidth(72)
        self.refresh()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self.raw_text == "新建" and event.button() == Qt.LeftButton:
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def refresh(self):
        prefix = "🔒 " if self.locked else ""
        self.setText(f"{prefix}{self.raw_text}")
        if self.block_type == "variable":
            bg = "#f8f2ff"
            border = "#7808d0"
            color = "#461178"
        elif self.locked:
            bg = "#263241"
            border = "#263241"
            color = "#ffffff"
        else:
            bg = "#ffffff"
            border = "#bcc8d6"
            color = "#243142"
        self.setStyleSheet(
            f"""
            QLabel {{
                background: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 5px 8px;
                font-family: "Microsoft YaHei";
                font-size: 12px;
                font-weight: 500;
            }}
            """
        )


NAMING_BLOCK_MIME = "application/x-pdf-workbench-naming-block"


class CanvasNamingBlock(QFrame):
    def __init__(self, canvas: "NamingBlockCanvas", block: dict, index: int):
        super().__init__(canvas)
        self.canvas = canvas
        self.block = dict(block)
        self.index = index
        self.drag_start: QPoint | None = None
        self.dragging = False
        self.editor: StyledLineEdit | None = None
        self.confirm_button: QToolButton | None = None
        self.cancel_button: QToolButton | None = None
        self.setCursor(Qt.OpenHandCursor if self.block["type"] != "new" else Qt.PointingHandCursor)
        self.setFixedHeight(48)
        self.setMinimumWidth(self._preferred_width())
        self.setMaximumWidth(150)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.delete_button = QToolButton(self)
        self.delete_button.setText("×")
        self.delete_button.setToolTip("删除此片段")
        self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.setStyleSheet(
            "QToolButton { border: none; border-radius: 8px; background: #d94a4a; color: white; font-size: 12px; }"
            "QToolButton:hover { background: #b93333; }"
        )
        self.delete_button.clicked.connect(lambda: self.canvas.blockDeleteRequested.emit(self.index))
        self._update_delete_button()

    def _preferred_width(self) -> int:
        text = self.text()
        return max(78, min(132, self.fontMetrics().horizontalAdvance(text) + 32))

    def text(self) -> str:
        return self.block.get("label", "变量") if self.block["type"] == "variable" else self.block.get("value", "")

    def is_new(self) -> bool:
        return self.block["type"] == "new"

    def set_delete_mode(self, enabled: bool):
        self._update_delete_button(enabled)
        self.update()

    def _update_delete_button(self, enabled: bool | None = None):
        visible = self.canvas.delete_mode if enabled is None else enabled
        self.delete_button.setVisible(visible and not self.is_new())

    def resizeEvent(self, event):
        self.delete_button.setGeometry(self.width() - 12, -4, 16, 16)
        if self.editor:
            self.editor.setGeometry(7, 5, self.width() - 66, self.height() - 10)
            if self.confirm_button and self.cancel_button:
                self.confirm_button.setGeometry(self.width() - 55, 10, 22, 26)
                self.cancel_button.setGeometry(self.width() - 30, 10, 22, 26)
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 3, -1, -3)
        locked = bool(self.block.get("locked"))
        if self.is_new():
            background, border, color = QColor("#ffffff"), QColor("#93a4b4"), QColor("#607080")
        elif locked:
            background, border, color = QColor("#334250"), QColor("#334250"), QColor("#ffffff")
        elif self.block["type"] == "variable":
            background, border, color = QColor("#faf5ff"), QColor("#9d56d8"), QColor("#5d188d")
        else:
            background, border, color = QColor("#ffffff"), QColor("#9eacbb"), QColor("#2b3a4a")
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 5, 5)
        if self.editor is None:
            painter.setPen(color)
            painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignCenter, self.text())
            if locked:
                painter.drawText(QRect(rect.right() - 22, rect.top() + 2, 17, 15), Qt.AlignCenter, "🔒")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start = event.position().toPoint()
            self.dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_start
            and not self.is_new()
            and not self.canvas.edit_mode
            and not self.canvas.delete_mode
            and (event.position().toPoint() - self.drag_start).manhattanLength() >= QApplication.startDragDistance()
        ):
            self.dragging = True
            self.canvas.start_block_drag(self)
            self.drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self.dragging:
            self.canvas.block_clicked(self)
        self.drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not self.is_new() and not self.canvas.edit_mode and not self.canvas.delete_mode:
            self.canvas.blockLockRequested.emit(self.index)
        super().mouseDoubleClickEvent(event)

    def begin_edit(self):
        if self.is_new() or self.editor:
            return
        self.editor = StyledLineEdit("片段内容")
        self.editor.setParent(self)
        self.editor.setText(self.text())
        self.setMinimumWidth(max(170, self.width()))
        self.editor.setGeometry(7, 5, self.width() - 66, self.height() - 10)
        self.confirm_button = QToolButton(self)
        self.confirm_button.setText("✓")
        self.confirm_button.setToolTip("确认")
        self.confirm_button.setStyleSheet(
            "QToolButton { border: 1px solid #98b2c9; border-radius: 4px; background: #f4f8fc; color: #28567d; }"
            "QToolButton:hover { border-color: #5891ff; background: #eaf2ff; }"
        )
        self.cancel_button = QToolButton(self)
        self.cancel_button.setText("×")
        self.cancel_button.setToolTip("取消")
        self.cancel_button.setStyleSheet(
            "QToolButton { border: 1px solid #c9d2db; border-radius: 4px; background: white; color: #667585; }"
            "QToolButton:hover { border-color: #d94a4a; color: #b93333; background: #fff4f4; }"
        )
        self.confirm_button.setGeometry(self.width() - 55, 10, 22, 26)
        self.cancel_button.setGeometry(self.width() - 30, 10, 22, 26)
        self.confirm_button.clicked.connect(lambda: self.canvas.confirm_inline_edit(self, self.editor.text()))
        self.cancel_button.clicked.connect(lambda: self.canvas.cancel_inline_edit(finish_mode=True))
        self.editor.show()
        self.confirm_button.show()
        self.cancel_button.show()
        self.editor.setFocus()
        self.editor.selectAll()
        self.editor.returnPressed.connect(lambda: self.canvas.confirm_inline_edit(self, self.editor.text()))
        self.editor.installEventFilter(self.canvas)
        self.update()

    def end_edit(self):
        if self.editor:
            self.editor.removeEventFilter(self.canvas)
            self.editor.deleteLater()
            self.confirm_button.deleteLater()
            self.cancel_button.deleteLater()
            self.editor = None
            self.confirm_button = None
            self.cancel_button = None
            self.setMinimumWidth(self._preferred_width())
            self.update()


class NamingBlockCanvas(QFrame):
    blocksChanged = Signal(object)
    blockDeleteRequested = Signal(int)
    blockLockRequested = Signal(int)
    newBlockRequested = Signal()
    inlineEditFinished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit_mode = False
        self.delete_mode = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(126)
        self.drop_index: int | None = None
        self.editing_block: CanvasNamingBlock | None = None
        self.setStyleSheet(
            "NamingBlockCanvas { border: 1px solid #d8e0e8; border-radius: 6px; background: #fbfcfd; }"
        )
        self.blocks_layout = QHBoxLayout(self)
        self.blocks_layout.setContentsMargins(20, 18, 20, 18)
        self.blocks_layout.setSpacing(12)
        self.blocks_layout.addStretch(1)

    def set_blocks(self, blocks: list[dict]):
        self.cancel_inline_edit()
        while self.blocks_layout.count():
            item = self.blocks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, block in enumerate(blocks):
            self.blocks_layout.addWidget(CanvasNamingBlock(self, block, index))
        self.blocks_layout.addWidget(CanvasNamingBlock(self, {"type": "new", "value": "新建"}, len(blocks)))
        self.blocks_layout.addStretch(1)

    def blocks(self) -> list[dict]:
        return [dict(block.block) for block in self.block_widgets() if not block.is_new()]

    def block_widgets(self) -> list[CanvasNamingBlock]:
        return [
            self.blocks_layout.itemAt(index).widget()
            for index in range(self.blocks_layout.count())
            if isinstance(self.blocks_layout.itemAt(index).widget(), CanvasNamingBlock)
        ]

    def set_delete_mode(self, enabled: bool):
        self.delete_mode = enabled
        for block in self.block_widgets():
            block.set_delete_mode(enabled)

    def set_edit_mode(self, enabled: bool):
        self.edit_mode = enabled
        if not enabled:
            self.cancel_inline_edit()

    def block_clicked(self, block: CanvasNamingBlock):
        if block.is_new():
            self.newBlockRequested.emit()
        elif self.edit_mode:
            self.start_inline_edit(block)

    def start_inline_edit(self, block: CanvasNamingBlock):
        if self.editing_block and self.editing_block is not block:
            self.cancel_inline_edit()
        self.editing_block = block
        block.begin_edit()

    def confirm_inline_edit(self, block: CanvasNamingBlock, text: str):
        if block.block["type"] == "variable":
            block.block["label"] = text.strip() or "变量"
        else:
            block.block["value"] = text.strip()
        block.end_edit()
        self.editing_block = None
        self.blocksChanged.emit(self.blocks())
        self.inlineEditFinished.emit()

    def cancel_inline_edit(self, finish_mode: bool = False):
        if self.editing_block:
            self.editing_block.end_edit()
            self.editing_block = None
        if finish_mode:
            self.inlineEditFinished.emit()

    def start_block_drag(self, block: CanvasNamingBlock):
        mime = QMimeData()
        mime.setData(NAMING_BLOCK_MIME, str(block.index).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(block.grab())
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(NAMING_BLOCK_MIME) and not self.edit_mode and not self.delete_mode:
            self.drop_index = self._insertion_index(event.position().toPoint())
            self.update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(NAMING_BLOCK_MIME) and not self.edit_mode and not self.delete_mode:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(NAMING_BLOCK_MIME):
            event.ignore()
            return
        try:
            source_row = int(bytes(event.mimeData().data(NAMING_BLOCK_MIME)).decode("ascii"))
        except (TypeError, ValueError):
            event.ignore()
            return
        values = self.blocks()
        if not 0 <= source_row < len(values):
            event.ignore()
            return
        insertion_row = self._insertion_index(event.position().toPoint())
        if insertion_row > source_row:
            insertion_row -= 1
        insertion_row = max(0, min(len(values) - 1, insertion_row))
        if insertion_row != source_row:
            moving = values.pop(source_row)
            values.insert(insertion_row, moving)
        self.drop_index = None
        self.blocksChanged.emit(values)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_index = None
        self.update()
        super().dragLeaveEvent(event)

    def _insertion_index(self, point: QPoint) -> int:
        actual_blocks = [block for block in self.block_widgets() if not block.is_new()]
        for index, block in enumerate(actual_blocks):
            if point.x() < block.geometry().center().x():
                return index
        return len(actual_blocks)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_index is None:
            return
        actual_blocks = [block for block in self.block_widgets() if not block.is_new()]
        if self.drop_index < len(actual_blocks):
            x = actual_blocks[self.drop_index].geometry().left() - 6
        elif actual_blocks:
            x = actual_blocks[-1].geometry().right() + 6
        else:
            x = 20
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#7808d0"), 3))
        painter.drawLine(x, 19, x, 70)

    def eventFilter(self, watched, event):
        if self.editing_block and watched is self.editing_block.editor:
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.cancel_inline_edit()
                return True
        return super().eventFilter(watched, event)


class BlockEditDialog(QDialog):
    def __init__(self, title: str, value: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(390)
        self.setStyleSheet('QDialog { background: #ffffff; font-family: "Microsoft YaHei"; }')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        label = QLabel("片段内容")
        label.setStyleSheet("font-size: 13px; font-weight: 600; color: #263241;")
        self.value_edit = StyledLineEdit("请输入片段内容")
        self.value_edit.setText(value)
        self.value_edit.selectAll()
        layout.addWidget(label)
        layout.addWidget(self.value_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确认")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.setStyleSheet(
            "QPushButton { min-width: 74px; min-height: 30px; border: 1px solid #b9c7d6; "
            "border-radius: 5px; background: white; color: #263241; }"
            "QPushButton:hover { border-color: #7808d0; color: #7808d0; }"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.value_edit.returnPressed.connect(self.accept)

    def value(self) -> str:
        return self.value_edit.text()


class OptionsDialog(QDialog):
    NAVIGATION = ["命名结构", "材料清单", "输出位置", "右侧填写区", "快捷键与校验说明"]

    def __init__(self, config: dict, parent=None, initial_page: int = 0):
        super().__init__(parent)
        self.setWindowTitle("选项")
        self.resize(880, 600)
        self.config = config
        self.blocks = [dict(block) for block in config.get("naming_blocks", [])]
        self.material_names = [split_numbered_material(item)[1] for item in config.get("materials", [])]
        self._loading_editor = False
        self.edit_mode = False
        self.delete_mode = False
        self.setStyleSheet(
            '''
            QDialog { background: #ffffff; font-family: "Microsoft YaHei"; }
            QLabel { color: #263241; }
            QListWidget { outline: none; }
            '''
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_titlebar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_navigation())
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("QStackedWidget { background: #ffffff; }")
        self.pages.addWidget(self._build_naming_page())
        self.pages.addWidget(self._build_materials_page())
        self.pages.addWidget(self._build_output_page())
        self.pages.addWidget(self._build_display_page())
        self.pages.addWidget(self._build_shortcuts_page())
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        QApplication.instance().installEventFilter(self)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.setStyleSheet(
            '''
            QDialogButtonBox { border-top: 1px solid #e5e9ee; padding: 12px 20px; }
            QPushButton { min-width: 82px; min-height: 32px; border: 1px solid #b9c7d6;
                          border-radius: 5px; background: white; color: #263241; }
            QPushButton:hover { border-color: #7808d0; color: #7808d0; }
            '''
        )
        buttons.accepted.connect(self._save_options)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.navigation.setCurrentRow(max(0, min(initial_page, len(self.NAVIGATION) - 1)))
        self._refresh_blocks()
        self._refresh_materials()

    def _build_titlebar(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet("QFrame { border-bottom: 1px solid #e5e9ee; background: #ffffff; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(22, 17, 22, 14)
        title = QLabel("选项")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #172033;")
        subtitle = QLabel("管理命名结构、材料清单和导出偏好")
        subtitle.setStyleSheet("font-size: 12px; color: #718096; margin-top: 3px;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return frame

    def _save_options(self):
        try:
            self.to_config_patch()
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.accept()

    def _build_navigation(self) -> QListWidget:
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(136)
        self.navigation.setSpacing(2)
        self.navigation.setStyleSheet(
            '''
            QListWidget { border: none; border-right: 1px solid #e5e9ee; background: #f7f8fa;
                          padding: 12px 8px; font-size: 13px; color: #526174; }
            QListWidget::item { height: 28px; padding: 5px 9px; border-radius: 4px; }
            QListWidget::item:selected { background: #ebe4f5; color: #54148d; font-weight: 700; }
            QListWidget::item:hover:!selected { background: #eef1f4; color: #263241; }
            '''
        )
        for name in self.NAVIGATION:
            self.navigation.addItem(QListWidgetItem(name))
        return self.navigation

    def _page_frame(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 17px; font-weight: 700; color: #172033;")
        detail = QLabel(description)
        detail.setWordWrap(True)
        detail.setStyleSheet("font-size: 12px; color: #718096; margin-bottom: 8px;")
        layout.addWidget(heading)
        layout.addWidget(detail)
        return page, layout

    def _tool_button(self, text: str, tooltip: str = "") -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip or text)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            '''
            QToolButton { min-height: 29px; padding: 0 9px; border: 1px solid #c7d1dc;
                          border-radius: 4px; background: white; color: #314154; font-size: 12px; }
            QToolButton:hover { border-color: #7808d0; color: #7808d0; background: #fbf8ff; }
            QToolButton:checked { border-color: #7808d0; color: #54148d; background: #f8f2ff; }
            QToolButton:disabled { color: #aab3bd; border-color: #e0e5ea; background: #f7f8fa; }
            '''
        )
        return button

    def _build_naming_page(self) -> QWidget:
        page, layout = self._page_frame("命名结构", "拖动方块调整顺序。双击方块可锁定或解锁。保存时最多保留一个变量片段。")

        preview_frame = QFrame()
        preview_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(0, 0, 0, 6)
        preview_layout.setSpacing(5)
        preview_title = QLabel("结构预览：")
        preview_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #526174;")
        preview_layout.addWidget(preview_title)
        self.naming_preview = QLabel()
        self.naming_preview.setWordWrap(True)
        self.naming_preview.setStyleSheet("font-size: 14px; color: #263241; padding: 3px 2px;")
        preview_layout.addWidget(self.naming_preview)
        layout.addWidget(preview_frame)

        block_actions = QHBoxLayout()
        self.edit_blocks_btn = self._tool_button("编辑")
        self.delete_blocks_btn = self._tool_button("删除")
        self.edit_blocks_btn.setCheckable(True)
        self.delete_blocks_btn.setCheckable(True)
        self.edit_blocks_btn.clicked.connect(lambda checked: self._set_canvas_mode("edit" if checked else "normal"))
        self.delete_blocks_btn.clicked.connect(lambda checked: self._set_canvas_mode("delete" if checked else "normal"))
        for button in (self.edit_blocks_btn, self.delete_blocks_btn):
            block_actions.addWidget(button)
        block_actions.addStretch(1)
        layout.addLayout(block_actions)

        self.naming_canvas = NamingBlockCanvas(self)
        self.naming_canvas.blocksChanged.connect(self._canvas_blocks_changed)
        self.naming_canvas.blockDeleteRequested.connect(self._delete_canvas_block)
        self.naming_canvas.blockLockRequested.connect(self._toggle_canvas_block_lock)
        self.naming_canvas.newBlockRequested.connect(self._add_canvas_block)
        self.naming_canvas.inlineEditFinished.connect(lambda: self._set_canvas_mode("normal"))
        layout.addWidget(self.naming_canvas)

        hint = QLabel("默认可拖动排序，双击固定片段锁定。点击“编辑”或“删除”后，再选择对应方块。")
        hint.setStyleSheet("font-size: 12px; color: #718096;")
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_materials_page(self) -> QWidget:
        page, layout = self._page_frame("材料清单", "材料行会显示在主界面右侧。这里维护默认顺序和名称，导入 PDF 后仍可直接填写变量与页码。")
        self.materials_list = QListWidget()
        self.materials_list.setStyleSheet(
            '''
            QListWidget { border: 1px solid #d8e0e8; border-radius: 5px; background: white; font-size: 13px; }
            QListWidget::item { min-height: 25px; padding: 4px 9px; }
            QListWidget::item:selected { background: #ebe4f5; color: #461178; }
            '''
        )
        self.materials_list.currentRowChanged.connect(self._select_material)
        layout.addWidget(self.materials_list, 1)

        actions = QHBoxLayout()
        add = self._tool_button("新增")
        remove = self._tool_button("删除")
        up = self._tool_button("上移")
        down = self._tool_button("下移")
        add.clicked.connect(self._add_material)
        remove.clicked.connect(self._remove_material)
        up.clicked.connect(lambda: self._move_material(-1))
        down.clicked.connect(lambda: self._move_material(1))
        for button in (add, remove, up, down):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.material_name_edit = StyledLineEdit("材料名称")
        self.material_name_edit.textEdited.connect(self._update_selected_material)
        layout.addWidget(QLabel("材料名称"))
        layout.addWidget(self.material_name_edit)
        return page

    def _build_output_page(self) -> QWidget:
        page, layout = self._page_frame("输出位置", "默认在原 PDF 同级目录建立“原文件名_拆分结果”文件夹，原文件不会被覆盖。")
        self.output_default_radio = QRadioButton("使用原 PDF 同级目录")
        self.output_custom_radio = QRadioButton("使用自定义默认目录")
        use_source_location = self.config.get("output_location_mode", "source") == "source"
        self.output_default_radio.setChecked(use_source_location)
        self.output_custom_radio.setChecked(not use_source_location)
        group = QButtonGroup(self)
        group.addButton(self.output_default_radio)
        group.addButton(self.output_custom_radio)
        for radio in (self.output_default_radio, self.output_custom_radio):
            radio.setStyleSheet("QRadioButton { font-size: 13px; color: #263241; padding: 5px 0; }")
            layout.addWidget(radio)

        row = QHBoxLayout()
        self.output_dir_edit = StyledLineEdit("选择一个文件夹")
        self.output_dir_edit.setText(self.config.get("custom_output_dir", ""))
        browse = self._tool_button("浏览")
        browse.clicked.connect(self._browse_output_dir)
        row.addWidget(self.output_dir_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self.output_default_radio.toggled.connect(self._toggle_output_path)
        self._toggle_output_path(self.output_default_radio.isChecked())
        layout.addStretch(1)
        return page

    def _build_display_page(self) -> QWidget:
        page, layout = self._page_frame(
            "右侧填写区",
            "调整主页右侧填写区的整体宽度。右侧变窄时，左侧 PDF 页面工作区会自动变宽。",
        )
        current_size = self.config.get("side_panel_size", "standard")
        self.side_size_group = QButtonGroup(self)
        choices = [
            ("compact", "紧凑", "右侧更窄，给 PDF 页面留出更多空间。"),
            ("standard", "标准", "当前使用的宽度，适合大多数情况。"),
            ("wide", "宽敞", "材料名称较长时更容易填写和查看。"),
        ]
        for value, title, description in choices:
            radio = QRadioButton(title)
            radio.setProperty("panelSize", value)
            radio.setChecked(current_size == value)
            radio.setStyleSheet("QRadioButton { font-size: 13px; font-weight: 600; color: #263241; padding: 5px 0; }")
            self.side_size_group.addButton(radio)
            layout.addWidget(radio)
            note = QLabel(description)
            note.setStyleSheet("font-size: 12px; color: #718096; margin-left: 22px; margin-bottom: 8px;")
            layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_shortcuts_page(self) -> QWidget:
        page, layout = self._page_frame("快捷键与校验说明", "当前版本提供固定快捷键和导出前校验，暂不支持自定义，避免不同操作习惯导致误处理。")
        shortcuts = [("Enter", "跳到下一行页码输入框"), ("↑", "跳到上一行页码输入框"), ("→（左侧页面区）", "返回最近使用的页码输入框"), ("→（材料名称框末尾）", "跳到同一行页码输入框"), ("←（页码框开头）", "返回同一行材料名称框"), ("Delete", "删除选中页面"), ("Ctrl + Z", "撤销上一步删除或拖拽"), ("Ctrl + Enter", "导出拆分结果"), ("Ctrl + 鼠标滚轮", "缩放页面预览")]
        for key, description in shortcuts:
            row = QFrame()
            row.setStyleSheet("QFrame { border-bottom: 1px solid #edf0f3; }")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 7, 4, 7)
            key_label = QLabel(key)
            key_label.setFixedWidth(150)
            key_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #461178;")
            text = QLabel(description)
            text.setStyleSheet("font-size: 13px; color: #526174;")
            row_layout.addWidget(key_label)
            row_layout.addWidget(text)
            layout.addWidget(row)
        validation = QLabel("导出会阻止：页码超出范围、页码重叠或遗漏、重复使用页面、空文件名和 Windows 非法文件名。")
        validation.setWordWrap(True)
        validation.setStyleSheet("margin-top: 14px; padding: 10px; background: #fff7e8; color: #7a5312; border: 1px solid #f1d6a6; border-radius: 5px; font-size: 12px;")
        layout.addWidget(validation)
        layout.addStretch(1)
        return page

    def _refresh_blocks(self):
        if not hasattr(self, "naming_canvas"):
            return
        self.naming_canvas.set_blocks(self.blocks)
        self._refresh_naming_preview()

    def _refresh_naming_preview(self):
        pieces = []
        for block in self.blocks:
            if block.get("locked"):
                pieces.append(block.get("label", "") if block.get("type") == "variable" else block.get("value", ""))
            else:
                pieces.append("[材料名称]")
        self.naming_preview.setText("".join(pieces) + ".pdf")

    def _set_canvas_mode(self, mode: str):
        self.edit_mode = mode == "edit"
        self.delete_mode = mode == "delete"
        self.edit_blocks_btn.blockSignals(True)
        self.delete_blocks_btn.blockSignals(True)
        self.edit_blocks_btn.setChecked(self.edit_mode)
        self.delete_blocks_btn.setChecked(self.delete_mode)
        self.edit_blocks_btn.blockSignals(False)
        self.delete_blocks_btn.blockSignals(False)
        self.naming_canvas.set_delete_mode(self.delete_mode)
        self.naming_canvas.set_edit_mode(self.edit_mode)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and (self.edit_mode or self.delete_mode):
            global_pos = event.globalPosition().toPoint()
            canvas_pos = self.naming_canvas.mapFromGlobal(global_pos)
            is_mode_button = watched in (self.edit_blocks_btn, self.delete_blocks_btn)
            # The red-box canvas is the only area where a mode stays active.
            if not self.naming_canvas.rect().contains(canvas_pos) and not is_mode_button:
                self._set_canvas_mode("normal")
        return super().eventFilter(watched, event)

    def _canvas_blocks_changed(self, blocks: list[dict]):
        self.blocks = blocks
        self._refresh_blocks()

    def _add_canvas_block(self):
        self.blocks.append({"type": "fixed", "value": "固定片段", "locked": False})
        self._refresh_blocks()
        self._set_canvas_mode("edit")
        block_widgets = [block for block in self.naming_canvas.block_widgets() if not block.is_new()]
        if block_widgets:
            self.naming_canvas.start_inline_edit(block_widgets[-1])

    def _delete_canvas_block(self, row: int):
        if not 0 <= row < len(self.blocks):
            return
        block = self.blocks[row]
        text = block.get("label", "变量") if block.get("type") == "variable" else block.get("value", "固定片段")
        dialog = QMessageBox(QMessageBox.Question, "删除片段", f"确定删除“{text or '空白片段'}”吗？", parent=self)
        confirm = dialog.addButton("确认删除", QMessageBox.AcceptRole)
        dialog.addButton("取消", QMessageBox.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is confirm:
            self.blocks.pop(row)
            self._refresh_blocks()
        self._set_canvas_mode("normal")

    def _toggle_canvas_block_lock(self, row: int):
        if not 0 <= row < len(self.blocks):
            return
        self.blocks[row]["locked"] = not bool(self.blocks[row].get("locked"))
        self._refresh_blocks()

    def closeEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)

    def _refresh_materials(self, selected_row: int | None = None):
        if not hasattr(self, "materials_list"):
            return
        previous = self.materials_list.currentRow() if selected_row is None else selected_row
        self.materials_list.blockSignals(True)
        self.materials_list.clear()
        for index, name in enumerate(self.material_names, start=1):
            item = QListWidgetItem(f"{index}. {name}" if name else f"{index}.")
            if not name:
                item.setForeground(QColor("#9aa3ad"))
            self.materials_list.addItem(item)
        self.materials_list.blockSignals(False)
        if self.material_names:
            self.materials_list.setCurrentRow(max(0, min(previous, len(self.material_names) - 1)))
        else:
            self._select_material(-1)

    def _select_material(self, row: int):
        self._loading_editor = True
        if 0 <= row < len(self.material_names):
            self.material_name_edit.setEnabled(True)
            self.material_name_edit.setText(self.material_names[row])
            self._set_material_input_hint(not bool(self.material_names[row]))
        else:
            self.material_name_edit.setEnabled(False)
            self.material_name_edit.clear()
            self._set_material_input_hint(False)
        self._loading_editor = False

    def _update_selected_material(self, value: str):
        if self._loading_editor:
            return
        row = self.materials_list.currentRow()
        if 0 <= row < len(self.material_names):
            self.material_names[row] = value.strip()
            item = self.materials_list.item(row)
            item.setText(f"{row + 1}. {self.material_names[row]}" if self.material_names[row] else f"{row + 1}.")
            item.setForeground(QColor("#263241") if self.material_names[row] else QColor("#9aa3ad"))
            self._set_material_input_hint(not bool(self.material_names[row]))

    def _set_material_input_hint(self, is_empty: bool):
        self.material_name_edit.set_empty_material(is_empty)

    def _add_material(self):
        self.material_names.append("")
        self._refresh_materials(len(self.material_names) - 1)
        self.material_name_edit.setFocus()

    def _remove_material(self):
        row = self.materials_list.currentRow()
        if 0 <= row < len(self.material_names):
            self.material_names.pop(row)
            self._refresh_materials(row)

    def _move_material(self, direction: int):
        row = self.materials_list.currentRow()
        new_row = row + direction
        if not (0 <= row < len(self.material_names) and 0 <= new_row < len(self.material_names)):
            return
        self.material_names[row], self.material_names[new_row] = self.material_names[new_row], self.material_names[row]
        self._refresh_materials(new_row)

    def _browse_output_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "选择默认输出目录", self.output_dir_edit.text() or str(BASE_DIR))
        if chosen:
            self.output_dir_edit.setText(chosen)
            self.output_custom_radio.setChecked(True)

    def _toggle_output_path(self, use_source: bool):
        self.output_dir_edit.setEnabled(not use_source)

    def to_config_patch(self) -> dict:
        variable_count = sum(not block.get("locked") for block in self.blocks)
        if variable_count > 1:
            raise ValueError("命名结构最多只能保留一个未锁定方块，请锁定或删除多余方块后再保存。")
        materials = [f"{index}.{name}" for index, name in enumerate(self.material_names, start=1)]
        return {
            "variable_column_width": self.config.get("variable_column_width", 220),
            "side_panel_size": next(
                (button.property("panelSize") for button in self.side_size_group.buttons() if button.isChecked()),
                "standard",
            ),
            "naming_blocks": self.blocks or [{"type": "variable", "label": "材料名", "locked": False}],
            "materials": materials or ["1."],
            "output_location_mode": "source" if self.output_default_radio.isChecked() else "custom",
            "custom_output_dir": self.output_dir_edit.text().strip(),
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF 扫描件拆分工作台")
        self.resize(1440, 860)

        self.config = load_config(USER_CONFIG_PATH if USER_CONFIG_PATH.exists() else CONFIG_PATH)
        self.saved_workspace_rows = load_workspace_rows(WORKSPACE_STATE_PATH)
        self.restoring_workspace = False
        self.state = AppState()
        self.state.set_naming_blocks(self.config["naming_blocks"])
        self.page_entries: list[tuple[StyledLineEdit, StyledLineEdit]] = []
        self.last_page_input: PageRangeLineEdit | None = None
        self.side_panel: QWidget | None = None

        root = PatternBackground()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(22, 18, 22, 22)
        root_layout.setSpacing(16)

        root_layout.addWidget(self.build_toolbar())
        root_layout.addLayout(self.build_workspace(), stretch=1)
        self.restore_material_rows(self.saved_workspace_rows)

    def build_toolbar(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255, 255, 255, 210);
                border: 1px solid rgba(190, 204, 218, 160);
                border-radius: 16px;
            }
            """
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self.import_btn = PillButton("导入")
        self.blank_btn = PillButton("删除空白页")
        self.export_btn = PillButton("导出")
        self.options_btn = PillButton("选项")

        self.import_btn.clicked.connect(self.choose_pdf)
        self.blank_btn.clicked.connect(self.remove_blank_pages)
        self.export_btn.clicked.connect(self.export_pdf)
        self.options_btn.clicked.connect(self.show_options)

        for button in (self.import_btn, self.blank_btn, self.export_btn, self.options_btn):
            layout.addWidget(button)
        layout.addItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.status_label = QLabel("请选择一份 PDF 开始处理")
        self.status_label.setStyleSheet('font-family: "Microsoft YaHei"; color: #334155; font-size: 13px;')
        layout.addWidget(self.status_label)
        return frame

    def build_workspace(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(16)
        self.page_workspace = PageWorkspace()
        self.page_workspace.pageOrderChanged.connect(self.apply_page_order)
        self.page_workspace.undoRequested.connect(self.undo_page_operation)
        self.page_workspace.statusChanged.connect(self.status_label.setText)
        self.page_workspace.warningRaised.connect(
            lambda message: QMessageBox.warning(self, "提示", message)
        )
        self.page_workspace.returnToPageInputRequested.connect(self.restore_last_page_input)
        layout.addWidget(self.page_workspace, stretch=1)
        self.side_panel = self.build_side_panel()
        layout.addWidget(self.side_panel)
        self.workspace_layout = layout
        return layout

    def build_side_panel(self) -> QWidget:
        frame = QFrame()
        panel_widths = {"compact": 360, "standard": 430, "wide": 520}
        frame.setFixedWidth(panel_widths.get(self.config.get("side_panel_size", "standard"), 430))
        frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255, 255, 255, 232);
                border: 1px solid rgba(188, 203, 218, 180);
                border-radius: 14px;
            }
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        title = QLabel("拆分命名面板")
        title.setStyleSheet('font-family: "Microsoft YaHei"; font-size: 16px; font-weight: 700; color: #172033;')
        layout.addWidget(title)

        preview = QLabel(self.naming_preview_text())
        preview.setWordWrap(True)
        preview.setMinimumHeight(36)
        preview.setStyleSheet(
            "QLabel { background: #f8fafc; border: 1px solid #dce4ec; border-radius: 5px; "
            "padding: 7px 9px; color: #526174; font-family: 'Microsoft YaHei'; font-size: 12px; }"
        )
        layout.addWidget(preview)

        table_header = QHBoxLayout()
        variable_header = QLabel("变量")
        page_header = QLabel("页码")
        for label in (variable_header, page_header):
            label.setStyleSheet('font-family: "Microsoft YaHei"; color: #526174; font-weight: 700;')
        table_header.addWidget(variable_header, stretch=2)
        table_header.addWidget(page_header, stretch=1)
        layout.addLayout(table_header)

        self.rows_area = QScrollArea()
        self.rows_area.setWidgetResizable(True)
        self.rows_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_area.setWidget(rows_widget)
        layout.addWidget(self.rows_area, stretch=1)

        for material in self.config["materials"]:
            self.add_material_row(material)
        return frame

    def add_material_row(self, material: str):
        _, material_name = split_numbered_material(material)
        row = QHBoxLayout()
        variable_input = MaterialNameLineEdit()
        variable_input.setText(material_name)
        variable_input.set_empty_material(not bool(material_name))
        variable_input.textChanged.connect(
            lambda text, field=variable_input: field.set_empty_material(not bool(text.strip()))
        )
        variable_input.textChanged.connect(self.autosave_workspace)
        variable_input.moveToPageRequested.connect(self.move_to_page_input)
        page_input = PageRangeLineEdit()
        page_input.lastUsed.connect(self.remember_page_input)
        page_input.moveRequested.connect(self.move_page_input_focus)
        page_input.moveToMaterialRequested.connect(self.move_to_material_input)
        page_input.textChanged.connect(self.autosave_workspace)
        row.addWidget(variable_input, stretch=2)
        row.addWidget(page_input, stretch=1)
        self.rows_layout.addLayout(row)
        self.page_entries.append((variable_input, page_input))

    def remember_page_input(self, page_input: PageRangeLineEdit):
        self.last_page_input = page_input

    def move_to_page_input(self, material_input: MaterialNameLineEdit):
        for current_material, page_input in self.page_entries:
            if current_material is material_input:
                page_input.setFocus(Qt.ShortcutFocusReason)
                page_input.setCursorPosition(len(page_input.text()))
                return

    def move_to_material_input(self, page_input: PageRangeLineEdit):
        for material_input, current_page in self.page_entries:
            if current_page is page_input:
                material_input.setFocus(Qt.ShortcutFocusReason)
                material_input.setCursorPosition(len(material_input.text()))
                return

    def autosave_workspace(self):
        if self.restoring_workspace or not self.page_entries:
            return
        rows = self.capture_material_rows()
        self.saved_workspace_rows = rows
        self.state.set_material_rows(rows)
        try:
            save_workspace_rows(WORKSPACE_STATE_PATH, rows)
        except OSError:
            self.status_label.setText("填写内容已保留在当前窗口，但本地自动保存失败")

    def restore_last_page_input(self):
        if self.last_page_input and self.last_page_input in [entry[1] for entry in self.page_entries]:
            self.last_page_input.restore_saved_cursor()
            self.status_label.setText("已返回上次使用的页码输入框")

    def move_page_input_focus(self, current: PageRangeLineEdit, direction: int):
        page_inputs = [entry[1] for entry in self.page_entries]
        try:
            current_index = page_inputs.index(current)
        except ValueError:
            return
        target_index = current_index + direction
        if not 0 <= target_index < len(page_inputs):
            return
        target = page_inputs[target_index]
        target.setFocus(Qt.ShortcutFocusReason)
        target.selectAll()
        target.setCursorPosition(len(target.text()))

    def choose_pdf(self):
        start_dir = str(self.state.source_pdf.parent) if self.state.source_pdf else str(BASE_DIR)
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", start_dir, "PDF 文件 (*.pdf)")
        if not path:
            return
        source_pdf = Path(path)
        self.set_busy(True, f"正在导入：{source_pdf.name}")
        try:
            self.state.source_pdf = source_pdf
            self.load_preview(source_pdf)
            self.status_label.setText(f"已导入：{source_pdf.name}，请点击“删除空白页”")
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", f"无法导入这个 PDF：\n\n{exc}")
            self.status_label.setText("导入失败")
        finally:
            self.set_busy(False)

    def remove_blank_pages(self):
        if not self.state.source_pdf:
            QMessageBox.warning(self, "提示", "请先导入 PDF。")
            return
        self.set_busy(True, "正在删除空白页...")
        try:
            processed_pdf, output_dir, removed_count = remove_blank_pages(self.state.source_pdf)
            self.state.start_pdf(self.state.source_pdf, processed_pdf, output_dir, removed_count)
            self.load_preview(processed_pdf)
            self.status_label.setText(f"已移除空白页 {removed_count} 页；输出目录：{output_dir}")
        except Exception as exc:
            QMessageBox.critical(self, "处理失败", str(exc))
            self.status_label.setText("删除空白页失败")
        finally:
            self.set_busy(False)

    def load_preview(self, pdf_path: Path):
        self.page_workspace.load_pdf(pdf_path)
        self.state.set_page_order(self.page_workspace.page_order)

    def apply_page_order(self, page_order: list[int], status: str):
        self.state.remember_page_order()
        self.state.page_order = list(page_order)
        self.status_label.setText(status)

    def export_pdf(self):
        processed_pdf = self.state.processed_pdf or self.state.source_pdf
        if not processed_pdf:
            QMessageBox.warning(self, "提示", "请先导入 PDF。")
            return
        if self.config.get("output_location_mode") == "custom" and self.config.get("custom_output_dir"):
            output_dir = Path(self.config["custom_output_dir"]) / f"{processed_pdf.stem}_拆分结果"
        else:
            output_dir = self.state.output_dir or processed_pdf.parent / f"{processed_pdf.stem}_拆分结果"
        if self.state.page_order:
            ordered_pdf = output_dir / f"{processed_pdf.stem}_当前页面顺序.pdf"
            processed_pdf = save_ordered_pdf(processed_pdf, self.state.page_order, ordered_pdf)
        materials = []
        for variable_input, page_input in self.page_entries:
            material_name = variable_input.text().strip()
            if not material_name:
                continue
            materials.append(MaterialInput(material=material_name, page_range=page_input.text()))
        try:
            result = export_materials(
                processed_pdf,
                output_dir,
                "",
                materials,
                naming_blocks=self.config.get("naming_blocks", []),
            )
            self.status_label.setText(f"导出完成：{len(result.output_files)} 份文件")
            QMessageBox.information(self, "完成", f"已导出 {len(result.output_files)} 份文件。\n输出目录：{output_dir}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def naming_preview_text(self) -> str:
        parts = []
        for block in self.config.get("naming_blocks", []):
            if block.get("locked"):
                parts.append(block.get("label", "") if block.get("type") == "variable" else block.get("value", ""))
            else:
                parts.append("[材料名称]")
        return "命名预览：" + "".join(parts) + ".pdf"

    def show_options(self, initial_page: int = 0):
        preserved_rows = self.capture_material_rows()
        dialog = OptionsDialog(self.config, self, initial_page=initial_page)
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.update(dialog.to_config_patch())
        save_config(USER_CONFIG_PATH, self.config)
        self.state.set_naming_blocks(self.config["naming_blocks"])
        self.state.set_material_rows(preserved_rows)
        self.rebuild_side_panel(preserved_rows)
        self.status_label.setText("选项已保存")

    def capture_material_rows(self) -> list[dict]:
        configured_materials = [
            split_numbered_material(material)[1]
            for material in self.config.get("materials", [])
        ]
        rows = []
        for index, (variable_input, page_input) in enumerate(self.page_entries):
            configured_name = configured_materials[index] if index < len(configured_materials) else ""
            entered_name = variable_input.text()
            rows.append(
                {
                    "configured_name": configured_name,
                    "entered_name": entered_name,
                    "page_range": page_input.text(),
                    "name_was_edited": entered_name != configured_name,
                }
            )
        return rows

    def restore_material_rows(self, preserved_rows: list[dict]) -> None:
        if not preserved_rows:
            return
        self.restoring_workspace = True
        used_rows: set[int] = set()
        target_materials = [
            split_numbered_material(material)[1]
            for material in self.config.get("materials", [])
        ]

        for target_index, (variable_input, page_input) in enumerate(self.page_entries):
            target_name = target_materials[target_index] if target_index < len(target_materials) else ""
            source_index = next(
                (
                    index
                    for index, row in enumerate(preserved_rows)
                    if index not in used_rows and row.get("configured_name", "") == target_name
                ),
                None,
            )
            if source_index is None and target_index < len(preserved_rows) and target_index not in used_rows:
                source_index = target_index
            if source_index is None:
                continue

            used_rows.add(source_index)
            source = preserved_rows[source_index]
            if source.get("name_was_edited"):
                variable_input.setText(source.get("entered_name", ""))
            else:
                variable_input.setText(target_name)
            page_input.setText(source.get("page_range", ""))
        self.restoring_workspace = False

    def rebuild_side_panel(self, preserved_rows: list[dict] | None = None):
        if not self.side_panel:
            return
        if preserved_rows is None:
            preserved_rows = self.capture_material_rows()
        self.workspace_layout.removeWidget(self.side_panel)
        self.side_panel.deleteLater()
        self.page_entries = []
        self.side_panel = self.build_side_panel()
        self.workspace_layout.addWidget(self.side_panel)
        self.restore_material_rows(preserved_rows)
        self.autosave_workspace()

    def set_busy(self, busy: bool, text: str | None = None):
        for button in (self.import_btn, self.blank_btn, self.export_btn, self.options_btn):
            button.setDisabled(busy)
        if text:
            self.status_label.setText(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undo_page_operation()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.export_pdf()
        else:
            super().keyPressEvent(event)

    def undo_page_operation(self):
        if self.state.undo_page_order():
            self.page_workspace.set_page_order(self.state.page_order)
            self.status_label.setText("已撤销上一步页面操作")
        else:
            self.status_label.setText("没有可撤销的页面操作")

    def closeEvent(self, event):
        self.autosave_workspace()
        self.page_workspace.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
