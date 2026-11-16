"""超弦空间官方推荐阵容选择与队长脚本自动匹配。"""

from __future__ import annotations

import re
import time
import numpy as np
from maa.custom_action import CustomAction

from ..utils.CharacterProfiles import CHARACTER_PROFILES
from ..utils.ElysianPolicy import compare_key, normalize
from ..utils.ZhConv import to_simplified

# 推荐阵容弹窗（1280×720 逻辑坐标）：前三列为女武神，第四列为协同者/人偶。
VALKYRIE_COLS = ((220, 305), (315, 400), (405, 490))
RECOMMEND_ROWS = (
    (1, (200, 315), 253),
    (2, (330, 445), 383),
    (3, (460, 575), 513),
)
APPLY_BTN_X = 1010
RECOMMEND_ENTRY_POINT = (790, 230)
COMPLETENESS_TOGGLE_ROI = [1010, 125, 140, 60]
COMPLETENESS_TOGGLE_POINT = (1075, 155)
CAPTAIN_SKILL_ROI = [30, 430, 620, 190]
DIALOG_CHECK_ROI = [200, 20, 960, 240]
DIALOG_MARKERS = ("推荐阵容", "官方推荐", "完整度优先", "阵容完整度")
TARGET_DYNAMIC_NODE = "超限空间战斗-推荐队伍动态"


def slot_has_unowned_banner(frame_bgr: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> bool:
    """检查单个女武神头像底部是否存在连续横向黄色「未拥有」条带（忽略四边泛光）。"""
    strip = frame_bgr[y0 + 60:y1, x0:x1]
    if strip.size == 0:
        return False
    b = strip[:, :, 0].astype(np.int16)
    g = strip[:, :, 1].astype(np.int16)
    r = strip[:, :, 2].astype(np.int16)
    yellow = (r > 200) & (g > 170) & (b < 110)
    max_row_width = int(yellow.sum(axis=1).max()) if yellow.size else 0
    return max_row_width >= 48


def _ocr_unowned_cols(ocr_items, y0: int, y1: int) -> set[int]:
    """从 OCR 文本中补充定位落在前 3 位女武神列内的「未拥有」标记。"""
    cols = set()
    for item in ocr_items or ():
        if "未拥有" not in to_simplified(item.get("text", "")):
            continue
        x, y, w, h = item.get("box", (0, 0, 0, 0))
        cx = x + w / 2.0
        cy = y + h / 2.0
        if not (y0 <= cy <= y1):
            continue
        for idx, (c0, c1) in enumerate(VALKYRIE_COLS):
            if c0 - 10 <= cx <= c1 + 10:
                cols.add(idx)
    return cols


def row_owned_valkyrie_count(frame_bgr: np.ndarray, ocr_items, y0: int, y1: int) -> int:
    """统计该推荐行前 3 位女武神（忽略第 4 位人偶/协同者）中已拥有的数量。"""
    unowned = _ocr_unowned_cols(ocr_items, y0, y1)
    for idx, (x0, x1) in enumerate(VALKYRIE_COLS):
        if idx not in unowned and slot_has_unowned_banner(frame_bgr, x0, x1, y0, y1):
            unowned.add(idx)
    return 3 - len(unowned)


def pick_best_recommended_row(frame_bgr: np.ndarray, ocr_items=None):
    """由上到下选出第一个 3 位女武神全拥有的行；若都不满 3 位则退回拥有数最多且最靠上的行。"""
    candidates = []
    for row_idx, (y0, y1), apply_y in RECOMMEND_ROWS:
        owned = row_owned_valkyrie_count(frame_bgr, ocr_items, y0, y1)
        candidates.append((owned, row_idx, apply_y))
        if owned == 3:
            return owned, apply_y, row_idx
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (item[0], -item[1]))
    return best


def _find_abyss_variant(role: str, profiles: dict) -> tuple[str, str] | None:
    """在指定角色的所有配置里优先找出深渊专用（mode == 'abyss'）变体。"""
    variants = profiles.get(role, {}).get("variants", {})
    for variant, cfg in variants.items():
        if cfg.get("mode") == "abyss":
            return role, variant
    return None


def resolve_captain_profile(captain_text: str, profiles: dict | None = None) -> tuple[str, str, str]:
    """根据编队界面的队长技描述匹配对应的深渊 CharacterCombat 脚本。"""
    catalog = profiles if profiles is not None else CHARACTER_PROFILES
    norm = normalize(captain_text or "")
    norm_key = compare_key(norm)

    # 1) 优先提取「XXX作为队长时」里的队长装甲名，避免被其他说明文字干扰。
    m = re.search(r"([^\s，,。:：]+?)作为队长时", norm)
    captain_name = m.group(1) if m else ""
    if captain_name:
        direct = _find_abyss_variant(captain_name, catalog)
        if direct:
            return *direct, f"精确匹配队长 {captain_name}"
        cap_key = compare_key(captain_name)
        for role in catalog:
            role_key = compare_key(role)
            if role_key == cap_key or (cap_key and cap_key in role_key) or (role_key and role_key in cap_key):
                hit = _find_abyss_variant(role, catalog)
                if hit:
                    return *hit, f"模糊匹配队长 {captain_name} -> {role}"

    # 2) 若队长句式未抽到名称，扫描整段文本中是否出现已有深渊配置的角色名。
    for role in catalog:
        role_key = compare_key(role)
        if role_key and role_key in norm_key:
            hit = _find_abyss_variant(role, catalog)
            if hit:
                return *hit, f"文本匹配角色 {role}"

    # 3) 兜底：星之环/多人编队走通用三人轮切，否则走单C站场。
    if any(marker in norm for marker in ("星之环", "命运之轮", "世界之星", "升华之理", "无存之仪", "天衍之杯", "3/3")):
        fallback = _find_abyss_variant("通用队伍", catalog)
        if fallback:
            return *fallback, f"未见专属配置（{captain_name or '未知队长'}），使用通用轮切"
    fallback = _find_abyss_variant("通用角色", catalog) or ("通用队伍", "abyss_rotate")
    return *fallback, f"未见专属配置（{captain_name or '未知队长'}），使用通用兜底"


class AbyssTeamSelect(CustomAction):
    """打开「女武神推荐」，由上到下选出三位女武神全有的阵容并动态切换对应战斗脚本。"""

    @staticmethod
    def _ocr(context, image, roi):
        result = context.run_recognition(
            "深渊-通用OCR",
            image,
            pipeline_override={"深渊-通用OCR": {"recognition": "OCR", "roi": roi, "expected": [".+"]}},
        )
        if not result or not result.hit:
            return []
        return [{"text": r.text, "box": list(r.box)} for r in (result.all_results or []) if getattr(r, "text", "")]

    @staticmethod
    def _click(context, x: int, y: int, wait_s: float = 0.8):
        context.run_action(
            "角色战斗-触摸",
            pipeline_override={"角色战斗-触摸": {"action": {"type": "Click", "param": {"target": [int(x), int(y), 1, 1]}}}},
        )
        time.sleep(wait_s)

    def run(self, context, argv):
        try:
            return self._run(context)
        except Exception as exc:
            print(f"AbyssTeamSelect error: {type(exc).__name__}: {exc}", flush=True)
            return False

    def _dialog_open(self, context, image) -> bool:
        dialog_items = self._ocr(context, image, DIALOG_CHECK_ROI)
        text = normalize(" ".join(i["text"] for i in dialog_items))
        return any(marker in text for marker in DIALOG_MARKERS)

    def _run(self, context):
        controller = context.tasker.controller
        image = controller.post_screencap().wait().get()

        # 若尚未打开「推荐阵容」弹窗，点右侧推荐头像区进入；兼容动画渐入延迟。
        if not self._dialog_open(context, image):
            self._click(context, *RECOMMEND_ENTRY_POINT, wait_s=1.2)
            for _ in range(2):
                image = controller.post_screencap().wait().get()
                if self._dialog_open(context, image):
                    break
                time.sleep(0.6)
            else:
                print("AbyssTeamSelect: 未能打开推荐阵容弹窗，沿用当前编队", flush=True)
                return True

        # 确保「完整度优先」处于 OFF，保证按官方推荐顺序由上到下扫描。
        toggle_items = self._ocr(context, image, COMPLETENESS_TOGGLE_ROI)
        if any("ON" in i["text"].upper() for i in toggle_items):
            self._click(context, *COMPLETENESS_TOGGLE_POINT, wait_s=0.8)
            image = controller.post_screencap().wait().get()

        chosen = None
        for page in range(2):
            ocr_items = self._ocr(context, image, [180, 190, 360, 410])
            best = pick_best_recommended_row(image, ocr_items)
            if best and best[0] == 3:
                chosen = best
                break
            if best and (chosen is None or best[0] > chosen[0]):
                chosen = best
            if page == 0:
                controller.post_swipe(640, 520, 640, 260, 400).wait()
                time.sleep(0.8)
                image = controller.post_screencap().wait().get()

        if chosen:
            owned_count, apply_y, row_idx = chosen
            print(f"AbyssTeamSelect: 选择第 {row_idx} 队（拥有女武神 {owned_count}/3），点击一键编队", flush=True)
            self._click(context, APPLY_BTN_X, apply_y, wait_s=1.5)
        else:
            # 兜底关闭弹窗
            self._click(context, 1110, 80, wait_s=1.0)

        # 回到编队界面后读取「队长技能」区并切换对应战斗脚本。
        image = controller.post_screencap().wait().get()
        captain_items = self._ocr(context, image, CAPTAIN_SKILL_ROI)
        captain_text = " ".join(i["text"] for i in captain_items)
        role, variant, reason = resolve_captain_profile(captain_text)
        print(f"AbyssTeamSelect: 队长识别 -> {role}/{variant}（{reason}）", flush=True)

        context.override_pipeline({
            TARGET_DYNAMIC_NODE: {
                "action": {
                    "type": "Custom",
                    "param": {
                        "custom_action": "CharacterCombat",
                        "custom_action_param": {
                            "character": role,
                            "variant": variant,
                            "confirmed_signet": "",
                            "cycles_per_run": 15,
                        },
                    },
                }
            }
        })
        return True
