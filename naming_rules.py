import json
import re
from pathlib import Path


DEFAULT_MATERIALS = [
    "1.材料一",
    "2.材料二",
    "3.材料三",
    "4.材料四",
    "5.材料五",
    "6.材料六",
    "7.材料七",
    "8.材料八",
    "9.材料九",
    "10.材料十",
]

DEFAULT_CONFIG = {
    "default_prefix": "项目名称_姓名_房号_",
    "variable_column_width": 220,
    "side_panel_size": "standard",
    "naming_blocks": [
        {"type": "fixed", "value": "项目名称_", "locked": True},
        {"type": "variable", "label": "材料名", "locked": False},
        {"type": "fixed", "value": "_归档资料", "locked": True},
    ],
    "materials": DEFAULT_MATERIALS,
    "output_location_mode": "source",
    "custom_output_dir": "",
}


def load_config(path: Path) -> dict:
    if not path.exists():
        save_config(path, DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_CONFIG.copy()

    materials = data.get("materials") or DEFAULT_MATERIALS
    default_prefix = data.get("default_prefix") or DEFAULT_CONFIG["default_prefix"]
    variable_column_width = data.get("variable_column_width") or DEFAULT_CONFIG["variable_column_width"]
    side_panel_size = data.get("side_panel_size", DEFAULT_CONFIG["side_panel_size"])
    naming_blocks = data.get("naming_blocks") or DEFAULT_CONFIG["naming_blocks"]
    return {
        "default_prefix": default_prefix,
        "variable_column_width": variable_column_width,
        "side_panel_size": side_panel_size,
        "naming_blocks": naming_blocks,
        "materials": materials,
        "output_location_mode": data.get("output_location_mode", "source"),
        "custom_output_dir": data.get("custom_output_dir", ""),
    }


def save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def load_workspace_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = data.get("material_rows", [])
    return rows if isinstance(rows, list) else []


def save_workspace_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"material_rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def unique_directory(path: Path) -> Path:
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.name}_{idx}")
        if not candidate.exists():
            return candidate
        idx += 1


def split_numbered_material(name: str) -> tuple[str, str]:
    if "." not in name:
        return "", name
    idx, material_name = name.split(".", 1)
    return idx.strip(), material_name.strip()


def build_output_name(prefix: str, material: str) -> str:
    idx, material_name = split_numbered_material(material)
    if idx:
        return safe_name(f"{idx}.{prefix}{material_name}.pdf")
    return safe_name(f"{prefix}{material_name}.pdf")


def build_structured_output_name(naming_blocks: list[dict], variable_value: str) -> str:
    parts = []
    for block in naming_blocks:
        if block.get("locked"):
            fixed_text = block.get("label", "") if block.get("type") == "variable" else block.get("value", "")
            parts.append(str(fixed_text))
        else:
            parts.append(variable_value)
    filename = safe_name("".join(parts))
    if not filename:
        raise ValueError("命名结构生成的文件名为空")
    return f"{filename}.pdf"
