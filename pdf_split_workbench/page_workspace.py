from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import fitz
from PySide6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPoint,
    QRect,
    QRunnable,
    QSize,
    QThreadPool,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QSizePolicy,
    QSpacerItem,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
)


PAGE_ROLE = Qt.UserRole + 1
SIZE_ROLE = Qt.UserRole + 2
PIXMAP_ROLE = Qt.UserRole + 3
MIME_TYPE = "application/x-pdf-workbench-page"


class ThumbnailCache:
    def __init__(self, max_bytes: int = 128 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.current_bytes = 0
        self.items: OrderedDict[tuple[int, int, int], QPixmap] = OrderedDict()

    def clear(self):
        self.items.clear()
        self.current_bytes = 0

    def get(self, key: tuple[int, int, int]) -> QPixmap | None:
        pixmap = self.items.get(key)
        if pixmap is not None:
            self.items.move_to_end(key)
        return pixmap

    def best_for_page(self, source_page: int, target_width: int, target_height: int) -> QPixmap | None:
        exact = self.get((source_page, target_width, target_height))
        if exact is not None:
            return exact
        candidates = [
            (key, pixmap)
            for key, pixmap in self.items.items()
            if key[0] == source_page
        ]
        if not candidates:
            return None
        key, pixmap = min(
            candidates,
            key=lambda item: abs(item[0][1] - target_width) + abs(item[0][2] - target_height),
        )
        self.items.move_to_end(key)
        return pixmap

    def put(self, key: tuple[int, int, int], pixmap: QPixmap):
        old = self.items.pop(key, None)
        if old is not None:
            self.current_bytes -= old.width() * old.height() * 4
        self.items[key] = pixmap
        self.current_bytes += pixmap.width() * pixmap.height() * 4
        while self.current_bytes > self.max_bytes and len(self.items) > 1:
            _, removed = self.items.popitem(last=False)
            self.current_bytes -= removed.width() * removed.height() * 4


class PageListModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.page_order: list[int] = []
        self.page_sizes: dict[int, tuple[float, float]] = {}
        self.target_pixels = (320, 452)
        self.cache = ThumbnailCache()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.page_order)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.page_order):
            return None
        source_page = self.page_order[index.row()]
        if role == Qt.DisplayRole:
            return f"第 {index.row() + 1} 页"
        if role == PAGE_ROLE:
            return source_page
        if role == SIZE_ROLE:
            return self.page_sizes.get(source_page, (595.0, 842.0))
        if role == PIXMAP_ROLE:
            width, height = self.target_pixels
            return self.cache.best_for_page(source_page, width, height)
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled
        if index.isValid():
            base |= Qt.ItemIsDragEnabled
        return base

    def mimeTypes(self):
        return [MIME_TYPE]

    def mimeData(self, indexes):
        mime = QMimeData()
        if indexes:
            mime.setData(MIME_TYPE, str(indexes[0].row()).encode("ascii"))
        return mime

    def set_document(self, page_order: list[int], page_sizes: dict[int, tuple[float, float]]):
        self.beginResetModel()
        self.page_order = list(page_order)
        self.page_sizes = dict(page_sizes)
        self.cache.clear()
        self.endResetModel()

    def set_page_order(self, page_order: list[int]):
        self.beginResetModel()
        self.page_order = list(page_order)
        self.endResetModel()

    def set_target_pixels(self, width: int, height: int):
        self.target_pixels = (max(1, width), max(1, height))
        if self.page_order:
            self.dataChanged.emit(self.index(0), self.index(len(self.page_order) - 1), [PIXMAP_ROLE])

    def update_page(self, source_page: int):
        for row, page in enumerate(self.page_order):
            if page == source_page:
                index = self.index(row)
                self.dataChanged.emit(index, index, [PIXMAP_ROLE])
                return


class PageDelegate(QStyledItemDelegate):
    def __init__(self, workspace: "PageWorkspace"):
        super().__init__(workspace.view)
        self.workspace = workspace

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        cell = option.rect.adjusted(7, 7, -7, -7)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.setPen(QPen(QColor("#7808d0"), 2))
            painter.setBrush(QColor("#e7f1ff"))
            painter.drawRoundedRect(cell, 4, 4)

        footer_height = 26
        page_area = QRect(cell.left() + 7, cell.top() + 7, cell.width() - 14, cell.height() - footer_height - 14)
        page_width, page_height = index.data(SIZE_ROLE) or (595.0, 842.0)
        scale = min(page_area.width() / page_width, page_area.height() / page_height)
        draw_width = max(1, int(page_width * scale))
        draw_height = max(1, int(page_height * scale))
        page_rect = QRect(
            page_area.center().x() - draw_width // 2,
            page_area.center().y() - draw_height // 2,
            draw_width,
            draw_height,
        )

        painter.setPen(QPen(QColor("#a8b0b8"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(page_rect)
        pixmap = index.data(PIXMAP_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            painter.drawPixmap(page_rect.adjusted(1, 1, -1, -1), pixmap)

        footer = QRect(cell.left(), cell.bottom() - footer_height + 1, cell.width(), footer_height)
        painter.setPen(QColor("#526174"))
        painter.drawText(footer, Qt.AlignCenter, index.data(Qt.DisplayRole))
        painter.restore()

    def sizeHint(self, option, index):
        return self.workspace.view.gridSize()


class PageListView(QListView):
    zoomRequested = Signal(int)
    deleteRequested = Signal()
    undoRequested = Signal()
    returnToPageInputRequested = Signal()
    moveRequested = Signal(int, int)

    def __init__(self):
        super().__init__()
        self.drop_row: int | None = None
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setAutoScroll(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSpacing(8)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            """
            QListView {
                border: none;
                background: #f1f1f1;
                outline: none;
            }
            QListView::item { background: transparent; }
            QScrollBar:vertical { width: 12px; }
            """
        )

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.zoomRequested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undoRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Right and event.modifiers() == Qt.NoModifier:
            self.returnToPageInputRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        indexes = self.selectedIndexes()
        if not indexes:
            return
        mime = QMimeData()
        mime.setData(MIME_TYPE, str(indexes[0].row()).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = indexes[0].data(PIXMAP_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            drag.setPixmap(pixmap.scaled(120, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not event.mimeData().hasFormat(MIME_TYPE):
            event.ignore()
            return
        self.drop_row = self._insertion_row(event.position().toPoint())
        self.viewport().update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drop_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        try:
            source_row = int(bytes(event.mimeData().data(MIME_TYPE)).decode("ascii"))
        except (TypeError, ValueError):
            event.ignore()
            return
        insertion_row = self._insertion_row(event.position().toPoint())
        self.drop_row = None
        self.viewport().update()
        self.moveRequested.emit(source_row, insertion_row)
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_row is None or self.model() is None or self.model().rowCount() == 0:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#7808d0"), 3))
        if self.drop_row >= self.model().rowCount():
            rect = self.visualRect(self.model().index(self.model().rowCount() - 1))
            x = rect.right() + 3
            painter.drawLine(x, rect.top() + 5, x, rect.bottom() - 5)
        else:
            rect = self.visualRect(self.model().index(self.drop_row))
            x = rect.left() - 3
            painter.drawLine(x, rect.top() + 5, x, rect.bottom() - 5)

    def _insertion_row(self, point: QPoint) -> int:
        index = self.indexAt(point)
        if not index.isValid():
            return self.model().rowCount() if self.model() else 0
        rect = self.visualRect(index)
        return index.row() + (1 if point.x() > rect.center().x() else 0)


class RenderSignals(QObject):
    pageReady = Signal(int, object, bytes)
    failed = Signal(int, str)
    finished = Signal(object)


class RenderBatchTask(QRunnable):
    def __init__(self, token: int, pdf_path: Path, jobs: list[tuple[int, int, int]]):
        super().__init__()
        self.token = token
        self.pdf_path = Path(pdf_path)
        self.jobs = jobs
        self.signals = RenderSignals()

    def run(self):
        try:
            with fitz.open(str(self.pdf_path)) as document:
                for source_page, target_width, target_height in self.jobs:
                    page = document.load_page(source_page)
                    rect = page.rect
                    scale = min(target_width / rect.width, target_height / rect.height)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    self.signals.pageReady.emit(
                        self.token,
                        (source_page, target_width, target_height),
                        pixmap.tobytes("png"),
                    )
        except Exception as exc:
            self.signals.failed.emit(self.token, str(exc))
        finally:
            self.signals.finished.emit(self)


class PageWorkspace(QFrame):
    pageOrderChanged = Signal(object, str)
    undoRequested = Signal()
    statusChanged = Signal(str)
    warningRaised = Signal(str)
    returnToPageInputRequested = Signal()

    MIN_ZOOM = 15
    MAX_ZOOM = 150
    ZOOM_STEP = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pdf_path: Path | None = None
        self.document_token = 0
        self.zoom_percent = 32
        self.page_box = QSize(190, 270)
        self.pending: set[tuple[int, int, int]] = set()
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(2)
        self.active_tasks: set[RenderBatchTask] = set()

        self.model = PageListModel()
        self.view = PageListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(PageDelegate(self))

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(45)
        self.render_timer.timeout.connect(self.render_visible_pages)

        self.setStyleSheet(
            """
            PageWorkspace {
                background: rgba(246, 250, 253, 230);
                border: 1px solid rgba(188, 203, 218, 180);
                border-radius: 14px;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(self._build_header())
        layout.addWidget(self.view, 1)

        self.view.zoomRequested.connect(self.zoom_by)
        self.view.deleteRequested.connect(self.delete_selected)
        self.view.undoRequested.connect(self.undoRequested)
        self.view.returnToPageInputRequested.connect(self.returnToPageInputRequested)
        self.view.moveRequested.connect(self.move_page)
        self.view.verticalScrollBar().valueChanged.connect(self.schedule_visible_render)
        self.view.horizontalScrollBar().valueChanged.connect(self.schedule_visible_render)
        self.view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.update_geometry()

    def _build_header(self):
        header = QHBoxLayout()
        title = QLabel("PDF 页面工作区")
        title.setStyleSheet('font-family: "Microsoft YaHei"; font-size: 16px; font-weight: 700; color: #172033; border: none;')
        header.addWidget(title)
        header.addSpacing(10)

        self.zoom_out_btn = self._zoom_button("−", "缩小页面")
        self.zoom_in_btn = self._zoom_button("+", "放大页面")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_by(-1))
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_by(1))
        self.zoom_label = QLabel()
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setFixedWidth(46)
        self.zoom_label.setStyleSheet('font-family: "Microsoft YaHei"; color: #526174; font-size: 12px; border: none;')
        header.addWidget(self.zoom_out_btn)
        header.addWidget(self.zoom_label)
        header.addWidget(self.zoom_in_btn)
        header.addItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.page_count_label = QLabel("未导入 PDF")
        self.page_count_label.setStyleSheet('font-family: "Microsoft YaHei"; color: #64748b; font-size: 12px; border: none;')
        header.addWidget(self.page_count_label)
        return header

    def _zoom_button(self, text: str, tooltip: str):
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            """
            QToolButton {
                width: 28px; height: 28px; border: 1px solid #b9c7d6;
                border-radius: 5px; background: white; color: #263241;
                font-family: "Microsoft YaHei"; font-size: 18px; font-weight: 600;
            }
            QToolButton:hover { border-color: #7808d0; color: #7808d0; background: #f8f2ff; }
            QToolButton:pressed { background: #eee2f8; }
            QToolButton:disabled { color: #aab3bd; border-color: #d8e0e8; background: #f5f7f9; }
            """
        )
        return button

    @property
    def has_document(self):
        return self.pdf_path is not None and bool(self.model.page_order)

    @property
    def page_order(self):
        return list(self.model.page_order)

    def load_pdf(self, pdf_path: Path, page_order: list[int] | None = None):
        pdf_path = Path(pdf_path)
        with fitz.open(str(pdf_path)) as document:
            order = list(range(len(document))) if page_order is None else list(page_order)
            sizes = {
                index: (float(document.load_page(index).rect.width), float(document.load_page(index).rect.height))
                for index in range(len(document))
            }
        self.document_token += 1
        self.pdf_path = pdf_path
        self.pending.clear()
        self.model.set_document(order, sizes)
        self.view.clearSelection()
        self.view.scrollToTop()
        self.page_count_label.setText(f"共 {len(order)} 页")
        self.update_geometry()
        self.statusChanged.emit(f"已载入 {len(order)} 页，正在生成可见页预览")
        QTimer.singleShot(0, self.render_visible_pages)

    def set_page_order(self, page_order: list[int]):
        self.model.set_page_order(page_order)
        self.page_count_label.setText(f"共 {len(page_order)} 页")
        self.schedule_visible_render()

    def zoom_by(self, direction: int):
        old_zoom = self.zoom_percent
        self.zoom_percent = max(
            self.MIN_ZOOM,
            min(self.MAX_ZOOM, self.zoom_percent + self.ZOOM_STEP * (1 if direction > 0 else -1)),
        )
        if self.zoom_percent == old_zoom:
            return
        anchor = self._center_anchor()
        self.update_geometry()
        QTimer.singleShot(0, lambda: self._restore_anchor(anchor))
        self.schedule_visible_render()
        self.statusChanged.emit(f"页面缩放：{self.zoom_percent}%")

    def update_geometry(self):
        base_width = 595.0
        base_height = 842.0
        self.page_box = QSize(
            max(120, int(base_width * self.zoom_percent / 100)),
            max(170, int(base_height * self.zoom_percent / 100)),
        )
        self.view.setGridSize(QSize(self.page_box.width() + 34, self.page_box.height() + 54))
        dpr = max(1.0, self.devicePixelRatioF())
        quality = 1.35
        self.model.set_target_pixels(
            int(self.page_box.width() * dpr * quality),
            int(self.page_box.height() * dpr * quality),
        )
        self.zoom_label.setText(f"{self.zoom_percent}%")
        self.zoom_out_btn.setEnabled(self.zoom_percent > self.MIN_ZOOM)
        self.zoom_in_btn.setEnabled(self.zoom_percent < self.MAX_ZOOM)
        self.view.scheduleDelayedItemsLayout()
        self.view.viewport().update()

    def schedule_visible_render(self):
        self.render_timer.start()

    def render_visible_pages(self):
        if not self.pdf_path or not self.model.page_order:
            return
        viewport = self.view.viewport().rect()
        buffer_rect = viewport.adjusted(0, -viewport.height(), 0, viewport.height())
        width, height = self.model.target_pixels
        jobs = []
        visible_rows = []
        buffered_rows = []
        for row in range(self.model.rowCount()):
            index = self.model.index(row)
            rect = self.view.visualRect(index)
            if not rect.isValid() or rect.isEmpty():
                continue
            if rect.intersects(viewport):
                visible_rows.append(row)
            elif rect.intersects(buffer_rect):
                buffered_rows.append(row)
        for row in visible_rows + buffered_rows:
            source_page = self.model.page_order[row]
            key = (source_page, width, height)
            if self.model.cache.get(key) is None and key not in self.pending:
                self.pending.add(key)
                jobs.append(key)
        # The first timer can fire before QListView finishes its initial layout.
        # Queue a small first-screen batch so import never waits for a scroll.
        if not visible_rows and not buffered_rows:
            for row, source_page in enumerate(self.model.page_order[:8]):
                key = (source_page, width, height)
                if self.model.cache.get(key) is None and key not in self.pending:
                    self.pending.add(key)
                    jobs.append(key)
        if not jobs:
            return
        task = RenderBatchTask(self.document_token, self.pdf_path, jobs)
        task.signals.pageReady.connect(self._page_rendered)
        task.signals.failed.connect(self._render_failed)
        task.signals.finished.connect(self._task_finished)
        self.active_tasks.add(task)
        self.thread_pool.start(task)

    def _task_finished(self, task: RenderBatchTask):
        self.active_tasks.discard(task)

    def _page_rendered(self, token: int, key, png_data: bytes):
        self.pending.discard(tuple(key))
        if token != self.document_token:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(png_data):
            return
        key = tuple(key)
        self.model.cache.put(key, pixmap)
        self.model.update_page(key[0])

    def _render_failed(self, token: int, message: str):
        if token == self.document_token:
            self.warningRaised.emit(f"页面预览生成失败：{message}")

    def delete_selected(self):
        rows = sorted({index.row() for index in self.view.selectedIndexes()})
        if not rows:
            return
        if len(rows) >= len(self.model.page_order):
            self.warningRaised.emit("至少需要保留一页。")
            return
        new_order = [page for row, page in enumerate(self.model.page_order) if row not in set(rows)]
        self.model.set_page_order(new_order)
        self.view.clearSelection()
        self.page_count_label.setText(f"共 {len(new_order)} 页")
        self.pageOrderChanged.emit(new_order, f"已删除 {len(rows)} 页，可按 Ctrl+Z 撤销")
        self.schedule_visible_render()

    def move_page(self, source_row: int, insertion_row: int):
        if not 0 <= source_row < len(self.model.page_order):
            return
        new_order = list(self.model.page_order)
        page = new_order.pop(source_row)
        if insertion_row > source_row:
            insertion_row -= 1
        insertion_row = max(0, min(len(new_order), insertion_row))
        if insertion_row == source_row:
            return
        new_order.insert(insertion_row, page)
        self.model.set_page_order(new_order)
        index = self.model.index(insertion_row)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QListView.EnsureVisible)
        self.pageOrderChanged.emit(new_order, f"已移动页面：{source_row + 1} → {insertion_row + 1}")
        self.schedule_visible_render()

    def _selection_changed(self):
        indexes = self.view.selectedIndexes()
        if indexes:
            pages = sorted(index.row() + 1 for index in indexes)
            if len(pages) == 1:
                self.statusChanged.emit(f"已选中第 {pages[0]} 页")
            else:
                self.statusChanged.emit(f"已选中 {len(pages)} 页")

    def _center_anchor(self):
        center = self.view.viewport().rect().center()
        index = self.view.indexAt(center)
        if not index.isValid():
            indexes = self.view.selectedIndexes()
            index = indexes[0] if indexes else self.model.index(0)
        return index.data(PAGE_ROLE) if index.isValid() else None

    def _restore_anchor(self, source_page: int | None):
        if source_page is None:
            return
        try:
            row = self.model.page_order.index(source_page)
        except ValueError:
            return
        self.view.scrollTo(self.model.index(row), QListView.PositionAtCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_visible_render()

    def shutdown(self):
        self.document_token += 1
        self.pending.clear()
        self.render_timer.stop()
        self.thread_pool.clear()
        self.thread_pool.waitForDone(3000)
        self.active_tasks.clear()
