from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppState:
    source_pdf: Path | None = None
    processed_pdf: Path | None = None
    output_dir: Path | None = None
    page_order: list[int] = field(default_factory=list)
    undo_history: list[list[int]] = field(default_factory=list)
    naming_blocks: list[dict] = field(default_factory=list)
    material_rows: list[dict] = field(default_factory=list)
    removed_blank_pages: int = 0
    processed_sources: set[str] = field(default_factory=set)
    scale_factor: float = 0.6

    def start_pdf(self, source_pdf: Path, processed_pdf: Path, output_dir: Path, removed_count: int) -> None:
        self.source_pdf = source_pdf
        self.processed_pdf = processed_pdf
        self.output_dir = output_dir
        self.removed_blank_pages = removed_count
        self.page_order = []
        self.undo_history = []

    def set_page_order(self, page_order: list[int]) -> None:
        self.page_order = page_order
        self.undo_history = []

    def remember_page_order(self) -> None:
        self.undo_history.append(self.page_order.copy())

    def undo_page_order(self) -> bool:
        if not self.undo_history:
            return False
        self.page_order = self.undo_history.pop()
        return True

    def set_naming_blocks(self, blocks: list[dict]) -> None:
        self.naming_blocks = blocks

    def set_material_rows(self, rows: list[dict]) -> None:
        self.material_rows = rows

    def mark_current_done(self) -> None:
        if self.source_pdf:
            self.processed_sources.add(str(self.source_pdf.resolve()))

    def reset_pdf(self) -> None:
        self.source_pdf = None
        self.processed_pdf = None
        self.output_dir = None
        self.page_order = []
        self.undo_history = []
        self.material_rows = []
        self.removed_blank_pages = 0
