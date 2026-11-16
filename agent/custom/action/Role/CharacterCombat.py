"""短周期角色执行器。每次输入前重新检查战斗 HUD，支持随时停止。"""
import json
import time
import re

from maa.custom_action import CustomAction
from ...utils import DesktopInput, MoveInput
from ...utils.CharacterProfiles import CHARACTER_PROFILES, MUMU_TOUCH_ACTIONS

# 安卓端（模拟器）只能走触摸坐标；桌面端资源包会提供 角色战斗-按键-<动作> 节点改用键码。
TOUCH_NODE = "角色战斗-触摸"
# 长按使用独立触摸点：周期性点按走 contact=0，其 touch_up 会把同一 touch 的按住一起抬起。
HOLD_CONTACT = 1
# 按住期间定期刷新一次（松开→立即重按）：修复误判战斗后"幽灵按住"导致下一层不普攻。
HOLD_REFRESH_S = 5.0


# 默认触控坐标：通用队伍或未单独声明切换键位时兜底使用。
DEFAULT_TOUCH_ACTIONS = {
    "switch_1": [1208, 147],
    "switch_2": [1203, 285],
    "support_1": [1208, 147],
    "support_2": [1203, 285],
    "stage_skill": [938, 424],
}
# 深渊等通用模式遇到弹窗或结算时必须立刻停手，避免误点复活/消费或结算页。
ABYSS_STOP_NODES = (
    "超弦空间-死人了退出任务",
    "超弦空间-战斗结束-胜利",
    "超弦空间-战斗结束-失败",
)
# 星之环充能数字（如 50/140、140/140）位于星之环按钮底部中央（720p 基准坐标）。
STAR_RING_ROI = [850, 540, 125, 80]
# 需要长按触发的派生动作：动作名 -> (基础按键, 时长字段, 默认毫秒数)
HOLD_ACTIONS = {
    "branch": ("attack", "branch_hold_ms", 520),
    "hold_ultimate": ("ultimate", "ultimate_hold_ms", 950),
    "hold_weapon": ("weapon", "weapon_hold_ms", 650),
}


class CharacterCombat(CustomAction):
    def __init__(self):
        super().__init__()
        self.input_nodes = {}
        self.support_at = {}
        self.switch_at = {}
        self.switch_index = {}
        self.signet_check_at = {}
        self.ultimate_at = {}
        self.star_ring_at = {}
        self.burst_started_at = {}
        self.burst_until = {}
        self.dodge_at = {}
        self.target_lock_at = {}
        self.movement_at = {}
        self.movement_index = {}
        self.branch_at = {}
        self.stage_skill_at = {}
        self.last_check_at = {}
        self.last_image = {}
        self.hud_missing_since = {}
        self.holding = False
        self.hold_key = None
        self.hold_release = True
        self.hold_at = 0.0

    @staticmethod
    def _is_desktop(context):
        """桌面端判定：沿用 MoveInput/DesktopInput 的节点存在性检测，异常时按安卓端处理。"""
        try:
            return DesktopInput.available(context)
        except Exception:
            return False

    @staticmethod
    def _still_fighting(context, image):
        """仍在战斗：任一判据命中即可，避免单一识别抖动就停手。

        「战斗中」模板与血量 HUD 都可能偶发漏检：血量文本在低血量/特效遮挡时
        会被读成没有斜杠的串（如 "1366"），只认它会误判战斗结束导致原地站桩。
        """
        for node in ("战斗逻辑-战斗中", "角色战斗-生命HUD", "超弦空间-战斗中"):
            try:
                result = context.run_recognition(node, image)
            except Exception:
                continue
            if result and result.hit:
                return True
        return False

    @staticmethod
    def _stop_popup_hit(context, image, profile):
        """检查是否已弹出结算或复活确认等需要立刻停手的界面。"""
        nodes = profile.get("stop_nodes")
        if nodes is None and profile.get("mode") == "abyss":
            nodes = ABYSS_STOP_NODES
        for node in nodes or ():
            try:
                result = context.run_recognition(node, image)
            except Exception:
                continue
            if result and result.hit:
                return node
        return ""

    @staticmethod
    def _star_ring_state(context, profile, image=None):
        """识别星之环充能文本（如 50/140、140/140），返回 'ready' / 'not_ready' / 'unknown'。"""
        ocr_node = profile.get("star_ring_ocr_node", "深渊-通用OCR")
        try:
            if image is None:
                image = context.tasker.controller.post_screencap().wait().get()
            result = context.run_recognition(
                ocr_node,
                image,
                pipeline_override={ocr_node: {"recognition": "OCR", "roi": STAR_RING_ROI, "expected": [".+"]}},
            )
        except Exception:
            return "unknown"
        if not result or not result.hit:
            return "unknown"
        text = " ".join(r.text for r in (result.all_results or []) if getattr(r, "text", ""))
        matches = re.findall(r"(\d{1,3})\s*[/7|]\s*(\d{3})", text)
        if not matches:
            return "unknown"
        for cur_s, cap_s in matches:
            cur, cap = int(cur_s), int(cap_s)
            if cap > 0 and cur >= cap:
                return "ready"
        return "not_ready"

    def _drop_hold(self, context):
        """画面已不是战斗：立即松开并清掉记账，避免把"幽灵按住"带进下一层。"""
        if self.hold_key is None:
            return
        self.holding = False
        self.hold_key = None
        self._release_attack(context)

    def run(self, context, argv):
        self.holding = False
        self.hold_release = True
        try:
            return self._run(context, argv)
        except Exception as exc:
            # 异常不得越过 ctypes 回调边界，否则框架可能继续高速重试。
            print(f"CharacterCombat stopped: {type(exc).__name__}: {exc}", flush=True)
            return False
        finally:
            # 只有判定为战斗结束/停止/异常时才松开；动作跑完会保持按住，
            # 由下一次节点重入接着按住并轮转周期动作。
            if self.holding and self.hold_release:
                self.holding = False
                self.hold_key = None
                self._release_attack(context)

    def _reset_fight_state(self, key):
        """单场战斗结束时清理阶段与轮切计时，避免跨层沿用旧状态。"""
        self.burst_until.pop(key, None)
        self.burst_started_at.pop(key, None)
        self.switch_at.pop(key, None)
        self.switch_index.pop(key, None)
        self.last_check_at.pop(key, None)
        self.last_image.pop(key, None)
        self.hud_missing_since.pop(key, None)

    def _check_combat_state(self, context, image, profile, key=None):
        """统一检查仍在战斗、战后禁用或深渊弹窗：'continue' / 'ended' / 'stop'。"""
        if self._stop_popup_hit(context, image, profile):
            print("CharacterCombat: stop popup/settlement detected, stopping", flush=True)
            self._drop_hold(context)
            if key is not None:
                self._reset_fight_state(key)
            return "ended"
        if not self._still_fighting(context, image):
            grace_s = float(
                profile.get("hud_miss_grace_s", 4.5 if profile.get("mode") == "abyss" else 0.0) or 0.0
            )
            if grace_s > 0 and key is not None:
                now = time.monotonic()
                missing_since = self.hud_missing_since.setdefault(key, now)
                if now - missing_since < grace_s:
                    # 大招/QTE 过场动画会短暂隐藏血条，保留爆发与轮切状态并持续衔接出招。
                    return "continue"
            self._drop_hold(context)
            if key is not None:
                self._reset_fight_state(key)
            return "ended"
        if key is not None:
            self.hud_missing_since.pop(key, None)
        if profile.get("mode") != "abyss":
            disabled = context.run_recognition("角色战斗-战后禁用", image)
            if disabled and disabled.hit:
                print("CharacterCombat: post-battle HUD, no input", flush=True)
                return "stop"
        return "continue"

    @staticmethod
    def _wait_interval(context, interval_ms):
        """短周期等待动作间隔，期间随时响应 tasker 停止。"""
        deadline = time.monotonic() + interval_ms / 1000.0
        while time.monotonic() < deadline:
            if context.tasker.stopping:
                return False
            time.sleep(0.02)
        return True

    def _run(self, context, argv):
        raw_params = getattr(argv, "custom_action_param", None)
        if isinstance(raw_params, str):
            try:
                params = json.loads(raw_params or "{}")
            except json.JSONDecodeError:
                params = {}
        else:
            params = raw_params or {}
        if not isinstance(params, dict):
            params = {}
        role = params.get("character")
        variant = params.get("variant")
        profile = CHARACTER_PROFILES.get(role, {}).get("variants", {}).get(variant)
        if not profile:
            print("CharacterCombat: 未找到角色/流派配置，停止输入", flush=True)
            return False
        required = profile.get("requires_signet") or ""
        if required and params.get("confirmed_signet") != required:
            print("CharacterCombat: 角色/变体/核心刻印未确认，停止输入", flush=True)
            return False
        key = (argv.task_detail.task_id, role, variant)
        cycles = max(1, int(params.get("cycles_per_run") or profile.get("cycles_per_run") or 1))
        for idx in range(cycles):
            status = self._run_cycle(context, role, variant, profile, key, force_check=(idx == 0))
            if status != "continue":
                return status == "ended"
        return True

    def _run_cycle(self, context, role, variant, profile, key, force_check=True):
        """执行单轮战斗连招；按 state_check_interval_s 节流截屏检状态，避免频繁暂停打断出招。"""
        if context.tasker.stopping:
            return "error"
        desktop = self._is_desktop(context)
        default_check_gap = 3.0 if profile.get("mode") == "abyss" else 0.0
        state_check_interval_s = float(profile.get("state_check_interval_s", default_check_gap))
        now = time.monotonic()
        last_check = self.last_check_at.get(key)
        need_check = (
            force_check
            or desktop
            or state_check_interval_s <= 0
            or last_check is None
            or (now - last_check >= state_check_interval_s)
        )
        if need_check:
            image = context.tasker.controller.post_screencap().wait().get()
            state = self._check_combat_state(context, image, profile, key)
            if state != "continue":
                return state
            now = time.monotonic()
            self.last_check_at[key] = now
            self.last_image[key] = image
        else:
            image = self.last_image.get(key)
        last_check_at = self.last_check_at.get(key, now)
        burst_duration_s = float(profile.get("burst_duration_s", 0) or 0)
        in_burst = burst_duration_s > 0 and now < self.burst_until.get(key, 0.0)
        base_cycle = profile.get("burst_cycle") if in_burst and profile.get("burst_cycle") is not None else profile.get("cycle")
        actions = list(base_cycle or ())
        star_ring_confirmed = not bool(profile.get("star_ring_ocr_gate", False))
        star_ring_interval = float(profile.get("star_ring_interval_s", 18))
        plan_star_ring = False
        if not in_burst and profile.get("star_ring") and now - self.star_ring_at.get(key, -1000) >= star_ring_interval:
            sr_state = self._star_ring_state(context, profile, image=image) if profile.get("star_ring_ocr_gate") else "unknown"
            if sr_state == "not_ready":
                retry_s = float(profile.get("star_ring_retry_s", 2.0))
                self.star_ring_at[key] = now - max(0.0, star_ring_interval - retry_s)
            else:
                star_ring_confirmed = star_ring_confirmed or (sr_state == "ready")
                plan_star_ring = True
        burst_ult_delay = float(profile.get("burst_ult_delay_s", 0) or 0)
        defer_ult_for_star_ring = plan_star_ring and burst_duration_s > 0 and burst_ult_delay > 0
        if profile.get("ultimate") and not defer_ult_for_star_ring:
            if in_burst:
                ult_interval = float(profile.get("burst_ultimate_interval_s", profile.get("ultimate_interval_s", 15)))
                ult_due = (now - self.burst_started_at.get(key, now) >= burst_ult_delay) and (
                    now - self.ultimate_at.get(key, -1000) >= ult_interval
                )
            else:
                ult_due = bool(profile.get("non_burst_ultimate", True)) and (
                    now - self.ultimate_at.get(key, -1000) >= float(profile.get("ultimate_interval_s", 15))
                )
            if ult_due:
                ult_action = (
                    profile.get("burst_ultimate_action")
                    if in_burst and profile.get("burst_ultimate_action")
                    else profile.get("ultimate_action", "ultimate")
                )
                actions.insert(0, ult_action)
        if plan_star_ring:
            actions.insert(0, "star_ring")
        if profile.get("dodge") and now - self.dodge_at.get(key, -1000) >= profile.get("dodge_interval_s", 2.2):
            actions.insert(0, "dodge")
        if profile.get("target_lock") and now - self.target_lock_at.get(key, -1000) >= profile.get("target_lock_interval_s", 3.0):
            actions.insert(0, "target_lock")
        if profile.get("movement") and now - self.movement_at.get(key, -1000) >= profile.get("movement_interval_s", 1.8):
            # 可选的定时移动；华裳配置不启用，不能替代场景中的接敌判断。
            actions.insert(0, "movement")
        branch_due = (
            profile.get("branch")
            and not profile.get("hold_attack")
            and not (in_burst and profile.get("burst_cycle") is not None)
            and now - self.branch_at.get(key, -1000) >= profile.get("branch_interval_s", 6.0)
        )
        if branch_due:
            # 出招表中的“攻击（长按）”是分支·无声的利刃；它不能用一次
            # Click 代替，否则只会继续普通攻击而丢失高倍率分支。
            actions.insert(0, "branch")
        if profile.get("elysian_support") and now - self.signet_check_at.get(key, -1000) >= 3:
            actions.insert(0, "elysian_support")
        if profile.get("stage_skill") and now - self.stage_skill_at.get(key, -1000) >= profile.get("stage_skill_interval_s", 8):
            actions.insert(0, "stage_skill")
        if in_burst and "burst_switch_cycle" in profile:
            switch_seq = profile.get("burst_switch_cycle") or ()
        else:
            switch_seq = profile.get("switch_cycle") or ()
        switch_interval = float(
            profile.get("burst_switch_interval_s", profile.get("switch_interval_s", 10))
            if in_burst
            else profile.get("switch_interval_s", 10)
        )
        if key not in self.switch_at and float(profile.get("initial_switch_delay_s", 0) or 0) > 0:
            self.switch_at[key] = now - max(0.0, switch_interval - float(profile["initial_switch_delay_s"]))
        planned_switch = None
        if switch_seq and now - self.switch_at.get(key, -1000) >= switch_interval:
            # 深渊三人小队轮切：每个周期只切下一位队友，避免同轮连续按两个头像直接跳过中间队友。
            idx = self.switch_index.get(key, 0)
            planned_switch = switch_seq[idx % len(switch_seq)]
            actions.insert(0, planned_switch)
        opening = list(profile.get("opening") or ())
        if opening and now - self.support_at.get(key, -1000) >= profile.get("support_interval_s", 30):
            actions = opening + actions
            self.support_at[key] = now
        interval_ms = profile.get("interval_ms", 120)
        if profile.get("hold_attack"):
            # 全程按住普攻：按住期间不再点攻击；其他动作走不同按钮，与按住互不冲突。
            actions = [a for a in actions if a != "attack"]
            if self.hold_key is not None and self.hold_key != key:
                # 上一场没松开（异常退出等）先补一次松开，避免按键残留。
                self._release_attack(context)
                self.hold_key = None
            self.holding = True
            self.hold_release = True          # 任何提前返回都松手；正常跑完会改回 False
            if self.hold_key != key:
                if not self._press_attack(context):
                    return "error"
                self.hold_key = key
                self.hold_at = time.monotonic()
            elif time.monotonic() - self.hold_at >= HOLD_REFRESH_S:
                # 两个节点无 pre/post_delay：松开后立刻重按，空档只有一次 adb 往返（约 10~40ms）。
                self._release_attack(context)
                self.hold_at = time.monotonic()
                if not self._press_attack(context):
                    return "error"
            if not actions:
                # cycle 为空且没有其它待执行动作：本轮开头已做 HUD 检查，直接等 interval 后保持按住返回。
                print(f"CharacterCombat: {role}/{variant} hold_attack 持续按住，直到战斗结束", flush=True)
                if not self._wait_interval(context, interval_ms):
                    return "error"
                self.hold_release = False     # 战斗仍在：保持按住
                return "continue"
        for action in actions:
            if context.tasker.stopping:
                return "error"
            if not desktop and time.monotonic() - last_check_at >= state_check_interval_s:
                # 安卓端按节流间隔复检战斗状态：深渊拉长至每 3.0s 一次，避免打断持续出招节奏。
                image = context.tasker.controller.post_screencap().wait().get()
                state = self._check_combat_state(context, image, profile, key)
                if state != "continue":
                    return state
                last_check_at = time.monotonic()
                self.last_check_at[key] = last_check_at
                self.last_image[key] = image
            if action == "elysian_support":
                self.signet_check_at[key] = time.monotonic()
                # 桌面端或节流帧超过 0.25s 时单独取一帧最新充能条，避免读到旧帧。
                support_img = (
                    context.tasker.controller.post_screencap().wait().get()
                    if (desktop or time.monotonic() - last_check_at > 0.25)
                    else image
                )
                charge = context.run_recognition("角色战斗-英桀助战充能", support_img)
                values = re.findall(r"(\d{2,4})[/7](\d{2,4})", "".join(r.text for r in charge.all_results)) if charge and charge.hit else []
                if not any(a == b and int(a) > 0 for a, b in values):
                    continue
            if action == "movement":
                if not self._move_short(context, key, profile):
                    return "error"
                self.movement_at[key] = time.monotonic()
                print(f"CharacterCombat: {role}/{variant} movement", flush=True)
                if not self._wait_interval(context, interval_ms):
                    return "error"
                continue
            if action in HOLD_ACTIONS:
                if not self._hold_action(context, action, profile):
                    return "error"
                done_at = time.monotonic()
                if action == "branch":
                    self.branch_at[key] = done_at
                elif action == "hold_ultimate":
                    self.ultimate_at[key] = done_at
                print(f"CharacterCombat: {role}/{variant} {action}", flush=True)
                if not self._wait_interval(context, interval_ms):
                    return "error"
                continue
            # 战后仍有生命条，但武器位已变成刷新门按钮，必须单独拒绝。
            node = self._input_node(context, action)
            if node == TOUCH_NODE:
                point = MUMU_TOUCH_ACTIONS.get(action) or DEFAULT_TOUCH_ACTIONS.get(action)
                if not point:
                    print(f"CharacterCombat: 动作 {action} 缺少触控坐标，跳过", flush=True)
                    continue
                result = context.run_action(node, pipeline_override={
                    node: {"action": {"type": "Click", "param": {
                        "target": [*point, 1, 1]
                    }}}
                })
            else:
                result = context.run_action(node)
            if not result or not result.success:
                return "error"
            print(f"CharacterCombat: {role}/{variant} {action}", flush=True)
            exec_now = time.monotonic()
            if planned_switch and action == planned_switch:
                self.switch_at[key] = exec_now
                self.switch_index[key] = self.switch_index.get(key, 0) + 1
                planned_switch = None
            elif action == "support_2":
                self.support_at[key] = exec_now
            elif action == "ultimate":
                self.ultimate_at[key] = exec_now
            elif action == "star_ring":
                self.star_ring_at[key] = exec_now
                if burst_duration_s > 0 and star_ring_confirmed:
                    self.burst_started_at[key] = exec_now
                    self.burst_until[key] = exec_now + burst_duration_s
            elif action == "dodge":
                self.dodge_at[key] = exec_now
            elif action == "target_lock":
                self.target_lock_at[key] = exec_now
            elif action == "stage_skill":
                self.stage_skill_at[key] = exec_now
            if not self._wait_interval(context, interval_ms):
                return "error"
        self.hold_release = False  # 动作跑完、战斗仍在：保持按住
        return "continue"

    def _input_node(self, context, action):
        """返回该动作要执行的节点名：桌面端用键码节点，缺失时回退触摸点击。"""
        cached = self.input_nodes.get(action)
        if cached:
            return cached
        candidates = [f"角色战斗-按键-{action}"]
        if action in ("switch_1", "switch_2"):
            candidates.append(f"角色战斗-按键-support_{action[-1]}")
        elif action == "stage_skill":
            candidates.append("角色战斗-按键-elysian_support")
        name = TOUCH_NODE
        for candidate in candidates:
            try:
                if context.get_node_data(candidate):
                    name = candidate
                    break
            except Exception:
                continue
        self.input_nodes[action] = name
        return name

    def _touch_based(self, context, node):
        """桌面端按键节点没有接触点概念；只有触摸型节点才需要指定 contact。"""
        try:
            data = context.get_node_data(node) or {}
        except Exception:
            return True
        action = data.get("action") if isinstance(data, dict) else None
        action_type = action.get("type") if isinstance(action, dict) else action
        return action_type in (None, "TouchDown", "TouchUp")

    def _press_attack(self, context):
        """按下普攻并保持；触摸型节点用独立 contact，避免被点按的 touch_up 抬起。"""
        override = None
        if self._touch_based(context, "按下攻击_action"):
            override = {"按下攻击_action": {"action": {"param": {"contact": HOLD_CONTACT}}}}
        result = context.run_action("按下攻击_action", pipeline_override=override)
        return bool(result and result.success)

    def _release_attack(self, context):
        try:
            override = None
            if self._touch_based(context, "松开攻击_action"):
                override = {"松开攻击_action": {"action": {"param": {"contact": HOLD_CONTACT}}}}
            context.run_action("松开攻击_action", pipeline_override=override)
        except Exception as exc:
            print(f"CharacterCombat release stopped: {type(exc).__name__}: {exc}", flush=True)

    def _move_short(self, context, key, profile):
        """相对当前视角短走；不代表已锁定敌人。"""
        controller = context.tasker.controller
        vectors = profile.get("movement_vectors") or ((0, -62), (38, -50), (-38, -50), (0, -62))
        index = self.movement_index.get(key, 0)
        dx, dy = vectors[index % len(vectors)]
        self.movement_index[key] = index + 1
        if MoveInput.walk(context, dx, float(profile.get("movement_hold_ms", 260)) / 1000.0, dy):
            return True                                     # 桌面端没有摇杆，改用 WASD
        held = DesktopInput.hold(context)  # 桌面端要先按住 Alt 才能拖视角，安卓端恒为 False
        try:
            if not controller.post_touch_down(153, 571).wait().succeeded:
                return False
            if not controller.post_touch_move(153 + int(dx), 571 + int(dy)).wait().succeeded:
                controller.post_touch_up().wait()
                return False
            time.sleep(float(profile.get("movement_hold_ms", 260)) / 1000.0)
            return controller.post_touch_up().wait().succeeded
        except Exception as exc:
            print(f"CharacterCombat movement stopped: {type(exc).__name__}: {exc}", flush=True)
            try:
                controller.post_touch_up().wait()
            except Exception:
                pass
            return False
        finally:
            DesktopInput.release(context, held)

    def _hold_action(self, context, action, profile):
        """长按派生动作（分支/长按大招/长按武器）：桌面端用 LongPressKey，安卓端用触摸按下-抬起。"""
        base_action, ms_key, default_ms = HOLD_ACTIONS[action]
        hold_ms = float(profile.get(ms_key, default_ms))
        node = self._input_node(context, action)
        if node != TOUCH_NODE:
            result = context.run_action(node)
            return bool(result and result.success)
        if action != "branch":
            base_node = self._input_node(context, base_action)
            if base_node != TOUCH_NODE:
                try:
                    data = context.get_node_data(base_node) or {}
                    key_code = ((data.get("action") or {}).get("param") or {}).get("key")
                except Exception:
                    key_code = None
                if key_code is not None:
                    result = context.run_action(
                        base_node,
                        pipeline_override={
                            base_node: {"action": {"type": "LongPressKey", "param": {"key": key_code, "duration": int(hold_ms)}}}
                        },
                    )
                    return bool(result and result.success)
        controller = context.tasker.controller
        point = MUMU_TOUCH_ACTIONS.get(base_action) or DEFAULT_TOUCH_ACTIONS.get(base_action)
        if not point:
            print(f"CharacterCombat: 长按动作 {action} 缺少基础坐标", flush=True)
            return False
        x, y = point
        try:
            if not controller.post_touch_down(x, y).wait().succeeded:
                return False
            time.sleep(hold_ms / 1000.0)
            return controller.post_touch_up().wait().succeeded
        except Exception as exc:
            print(f"CharacterCombat {action} stopped: {type(exc).__name__}: {exc}", flush=True)
            try:
                controller.post_touch_up().wait()
            except Exception:
                pass
            return False
