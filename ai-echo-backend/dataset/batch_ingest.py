# dataset/batch_ingest.py
"""
知数知圈 · 批量素材解析器

支持格式：
  - CSV   列: content, material_type(可选), domain(可选), tags(可选)
  - JSONL 每行: {"content":"...", "material_type":"text", ...}
  - ZIP   内含若干 .txt / .md 文件，每个文件一条素材
  - TXT   纯文本，按空行分段，每段一条

返回统一的 ParseResult，由 dataset_api.py 批量写入 storage。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawMaterial:
    """解析后等待写入的素材行"""
    content:       str
    material_type: str = "text"    # text / image / audio / video
    domain:        str = ""
    tags:          List[str] = field(default_factory=list)
    source_name:   str = ""        # 来源文件名（ZIP 内文件名、CSV 行号等）


@dataclass
class ParseResult:
    materials: List[RawMaterial]
    total:     int
    skipped:   int              # 被跳过的行（太短、格式错误）
    errors:    List[str]        # 解析警告，不阻断流程


# ════════════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════════════

_MIN_CONTENT_LEN = 10   # 少于此字数的内容直接跳过


def _clean(text: str) -> str:
    return text.strip().replace("\r\n", "\n").replace("\r", "\n")


def _make_material(
    content: str,
    material_type: str = "text",
    domain: str = "",
    tags: str = "",
    source_name: str = "",
) -> Optional[RawMaterial]:
    content = _clean(content)
    if len(content) < _MIN_CONTENT_LEN:
        return None
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    return RawMaterial(
        content=content,
        material_type=material_type or "text",
        domain=domain or "",
        tags=tag_list,
        source_name=source_name,
    )


# ════════════════════════════════════════════════════════════════════
# 各格式解析器
# ════════════════════════════════════════════════════════════════════

def parse_csv(raw_bytes: bytes, encoding: str = "utf-8") -> ParseResult:
    """
    CSV 格式要求：
      必须列：content
      可选列：material_type, domain, tags
    首行为标题行。
    """
    materials: List[RawMaterial] = []
    errors: List[str] = []
    skipped = 0

    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except Exception as e:
        return ParseResult([], 0, 0, [f"CSV 解码失败: {e}"])

    reader = csv.DictReader(io.StringIO(text))
    if "content" not in (reader.fieldnames or []):
        return ParseResult([], 0, 0, ["CSV 缺少必须列 'content'，请检查表头"])

    for i, row in enumerate(reader, start=2):
        content = row.get("content", "")
        mat = _make_material(
            content=content,
            material_type=row.get("material_type", "text"),
            domain=row.get("domain", ""),
            tags=row.get("tags", ""),
            source_name=f"row_{i}",
        )
        if mat is None:
            skipped += 1
            if content:
                errors.append(f"第 {i} 行内容过短（已跳过）")
        else:
            materials.append(mat)

    return ParseResult(
        materials=materials,
        total=len(materials) + skipped,
        skipped=skipped,
        errors=errors,
    )


def parse_jsonl(raw_bytes: bytes, encoding: str = "utf-8") -> ParseResult:
    """
    每行一个 JSON 对象，必须有 content 字段。
    其他可选字段：material_type, domain, tags（字符串或列表）
    """
    materials: List[RawMaterial] = []
    errors: List[str] = []
    skipped = 0

    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except Exception as e:
        return ParseResult([], 0, 0, [f"JSONL 解码失败: {e}"])

    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {i} 行 JSON 解析失败: {e}（已跳过）")
            skipped += 1
            continue

        content = obj.get("content", "")
        tags_raw = obj.get("tags", "")
        if isinstance(tags_raw, list):
            tags_str = ",".join(str(t) for t in tags_raw)
        else:
            tags_str = str(tags_raw)

        mat = _make_material(
            content=content,
            material_type=obj.get("material_type", "text"),
            domain=obj.get("domain", ""),
            tags=tags_str,
            source_name=f"line_{i}",
        )
        if mat is None:
            skipped += 1
        else:
            materials.append(mat)

    return ParseResult(
        materials=materials,
        total=len(materials) + skipped,
        skipped=skipped,
        errors=errors,
    )


def parse_zip(raw_bytes: bytes) -> ParseResult:
    """
    ZIP 内每个 .txt / .md 文件视为一条素材。
    文件名格式可带域名前缀：medical_问诊记录.txt → domain=medical
    """
    materials: List[RawMaterial] = []
    errors: List[str] = []
    skipped = 0

    _DOMAIN_PREFIXES = ["medical", "legal", "code", "education", "finance", "general"]

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except Exception as e:
        return ParseResult([], 0, 0, [f"ZIP 解压失败: {e}"])

    for name in zf.namelist():
        # 跳过目录和隐藏文件
        if name.endswith("/") or name.startswith("__") or name.startswith("."):
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ("txt", "md", "text"):
            continue

        try:
            content = zf.read(name).decode("utf-8", errors="replace")
        except Exception as e:
            errors.append(f"{name}: 读取失败 ({e})")
            skipped += 1
            continue

        # 尝试从文件名解析 domain
        base = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        domain = ""
        for pfx in _DOMAIN_PREFIXES:
            if base.lower().startswith(pfx):
                domain = pfx
                break

        mat = _make_material(
            content=content,
            material_type="text",
            domain=domain,
            source_name=name,
        )
        if mat is None:
            skipped += 1
            errors.append(f"{name}: 内容过短（已跳过）")
        else:
            materials.append(mat)

    zf.close()
    return ParseResult(
        materials=materials,
        total=len(materials) + skipped,
        skipped=skipped,
        errors=errors,
    )


def parse_txt(raw_bytes: bytes, encoding: str = "utf-8") -> ParseResult:
    """
    纯文本：按连续空行（\\n\\n）分段，每段一条素材。
    """
    materials: List[RawMaterial] = []
    errors: List[str] = []
    skipped = 0

    try:
        text = raw_bytes.decode(encoding, errors="replace")
    except Exception as e:
        return ParseResult([], 0, 0, [f"TXT 解码失败: {e}"])

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for i, para in enumerate(paragraphs, start=1):
        mat = _make_material(content=para, source_name=f"para_{i}")
        if mat is None:
            skipped += 1
        else:
            materials.append(mat)

    return ParseResult(
        materials=materials,
        total=len(materials) + skipped,
        skipped=skipped,
        errors=errors,
    )


# ════════════════════════════════════════════════════════════════════
# 统一入口
# ════════════════════════════════════════════════════════════════════

def parse_upload(filename: str, raw_bytes: bytes) -> ParseResult:
    """
    根据文件名后缀自动选择解析器。
    filename: 原始上传文件名（用于判断格式）
    raw_bytes: 文件的二进制内容
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "csv":
        return parse_csv(raw_bytes)
    elif ext in ("jsonl", "ndjson"):
        return parse_jsonl(raw_bytes)
    elif ext == "zip":
        return parse_zip(raw_bytes)
    elif ext in ("txt", "md", "text"):
        return parse_txt(raw_bytes)
    else:
        # 尝试 JSONL，再 CSV，再 TXT
        result = parse_jsonl(raw_bytes)
        if result.total > 0:
            return result
        result = parse_csv(raw_bytes)
        if result.total > 0:
            return result
        return parse_txt(raw_bytes)
