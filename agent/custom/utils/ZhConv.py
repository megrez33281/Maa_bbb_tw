"""繁體中文轉簡體中文正規化工具，供 OCR 判斷統一比對使用。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_EXTRA_T2S = {
    "砲": "炮",
    "鎚": "锤",
    "臺": "台",
    "裡": "里",
    "裏": "里",
    "爲": "为",
    "啟": "启",
    "衆": "众",
    "衛": "卫",
    "線": "线",
}
_PHRASE_FIXES = (
    ("英杰", "英桀"),
    ("介面", "界面"),
    ("状态清单", "状态列表"),
)


def _find_charmap() -> Path | None:
    for rel in (
        "resource/resource_tw/tools/繁中助手_字表.txt",
        "assets/resource/resource_tw/tools/繁中助手_字表.txt",
    ):
        candidate = _PROJECT_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _t2s_table() -> dict[int, str]:
    mapping: dict[str, str] = {}
    charmap = _find_charmap()
    if charmap is not None:
        for line in charmap.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "\t" not in line:
                continue
            trad, simp_candidates = line.split("\t", 1)
            if len(trad) == 1:
                first = simp_candidates.split(maxsplit=1)[0]
                if len(first) == 1 and first != trad:
                    mapping[trad] = first
    mapping.update(_EXTRA_T2S)
    return str.maketrans(mapping)


def to_simplified(text: str) -> str:
    """將 OCR 中的繁體字元轉成簡體，並修正遊戲專有名詞。"""
    if not text:
        return ""
    result = text.translate(_t2s_table())
    for src, dst in _PHRASE_FIXES:
        if src in result:
            result = result.replace(src, dst)
    return result
