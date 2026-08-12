from dataclasses import dataclass
from pathlib import Path

from .naming_rules import build_output_name, build_structured_output_name, unique_path


SKIP_VALUES = {"", "无", "跳过"}


@dataclass
class MaterialInput:
    material: str
    page_range: str


@dataclass
class ExportResult:
    output_files: list[Path]
    skipped_count: int


def parse_page_range(text: str, total_pages: int) -> list[int] | None:
    value = text.strip()
    if value in SKIP_VALUES:
        return None

    pages: list[int] = []
    seen: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise ValueError("页码格式不能为空片段")

        if "-" in part:
            bounds = [item.strip() for item in part.split("-")]
            if len(bounds) != 2 or not all(item.isdigit() for item in bounds):
                raise ValueError(f"页码格式不正确：{part}")
            start, end = map(int, bounds)
            if start > end:
                raise ValueError(f"页码范围起止反向：{part}")
            current_pages = list(range(start, end + 1))
        else:
            if not part.isdigit():
                raise ValueError(f"页码格式不正确：{part}")
            current_pages = [int(part)]

        for page in current_pages:
            if page < 1 or page > total_pages:
                raise ValueError(f"页码 {page} 超出当前 PDF 页数 {total_pages}")
            if page in seen:
                raise ValueError(f"同一行内重复填写第 {page} 页")
            seen.add(page)
            pages.append(page)

    return pages


def validate_materials(materials: list[MaterialInput], total_pages: int) -> dict[str, list[int] | None]:
    parsed: dict[str, list[int] | None] = {}
    used_pages: dict[int, str] = {}

    for row_index, item in enumerate(materials, start=1):
        try:
            pages = parse_page_range(item.page_range, total_pages)
        except ValueError as exc:
            raise ValueError(f"第 {row_index} 行：{item.material}，{exc}") from exc

        parsed[item.material] = pages
        if not pages:
            continue

        for page in pages:
            if page in used_pages:
                raise ValueError(f"第 {row_index} 行：{item.material}，与“{used_pages[page]}”重复使用第 {page} 页")
            used_pages[page] = item.material

    missing_pages = [page for page in range(1, total_pages + 1) if page not in used_pages]
    if missing_pages:
        preview = ", ".join(str(page) for page in missing_pages[:20])
        suffix = "..." if len(missing_pages) > 20 else ""
        raise ValueError(f"存在未分配页面：{preview}{suffix}")

    return parsed


def export_materials(
    processed_pdf: Path,
    output_dir: Path,
    prefix: str,
    materials: list[MaterialInput],
    naming_blocks: list[dict] | None = None,
) -> ExportResult:
    from PyPDF2 import PdfReader, PdfWriter

    processed_pdf = Path(processed_pdf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(processed_pdf, "rb") as input_file:
        reader = PdfReader(input_file)
        parsed = validate_materials(materials, len(reader.pages))
        planned_names: set[str] = set()
        output_files: list[Path] = []
        skipped_count = 0

        for item in materials:
            pages = parsed[item.material]
            if not pages:
                skipped_count += 1
                continue

            output_name = (
                build_structured_output_name(naming_blocks, item.material)
                if naming_blocks is not None
                else build_output_name(prefix, item.material)
            )
            if not output_name or output_name == ".pdf":
                raise ValueError(f"文件名为空：{item.material}")
            if output_name in planned_names:
                raise ValueError(f"输出文件名重复：{output_name}")
            planned_names.add(output_name)

            out_path = unique_path(output_dir / output_name)
            writer = PdfWriter()
            for page in pages:
                writer.add_page(reader.pages[page - 1])

            with open(out_path, "wb") as output_file:
                writer.write(output_file)
            output_files.append(out_path)

    return ExportResult(output_files=output_files, skipped_count=skipped_count)
