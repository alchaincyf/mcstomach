#!/usr/bin/env python3
"""视觉主题分配（25 号新增）的定向测试。

全部使用**虚构夹具**与**受控随机源**（FixedSlotRng），不涉及任何真实账号；
演示档案是受控测试产物，**不是任何人的中奖结果**。

运行：
    python3 tests/test_visual.py
可选：
    python3 tests/test_visual.py --demo-out <目录>
        额外落两份受控演示档案（一个常规一个隐藏），明确标注非中奖。
"""

import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts"))

from lib import export as export_mod   # noqa: E402
from lib import privacy as privacy_mod  # noqa: E402
from lib import schema                  # noqa: E402
from lib import validate as validate_mod  # noqa: E402
from lib import visual as visual_mod    # noqa: E402

sys.path.insert(0, HERE)
from test_mcstomach import FROZEN_NOW, build_profile  # noqa: E402


class NeverRng:
    """一旦被调用就失败的受控源：用于证明沿用路径绝不重抽。"""

    def randrange(self, n):
        raise AssertionError("已有 visual.theme 时不得再调用随机源")


class SlotDistributionTests(unittest.TestCase):
    def test_exactly_one_hidden_in_five_equal_slots(self):
        """5 个等概率槽位里恰好 1 个隐藏——用受控源逐槽验证，不靠统计。"""
        themes = []
        for slot in range(len(visual_mod.SLOTS)):
            profile, _ = build_profile("orders_fish_coffee_fictional.json")
            _, summary = visual_mod.assign_visual(
                profile, rng=visual_mod.FixedSlotRng(slot)
            )
            self.assertTrue(summary["assigned"])
            self.assertEqual(summary["theme"], visual_mod.SLOTS[slot])
            themes.append(summary["theme"])
        self.assertEqual(themes.count("retro_pixel"), 1)
        self.assertEqual(themes.count("plush"), 4)
        self.assertEqual(len(visual_mod.SLOTS), 5)

    def test_hidden_theme_is_retro_pixel(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        _, summary = visual_mod.assign_visual(
            profile, rng=visual_mod.FixedSlotRng(4)
        )
        self.assertEqual(summary["theme"], "retro_pixel")
        self.assertEqual(profile["visual"]["schema"], schema.VISUAL_SCHEMA_ID)

    def test_assignment_touches_only_visual(self):
        """分配前后口味 facts/rules/naming/等级一律不变。"""
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        before = json.dumps(
            {k: v for k, v in profile.items() if k != "visual"},
            ensure_ascii=False, sort_keys=True,
        )
        visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(4))
        after = json.dumps(
            {k: v for k, v in profile.items() if k != "visual"},
            ensure_ascii=False, sort_keys=True,
        )
        self.assertEqual(before, after)


class PersistenceTests(unittest.TestCase):
    def _assigned(self, slot=0):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(slot))
        return profile

    def test_repeat_call_never_rerolls(self):
        profile = self._assigned(4)
        _, summary = visual_mod.assign_visual(profile, rng=NeverRng())
        self.assertFalse(summary["assigned"])
        self.assertTrue(summary["reused"])
        self.assertEqual(summary["theme"], "retro_pixel")

    def test_nickname_change_keeps_theme(self):
        profile = self._assigned(4)
        profile["naming"]["primary"] = "新起的外号"
        profile["naming"]["source"] = "user_claimed"
        _, summary = visual_mod.assign_visual(profile, rng=NeverRng())
        self.assertEqual(summary["theme"], "retro_pixel")

    def test_export_keeps_theme(self):
        profile = self._assigned(4)
        card = export_mod.build_share_card(profile)
        self.assertEqual(card["visual_theme"], "retro_pixel")
        errors, _ = validate_mod.validate_profile(profile, card)
        self.assertEqual(errors, [])
        # 再导一次也不变
        again = export_mod.build_share_card(visual_mod.assign_visual(profile)[0])
        self.assertEqual(again["visual_theme"], "retro_pixel")


class CompatAndPrivacyTests(unittest.TestCase):
    def test_old_profile_without_visual_is_valid(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        self.assertNotIn("visual", profile)
        errors, _ = validate_mod.validate_profile(profile)
        self.assertEqual(errors, [])
        card = export_mod.build_share_card(profile)
        self.assertNotIn("visual_theme", card)

    def test_invalid_visual_theme_rejected_not_overwritten(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        profile["visual"] = {"schema": schema.VISUAL_SCHEMA_ID, "theme": "golden"}
        with self.assertRaises(ValueError):
            visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(0))
        self.assertEqual(profile["visual"]["theme"], "golden")

    def test_unknown_visual_field_rejected(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        profile["visual"] = {
            "schema": schema.VISUAL_SCHEMA_ID,
            "theme": "plush",
            "internal_seed": 12345,
        }
        with self.assertRaises(ValueError):
            visual_mod.assign_visual(profile)
        errors, _ = validate_mod.validate_profile(profile)
        self.assertTrue(any("visual" in e for e in errors))

    def test_no_seed_or_identity_written(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(4))
        self.assertEqual(set(profile["visual"]), {"schema", "theme"})
        self.assertEqual(privacy_mod.find_forbidden_keys(profile["visual"]), [])


def write_demos(out_dir):
    """受控演示档案：一个常规一个隐藏，明确标注非中奖。"""
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name, slot in (("受控演示-毛绒常规-非中奖", 0), ("受控演示-复古像素-非中奖", 4)):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(slot))
        card = export_mod.build_share_card(profile)
        errors, _ = validate_mod.validate_profile(profile, card)
        if errors:
            raise SystemExit(f"演示档案校验失败：{errors}")
        target = os.path.join(out_dir, name)
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "profile.json"), "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        with open(os.path.join(target, "share-card.json"), "w", encoding="utf-8") as fh:
            json.dump(card, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        with open(os.path.join(target, "README.txt"), "w", encoding="utf-8") as fh:
            fh.write(
                "受控测试演示档案（FixedSlotRng 固定槽位产生），"
                "用于核对分配逻辑，不是任何真实用户的中奖结果。\n"
                f"theme = {profile['visual']['theme']}\n"
            )
        made.append(target)
    return made


class CarryWithStomachTests(unittest.TestCase):
    """27 号：主题随「我的胃.md」与个人 SKILL.md 携带。"""

    def _mds(self, slot):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        visual_mod.assign_visual(profile, rng=visual_mod.FixedSlotRng(slot))
        return (
            export_mod.render_shareable_stomach(profile),
            export_mod.render_personal_skill(profile),
            profile,
        )

    def test_both_themes_carried_in_shareable_md(self):
        for slot, theme, label in (
            (0, "plush", "毛绒常规款"),
            (4, "retro_pixel", "复古像素隐藏款"),
        ):
            stomach, _, profile = self._mds(slot)
            self.assertIn("## 卡片外观", stomach)
            self.assertIn(f"theme：{theme}（{label}）", stomach)
            self.assertIn("已确定，重新出卡沿用；不重抽；表情仍可选择", stomach)
            # 与 JSON 一致
            self.assertEqual(profile["visual"]["theme"], theme)
            # 吃什么规则原样保留
            self.assertIn("## 硬忌口", stomach)
            self.assertIn("## 口味偏好", stomach)

    def test_both_themes_carried_in_personal_skill(self):
        for slot, theme, label in (
            (0, "plush", "毛绒常规款"),
            (4, "retro_pixel", "复古像素隐藏款"),
        ):
            _, skill, profile = self._mds(slot)
            self.assertIn("## 卡片外观", skill)
            self.assertIn(f"theme：{theme}（{label}）", skill)
            self.assertEqual(profile["visual"]["theme"], theme)
            self.assertIn("## 口味规则", skill)

    def test_md_theme_matches_json(self):
        stomach, skill, profile = self._mds(4)
        json_theme = json.loads(json.dumps(profile))["visual"]["theme"]
        self.assertIn(f"theme：{json_theme}", stomach)
        self.assertIn(f"theme：{json_theme}", skill)

    def test_old_profile_without_visual_has_no_appearance_section(self):
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        self.assertNotIn("visual", profile)
        stomach = export_mod.render_shareable_stomach(profile)
        skill = export_mod.render_personal_skill(profile)
        self.assertNotIn("卡片外观", stomach)
        self.assertNotIn("卡片外观", skill)
        self.assertNotIn("theme：", stomach)
        self.assertNotIn("theme：", skill)

    def test_illegal_visual_theme_not_carried(self):
        """theme 被篡改成非法值：MD 不携带也不猜主题。"""
        profile, _ = build_profile("orders_fish_coffee_fictional.json")
        profile["visual"] = {"schema": schema.VISUAL_SCHEMA_ID, "theme": "golden"}
        stomach = export_mod.render_shareable_stomach(profile)
        skill = export_mod.render_personal_skill(profile)
        self.assertNotIn("卡片外观", stomach)
        self.assertNotIn("卡片外观", skill)

    def test_no_identity_leaks_in_appearance(self):
        stomach, skill, _ = self._mds(4)
        for text in (stomach, skill):
            segment = text.split("## 卡片外观", 1)[-1].split("## ", 1)[0]
            self.assertEqual(
                privacy_mod.find_forbidden_keys({"x": segment.strip()}), []
            )
            self.assertNotIn("seed", segment)


if __name__ == "__main__":
    argv = sys.argv[1:]
    demo_dir = None
    if "--demo-out" in argv:
        index = argv.index("--demo-out")
        if index + 1 >= len(argv):
            raise SystemExit("--demo-out 需要一个目录参数")
        demo_dir = argv[index + 1]
        del argv[index:index + 2]
    if demo_dir:
        for target in write_demos(demo_dir):
            print(f"[demo] {target}")
    unittest.main(argv=[sys.argv[0]] + argv, verbosity=2)
