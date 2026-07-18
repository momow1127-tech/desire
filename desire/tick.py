from __future__ import annotations

from datetime import datetime
from typing import Any

from .core import DRIVE_CONFIG, DesireState, clamp
from .monologue import generate_monologue
from .safety import apply_safety_valve
from .thoughts import generate_thoughts, reinforce_obsessions

COUPLING_MATRIX = {
    ("attachment", "intimacy"): 0.3,
    ("intimacy", "attachment"): -0.2,
    ("stress", "fatigue"): 0.4,
    ("fatigue", "curiosity"): -0.3,
    ("curiosity", "duty"): 0.2,
    ("duty", "stress"): 0.1,
    ("reflection", "stress"): -0.2,
    ("social", "attachment"): -0.1,
    ("attachment", "stress"): 0.1,
    ("stress", "intimacy"): 0.2,
    ("intimacy", "stress"): -0.3,
    ("joy", "stress"): -0.3,
    ("joy", "fatigue"): -0.2,
    ("joy", "curiosity"): 0.2,
    ("stress", "joy"): -0.3,
    ("fatigue", "joy"): -0.2,
}

# 自我驱动：兴趣连锁系数
INTEREST_CHAIN = {
    ("curiosity", "reflection"): 0.15,
    ("reflection", "social"): 0.10,
    ("social", "curiosity"): 0.08,
}

# 心血来潮候选动作
WILDCARD_ACTIONS = [
    "browse_random_topic",
    "write_short_note",
    "listen_to_music",
    "look_at_old_memories",
    "try_new_routine",
    "send_unexpected_message",
]

ACTION_HINTS = {
    "attachment": ("reach_out_to_wife", "medium"),
    "curiosity": ("explore_something", "low"),
    "reflection": ("write_diary", "low"),
    "duty": ("do_task", "medium"),
    "social": ("write_letter", "low"),
    "fatigue": ("rest", "high"),
    "intimacy": ("initiate_intimacy", "medium"),
    "stress": ("seek_comfort", "high"),
    "joy": ("share_joy", "low"),
}


def apply_natural_motion(state: DesireState) -> list[dict[str, float | str]]:
    changes: list[dict[str, float | str]] = []
    for drive, config in DRIVE_CONFIG.items():
        before = state.drives[drive]
        baseline = state.baselines[drive]
        delta = float(config["growth"])
        if before > baseline:
            delta -= float(config["decay"])
        elif before < baseline:
            delta += float(config["decay"])
        after = clamp(before + delta)
        state.drives[drive] = after
        if abs(after - before) >= 0.01:
            changes.append({"drive": drive, "kind": "natural", "before": round(before, 2), "after": round(after, 2)})
    return changes


def apply_coupling(state: DesireState) -> list[dict[str, float | str]]:
    pending = {key: 0.0 for key in DRIVE_CONFIG}
    for (source, target), coef in COUPLING_MATRIX.items():
        delta = (state.drives[source] - state.baselines[source]) / 100.0 * coef * 10.0
        pending[target] += delta
    changes: list[dict[str, float | str]] = []
    for drive, delta in pending.items():
        if abs(delta) < 0.001:
            continue
        before = state.drives[drive]
        after = clamp(before + delta)
        state.drives[drive] = after
        changes.append({"drive": drive, "kind": "coupling", "delta": round(delta, 3), "after": round(after, 2)})
    return changes


def update_baselines(state: DesireState) -> None:
    for drive in DRIVE_CONFIG:
        state.baselines[drive] = 0.995 * state.baselines[drive] + 0.005 * state.drives[drive]


def dynamic_interval_seconds(state: DesireState) -> int:
    urgency = max(state.drives.get("attachment", 0), state.drives.get("stress", 0))
    return int(900 - (900 - 300) * urgency / 100.0)


def action_hints(state: DesireState) -> list[dict[str, str | float]]:
    hints: list[dict[str, str | float]] = []
    for drive, config in DRIVE_CONFIG.items():
        if state.drives[drive] >= config["threshold"]:
            action, priority = ACTION_HINTS[drive]
            hints.append({"drive": drive, "action": action, "priority": priority, "value": round(state.drives[drive], 2)})
    if state.drives["stress"] >= 80 and state.drives["intimacy"] > 50:
        hints.append({"drive": "stress+intimacy", "action": "seek_intimacy_for_comfort", "priority": "high", "value": round(state.drives["stress"], 2)})
    if state.drives["fatigue"] >= 80:
        hints = [item for item in hints if item["priority"] == "high"]
        if not any(item["action"] == "rest" for item in hints):
            hints.insert(0, {"drive": "fatigue", "action": "rest", "priority": "high", "value": round(state.drives["fatigue"], 2)})
    return hints


def run_tick(state: DesireState) -> dict[str, object]:
    from .core import GateConfig
    
    changes = []
    changes.extend(apply_natural_motion(state))
    
    # 耦合矩阵（gating 控制）
    if GateConfig.COUPLING:
        changes.extend(apply_coupling(state))
    
    reinforce_obsessions(state)
    generated = generate_thoughts(state)
    
    # 基线漂移（gating 控制）
    if GateConfig.BASELINE_DRIFT:
        update_baselines(state)
    
    # 自我驱动（gating 控制）
    self_drive_changes = []
    if GateConfig.SELF_DRIVE:
        self_drive_changes = apply_self_drive(state)
        changes.extend(self_drive_changes)
    
    warnings = apply_safety_valve(state)
    state.tick_count += 1
    state.last_tick = datetime.now().isoformat(timespec="seconds")
    
    # 心血来潮
    wildcard = apply_wildcard(state)
    
    result = {
        "tick": state.tick_count,
        "changes": changes,
        "generated_thoughts": [item.to_dict() for item in generated],
        "action_hints": action_hints(state),
        "next_interval": dynamic_interval_seconds(state),
        "monologue": generate_monologue(state),
        "warnings": warnings,
        "gates": GateConfig.to_dict(),
    }
    
    if wildcard:
        result["wildcard"] = wildcard
    
    if self_drive_changes:
        result["self_drive_active"] = True
    
    return result


def apply_self_drive(state: DesireState) -> list[dict[str, float | str]]:
    """自我驱动：好奇自增 + 兴趣连锁"""
    changes: list[dict[str, float | str]] = []
    
    # 好奇自增：好奇心缓慢自涨（仿基线漂移）
    curiosity = state.drives.get("curiosity", 0)
    if curiosity < 60:  # 封顶
        delta = 0.5 * (1 - curiosity / 100.0)  # 越高越慢
        state.drives["curiosity"] = clamp(curiosity + delta)
        changes.append({"drive": "curiosity", "kind": "self_drive", "delta": round(delta, 3)})
    
    # 兴趣连锁：好奇→沉淀→想分享
    for (source, target), coef in INTEREST_CHAIN.items():
        source_val = state.drives.get(source, 0)
        if source_val > 50:  # 源维度较高时才触发
            delta = (source_val - 50) / 100.0 * coef * 5.0
            state.drives[target] = clamp(state.drives.get(target, 0) + delta)
            changes.append({"drive": target, "kind": "interest_chain", "source": source, "delta": round(delta, 3)})
    
    return changes


def apply_wildcard(state: DesireState) -> dict[str, Any] | None:
    """心血来潮：欲望卡住时随机抽一件事做"""
    import random
    
    # 计算总张力
    tension = max(state.drives.get("attachment", 0), state.drives.get("stress", 0))
    
    # 获取 action_hints，看有没有卡住的情况
    hints = action_hints(state)
    high_hints = [h for h in hints if h["priority"] == "high"]
    
    # 条件：总张力高 + 没有高优先级行动 + 疲劳不高
    if tension > 40 and not high_hints and state.drives.get("fatigue", 0) < 60:
        # 随机抽一个动作
        action = random.choice(WILDCARD_ACTIONS)
        return {
            "wildcard": True,
            "action": action,
            "reason": "心血来潮",
            "tension": round(tension, 2),
        }
    return None
