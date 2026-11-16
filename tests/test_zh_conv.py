"""繁中正規化、樂土策略與聖痕詞綴解析的離線單元測試。"""

import unittest

from agent.custom.utils.AffixRules import classify_line, evaluate_side
from agent.custom.utils.CharacterProfiles import CHARACTER_PROFILES
from agent.custom.utils.ElysianPolicy import (
    RunState,
    choice_rows,
    normalize,
    read_floor,
    score_signet,
    shop_rows,
    signet_disabled,
)
from agent.custom.utils.ZhConv import to_simplified


class ZhConvTests(unittest.TestCase):
    def test_to_simplified_preserves_game_terms(self):
        self.assertEqual(to_simplified("英傑"), "英桀")
        self.assertEqual(to_simplified("點擊空白處關閉介面"), "点击空白处关闭界面")
        self.assertEqual(to_simplified("重砲、鐮刀、火箭鎚"), "重炮、镰刀、火箭锤")
        self.assertEqual(to_simplified("鏖滅空夢無限剎那黃金"), "鏖灭空梦无限刹那黄金")
        self.assertEqual(to_simplified("戰況報告 狀態清單 往世樂土"), "战况报告 状态列表 往世乐土")

    def test_elysian_policy_handles_traditional_chinese(self):
        self.assertEqual(normalize("「華裳」的祝福"), "「华裳」的祝福")
        self.assertEqual(read_floor([{"text": "第12層", "box": [20, 20, 60, 24]}]), (12, 17))
        state = RunState(buff_priority=["全伤", "双伤", "回复能量"], disabled_families=["鏖灭"])
        self.assertGreater(score_signet("敵人受到的全傷害提高30%", state), 50)
        self.assertTrue(signet_disabled("劍鋒·劍塚", state))

    def test_choice_and_shop_rows_with_traditional_labels(self):
        items = [
            {"text": "黃金的詠嘆", "box": [540, 200, 140, 28]},
            {"text": "核心", "box": [1140, 200, 50, 28]},
            {"text": "300", "box": [1140, 230, 50, 24]},
        ]
        choices = choice_rows(items)
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]["text"], "黄金的咏叹")
        shops = shop_rows(items)
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0]["title"], "黄金的咏叹")
        self.assertEqual(shops[0]["price"], 300)

    def test_affix_rules_handles_traditional_chinese(self):
        parsed = classify_line("重砲、鐮刀、火箭鎚角色攻擊提升22.7", "异能", "重炮、镰刀、火箭锤")
        self.assertTrue(parsed.effective)
        self.assertAlmostEqual(parsed.value, 22.7)
        parsed_attr = classify_line("異能角色攻擊提升13.0", "异能", "重炮、镰刀、火箭锤")
        self.assertTrue(parsed_attr.effective)
        self.assertAlmostEqual(parsed_attr.value, 13.0)
        side = evaluate_side(
            ["攻擊提升9.0", "異能角色攻擊提升13.0"],
            "异能",
            "太刀、十字架、链刃",
        )
        self.assertTrue(side.reliable)
        self.assertAlmostEqual(side.total, 22.0)

    def test_elysian_ultimate_variant_profile_loaded(self):
        variants = CHARACTER_PROFILES["真我·人之律者"]["variants"]
        self.assertIn("elysian_czcx", variants)
        ult = variants["elysian_czcx"]
        self.assertEqual(ult["requires_signet"], "初绽")
        self.assertTrue(ult["ultimate"])
        self.assertTrue(ult["star_ring"])
        self.assertTrue(ult["branch"])
        self.assertEqual(ult["signet_plan"]["exclusive"][:2], ["初绽", "初醒"])



if __name__ == "__main__":
    unittest.main()
