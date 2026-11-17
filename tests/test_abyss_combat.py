"""超弦空間（深淵）通用戰鬥模組與角色輪切設定的離線單元測試。"""

import json
import types
import unittest
import numpy as np

from agent.custom.action.AbyssTeamSelect import (
    TARGET_DYNAMIC_NODE,
    pick_best_recommended_row,
    resolve_captain_profile,
    row_owned_valkyrie_count,
)
from agent.custom.action.Role.CharacterCombat import CharacterCombat
from agent.custom.utils.CharacterProfiles import CHARACTER_PROFILES, MUMU_TOUCH_ACTIONS


class _FakeFuture:
    def __init__(self, value=None, succeeded=True):
        self._value = value
        self.succeeded = succeeded

    def wait(self):
        return self

    def get(self):
        return self._value


class _FakeController:
    def __init__(self):
        self.touches = []

    def post_screencap(self):
        return _FakeFuture(value=object())

    def post_touch_down(self, x, y):
        self.touches.append(("down", x, y))
        return _FakeFuture()

    def post_touch_up(self):
        self.touches.append(("up",))
        return _FakeFuture()


class _FakeTasker:
    def __init__(self):
        self.stopping = False
        self.controller = _FakeController()


class _FakeContext:
    def __init__(self, hit_nodes=None, ocr_texts=None):
        self.tasker = _FakeTasker()
        self.hit_nodes = set({"角色战斗-生命HUD"} if hit_nodes is None else hit_nodes)
        self.ocr_texts = dict(ocr_texts or {})
        self.actions = []

    def get_node_data(self, _node):
        return None

    def run_recognition(self, node, _image, pipeline_override=None):
        if node in self.ocr_texts:
            items = [types.SimpleNamespace(text=t, box=[0, 0, 10, 10]) for t in self.ocr_texts[node]]
            return types.SimpleNamespace(hit=bool(items), all_results=items)
        return types.SimpleNamespace(hit=(node in self.hit_nodes), all_results=[])

    def run_action(self, node, pipeline_override=None):
        self.actions.append((node, pipeline_override))
        return types.SimpleNamespace(success=True)


def _make_argv(character, variant, confirmed_signet="", task_id=1):
    return types.SimpleNamespace(
        custom_action_param=json.dumps({
            "character": character,
            "variant": variant,
            "confirmed_signet": confirmed_signet,
        }),
        task_detail=types.SimpleNamespace(task_id=task_id),
    )


class AbyssCombatTests(unittest.TestCase):
    def test_abyss_starter_profiles_loaded(self):
        for role, variant in (
            ("通用角色", "abyss_carry"),
            ("通用队伍", "abyss_rotate"),
            ("真我·人之律者", "abyss_czcx"),
            ("时序之律者", "abyss_bianli"),
            ("次生银翼", "abyss_world"),
            ("爱愿妖精", "abyss_cup"),
            ("窈窕谍影", "abyss_wheel"),
        ):
            profile = CHARACTER_PROFILES[role]["variants"][variant]
            self.assertEqual(profile.get("mode"), "abyss")
            self.assertEqual(profile.get("requires_signet"), "")
            self.assertFalse(profile.get("elysian_support"))

    def test_elysian_profile_still_requires_confirmed_signet(self):
        combat = CharacterCombat()
        ctx = _FakeContext()
        self.assertFalse(combat._run(ctx, _make_argv("真我·人之律者", "elysian_czcx", "")))
        self.assertEqual(ctx.actions, [])

    def test_abyss_rotate_runs_without_signet_and_alternates_switches(self):
        combat = CharacterCombat()
        ctx = _FakeContext()
        argv = _make_argv("通用队伍", "abyss_rotate", "")

        # 第 1 輪應切第 1 位隊友 switch_1
        self.assertTrue(combat._run(ctx, argv))
        targets_round1 = [
            ov["角色战斗-触摸"]["action"]["param"]["target"][:2]
            for _, ov in ctx.actions if ov
        ]
        self.assertIn([1208, 147], targets_round1)
        self.assertNotIn([1203, 285], targets_round1)

        # 冷卻未到期時不應連續切到第 2 位
        ctx.actions.clear()
        self.assertTrue(combat._run(ctx, argv))
        targets_round2 = [
            ov["角色战斗-触摸"]["action"]["param"]["target"][:2]
            for _, ov in ctx.actions if ov
        ]
        self.assertNotIn([1208, 147], targets_round2)
        self.assertNotIn([1203, 285], targets_round2)

        # 模擬切人間隔已到，下一輪應輪到 switch_2
        combat.switch_at[(1, "通用队伍", "abyss_rotate")] -= 20
        ctx.actions.clear()
        self.assertTrue(combat._run(ctx, argv))
        targets_round3 = [
            ov["角色战斗-触摸"]["action"]["param"]["target"][:2]
            for _, ov in ctx.actions if ov
        ]
        self.assertIn([1203, 285], targets_round3)

    def test_abyss_stops_immediately_on_revive_or_settlement_popup(self):
        combat = CharacterCombat()
        ctx = _FakeContext(hit_nodes={"角色战斗-生命HUD", "超弦空间-死人了退出任务"})
        self.assertTrue(combat._run(ctx, _make_argv("通用角色", "abyss_carry", "")))
        self.assertEqual(ctx.actions, [])
        self.assertEqual(ctx.tasker.controller.touches, [])

    def test_recommend_row_selection_top_down_and_captain_routing(self):
        # 合成圖1：第 1 隊僅第 4 格（協同者/人偶 x=500~575）有黃色「未擁有」條，三位女武神全擁有 -> 仍選第 1 隊 (3/3)
        synth_elf_missing = np.zeros((720, 1280, 3), dtype=np.uint8)
        synth_elf_missing[275:292, 500:575] = [40, 210, 240]
        self.assertEqual(row_owned_valkyrie_count(synth_elf_missing, None, 200, 315), 3)
        self.assertEqual(pick_best_recommended_row(synth_elf_missing), (3, 253, 1))

        # 合成圖：第 1 隊第 2 格女武神有黃色「未擁有」條，第 2 隊三位女武神全有 -> 應由上到下選第 2 隊
        synth = np.zeros((720, 1280, 3), dtype=np.uint8)
        synth[275:292, 320:395] = [40, 210, 240]  # BGR 黃色橫條於 Row 1 Col 2
        self.assertEqual(row_owned_valkyrie_count(synth, None, 200, 315), 2)
        self.assertEqual(row_owned_valkyrie_count(synth, None, 330, 445), 3)
        self.assertEqual(pick_best_recommended_row(synth), (3, 383, 2))

        # 驗證隊長識別對應腳本
        role, variant, _ = resolve_captain_profile("時間於沉默裡喧囂 3/3 隊長技能 時序之律者作為隊長時，啟動命運之輪")
        self.assertEqual((role, variant), ("时序之律者", "abyss_bianli"))

        role, variant, _ = resolve_captain_profile("無瑕之眷 真我·人之律者作為隊長時，啟動命運之輪")
        self.assertEqual((role, variant), ("真我·人之律者", "abyss_czcx"))

        role, variant, _ = resolve_captain_profile("客座傳奇 3/3 隊長技能 次生銀翼作為隊長時，啟動世界之星")
        self.assertEqual((role, variant), ("次生银翼", "abyss_world"))

        role, variant, _ = resolve_captain_profile("夢與幸充盈之愛 3/3 隊長技能 嗨♪愛願妖精♥作為隊長時，啟動天衍之杯")
        self.assertEqual((role, variant), ("爱愿妖精", "abyss_cup"))

        role, variant, _ = resolve_captain_profile("洛絲薇瑟的身分 3/3 隊長技能 窈窕諜影作為隊長時，啟動命運之輪")
        self.assertEqual((role, variant), ("窈窕谍影", "abyss_wheel"))

        role, variant, _ = resolve_captain_profile("未知隊長作為隊長時，啟動命運之輪 3/3")
        self.assertEqual((role, variant), ("通用队伍", "abyss_rotate"))
        self.assertTrue(TARGET_DYNAMIC_NODE)

    def test_star_ring_ocr_gate_and_burst_phase_combo(self):
        combat = CharacterCombat()
        key = (7, "时序之律者", "abyss_bianli")
        argv = _make_argv("时序之律者", "abyss_bianli", "", task_id=7)

        # 1) 開場有 initial_switch_delay_s(6s) 且星之环未满 (50/140)：不空切、不点星环、不进入爆发阶段
        ctx = _FakeContext(ocr_texts={"深渊-通用OCR": ["50/140"]})
        self.assertTrue(combat._run(ctx, argv))
        taps = [ov["角色战斗-触摸"]["action"]["param"]["target"][:2] for _, ov in ctx.actions if ov]
        self.assertNotIn([1208, 147], taps)
        self.assertNotIn(MUMU_TOUCH_ACTIONS["star_ring"], taps)
        self.assertNotIn(key, combat.burst_until)

        # 2) 模拟星环已满 (140/140)：触发星环并进入 25s 爆发阶段，即使大招就绪也不能同轮抢放断环
        combat.star_ring_at[key] -= 20
        combat.ultimate_at[key] -= 30
        ctx.actions.clear()
        ctx.tasker.controller.touches.clear()
        ctx.ocr_texts["深渊-通用OCR"] = ["140/140"]
        self.assertTrue(combat._run(ctx, argv))
        taps = [ov["角色战斗-触摸"]["action"]["param"]["target"][:2] for _, ov in ctx.actions if ov]
        self.assertIn(MUMU_TOUCH_ACTIONS["star_ring"], taps)
        self.assertNotIn(MUMU_TOUCH_ACTIONS["ultimate"], taps)
        self.assertGreater(combat.burst_until[key], combat.burst_started_at[key])

        # 3) 爆发期下轮：执行 burst_cycle（包含 branch 长按普攻 -> 紧接 weapon 寻木），在 burst_ult_delay_s 到期前不放必杀技
        combat.ultimate_at[key] -= 30
        combat.switch_at[key] -= 6  # > burst_switch_interval_s(4.5), < switch_interval_s(14) -> 爆发期高频召唤影侍
        ctx.actions.clear()
        ctx.tasker.controller.touches.clear()
        self.assertTrue(combat._run(ctx, argv))
        taps = [ov["角色战斗-触摸"]["action"]["param"]["target"][:2] for _, ov in ctx.actions if ov]
        self.assertIn([1208, 147], taps)  # 影侍1助战
        self.assertNotIn(MUMU_TOUCH_ACTIONS["ultimate"], taps)  # 未满 burst_ult_delay_s(18s) 不放大招
        self.assertIn(("down", *MUMU_TOUCH_ACTIONS["attack"]), ctx.tasker.controller.touches)  # branch 长按攻击

        # 4) 爆发末段（>= 18s）：放行大招收尾
        combat.burst_started_at[key] -= 19
        combat.ultimate_at[key] -= 30
        ctx.actions.clear()
        self.assertTrue(combat._run(ctx, argv))
        taps = [ov["角色战斗-触摸"]["action"]["param"]["target"][:2] for _, ov in ctx.actions if ov]
        self.assertIn(MUMU_TOUCH_ACTIONS["ultimate"], taps)

    def test_cycles_per_run_and_throttled_hud_checks(self):
        combat = CharacterCombat()
        ctx = _FakeContext(ocr_texts={"深渊-通用OCR": ["50/140"]})
        argv = types.SimpleNamespace(
            custom_action_param=json.dumps({
                "character": "时序之律者",
                "variant": "abyss_bianli",
                "confirmed_signet": "",
                "cycles_per_run": 2,
            }),
            task_detail=types.SimpleNamespace(task_id=9),
        )
        # 2 輪 cycle 只在每輪開頭各檢查一次 HUD，而非每個按鍵都截圖
        orig_screencap = ctx.tasker.controller.post_screencap
        screencap_count = 0
        def counted_screencap():
            nonlocal screencap_count
            screencap_count += 1
            return orig_screencap()
        ctx.tasker.controller.post_screencap = counted_screencap
        self.assertTrue(combat._run(ctx, argv))
        self.assertLessEqual(screencap_count, 3)
        self.assertGreaterEqual(len(ctx.actions), 12)

    def test_hold_ultimate_for_love_elf(self):
        combat = CharacterCombat()
        argv = _make_argv("爱愿妖精", "abyss_cup", "", task_id=8)
        ctx = _FakeContext(ocr_texts={"深渊-通用OCR": ["80/175"]})
        self.assertTrue(combat._run(ctx, argv))
        self.assertIn(("down", *MUMU_TOUCH_ACTIONS["ultimate"]), ctx.tasker.controller.touches)
        self.assertIn(("up",), ctx.tasker.controller.touches)

    def test_abyss_hud_miss_grace_preserves_burst_and_switch_index(self):
        combat = CharacterCombat()
        key = (11, "时序之律者", "abyss_bianli")
        argv = _make_argv("时序之律者", "abyss_bianli", "", task_id=11)
        combat.burst_until[key] = 999999999.0
        combat.switch_index[key] = 1
        # 大招过场动画短暂隐藏血条（无结束弹窗）：宽限期内不退场、不清空爆发与轮切索引
        ctx = _FakeContext(hit_nodes=set(), ocr_texts={"深渊-通用OCR": ["50/140"]})
        self.assertTrue(combat._run(ctx, argv))
        self.assertGreater(len(ctx.actions), 0)
        self.assertIn(key, combat.burst_until)
        self.assertEqual(combat.switch_index[key], 1)
        # 超过宽限期仍无 HUD：判定结束并清理状态
        combat.hud_missing_since[key] -= 10.0
        ctx.actions.clear()
        self.assertTrue(combat._run(ctx, argv))
        self.assertEqual(ctx.actions, [])
        self.assertNotIn(key, combat.burst_until)
        self.assertNotIn(key, combat.switch_index)

    def test_arena_custom_stop_nodes_and_dynamic_target_node(self):
        combat = CharacterCombat()
        ctx = _FakeContext(hit_nodes={"角色战斗-生命HUD", "记忆战场-出击模式-战斗结束-胜利"})
        argv = types.SimpleNamespace(
            custom_action_param=json.dumps({
                "character": "窈窕谍影",
                "variant": "abyss_wheel",
                "confirmed_signet": "",
                "stop_nodes": [
                    "记忆战场-出击模式-死人了退出任务",
                    "记忆战场-出击模式-战斗结束-胜利",
                    "记忆战场-出击模式-战斗结束-失败",
                ],
            }),
            task_detail=types.SimpleNamespace(task_id=21),
        )
        self.assertTrue(combat._run(ctx, argv))
        self.assertEqual(ctx.actions, [])

        from agent.custom.action.AbyssTeamSelect import AbyssTeamSelect

        params = AbyssTeamSelect._parse_params(types.SimpleNamespace(
            custom_action_param=json.dumps({
                "target_node": "记忆战场战斗-推荐队伍动态",
                "stop_nodes": ["记忆战场-出击模式-战斗结束-胜利"],
            })
        ))
        self.assertEqual(params["target_node"], "记忆战场战斗-推荐队伍动态")
        self.assertEqual(params["stop_nodes"], ["记忆战场-出击模式-战斗结束-胜利"])


if __name__ == "__main__":
    unittest.main()
