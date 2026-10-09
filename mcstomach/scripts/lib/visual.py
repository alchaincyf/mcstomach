"""视觉主题的首次分配：一次抽签，永久沿用。

20% 隐藏款口径：**5 个等概率槽位里 1 个**是复古像素隐藏款，
其余 4 个是毛绒常规款。单位是一个胃档案**首次**生成时的外观，
不是每张图反复抽。

- 结果写进 profile 的独立 `visual` 段；**存在合法结果就原样沿用**——
  改昵称、重导出、重出图、换会话都不会重抽，也不再调用随机源。
- **不存随机种子、不存抽取时间、不带身份信息**；抽签与积分、金额、
  消费频次完全无关。
- 不改口味 facts / rules / naming / 置信度 / 本人认领名，只新增 `visual`。
"""

import random

from . import schema

#: 5 个等概率槽位：前 4 个常规毛绒，最后 1 个隐藏像素（1/5 = 20%）。
SLOTS = ("plush", "plush", "plush", "plush", "retro_pixel")

HIDDEN_THEME = "retro_pixel"
DEFAULT_THEME = "plush"


class FixedSlotRng:
    """受控随机源（测试/演示用）：固定返回第 `slot` 个槽位。

    **只用于测试与明确标注的演示数据**，普通使用必须走真实随机路径。
    """

    def __init__(self, slot):
        if not 0 <= slot < len(SLOTS):
            raise ValueError(f"slot 必须在 0..{len(SLOTS) - 1}，实际是 {slot!r}")
        self.slot = slot
        self.calls = 0

    def randrange(self, n):
        if n != len(SLOTS):
            raise ValueError("FixedSlotRng 只服务槽位抽签")
        self.calls += 1
        return self.slot


def assign_visual(profile, rng=None):
    """给 profile 首次分配视觉主题；已有合法结果就原样沿用。

    返回 (profile, summary)。summary 字段：
    - assigned: bool，本次是否发生了首次分配
    - theme: "plush" | "retro_pixel"
    - reused: bool，是否沿用了已有结果（沿用时绝不调用随机源）
    """
    if rng is None:
        rng = random.SystemRandom()

    existing = profile.get("visual")
    if existing is not None:
        if not isinstance(existing, dict):
            raise ValueError("visual 段已存在但不是对象，拒绝覆盖，请先人工核对")
        theme = existing.get("theme")
        if theme not in schema.VISUAL_THEMES:
            raise ValueError(f"visual.theme 非法：{theme!r}，拒绝覆盖，请先人工核对")
        unknown = set(existing) - {"schema", "theme"}
        if unknown:
            raise ValueError(f"visual 段出现未知字段：{sorted(unknown)}")
        return profile, {"assigned": False, "theme": theme, "reused": True}

    slot = rng.randrange(len(SLOTS))
    profile["visual"] = {
        "schema": schema.VISUAL_SCHEMA_ID,
        "theme": SLOTS[slot],
    }
    return profile, {
        "assigned": True,
        "theme": SLOTS[slot],
        "reused": False,
    }
