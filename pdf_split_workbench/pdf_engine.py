from pathlib import Path

import fitz

from .naming_rules import safe_name, unique_directory, unique_path


def make_default_output_dir(source_pdf: Path) -> Path:
    return unique_directory(source_pdf.parent / f"{safe_name(source_pdf.stem)}_拆分结果")


def is_blank_page(page, render_scale: float = 0.4, dark_threshold: int = 180, dark_ratio: float = 0.005) -> bool:
    if page.get_text().strip():
        return False

    pix = page.get_pixmap(colorspace=fitz.csGRAY, matrix=fitz.Matrix(render_scale, render_scale))
    dark_pixels = sum(1 for pixel in pix.samples if pixel < dark_threshold)
    return (dark_pixels / len(pix.samples)) < dark_ratio


def remove_blank_pages(source_pdf: Path) -> tuple[Path, Path, int]:
    source_pdf = Path(source_pdf).resolve()
    output_dir = make_default_output_dir(source_pdf)
    output_dir.mkdir(parents=True, exist_ok=True)

    processed_pdf = unique_path(output_dir / f"{safe_name(source_pdf.stem)}_处理后完整PDF.pdf")
    source_doc = fitz.open(str(source_pdf))
    new_doc = fitz.open()
    removed_count = 0

    try:
        for page_index in range(len(source_doc)):
            page = source_doc.load_page(page_index)
            if is_blank_page(page):
                removed_count += 1
                continue
            new_doc.insert_pdf(source_doc, from_page=page_index, to_page=page_index)

        new_doc.save(str(processed_pdf))
    finally:
        new_doc.close()
        source_doc.close()

    return processed_pdf, output_dir, removed_count


def render_page_ppm(doc, page_index: int, scale_factor: float) -> bytes:
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale_factor, scale_factor))
    return pix.tobytes("ppm")


def save_ordered_pdf(source_pdf: Path, page_order: list[int], output_pdf: Path) -> Path:
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    source_doc = fitz.open(str(source_pdf))
    output_doc = fitz.open()
    try:
        for page_index in page_order:
            output_doc.insert_pdf(source_doc, from_page=page_index, to_page=page_index)
        output_doc.save(str(output_pdf))
    finally:
        output_doc.close()
        source_doc.close()
    return output_pdf


def build_page_order(total_pages: int) -> list[int]:
    return list(range(total_pages))


def delete_pages(page_order: list[int], current_page_numbers: list[int]) -> list[int]:
    to_delete = {page_number - 1 for page_number in current_page_numbers}
    return [page_index for position, page_index in enumerate(page_order) if position not in to_delete]


def move_page(page_order: list[int], from_position: int, to_position: int) -> list[int]:
    new_order = page_order.copy()
    page = new_order.pop(from_position)
    new_order.insert(to_position, page)
    return new_order
