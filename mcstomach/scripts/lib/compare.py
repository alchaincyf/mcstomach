"""两份胃的可解释比较。

只用**双方都有数据**的维度。**不产出任何百分比 / 评分 / 契合指数**——
没有全量用户库，也没有统计模型支持那种数字。

这是库函数（不在 SKILL.md 的命令清单里），供流程与测试调用。
"""

from . import schema
from .distill import TASTE_HINTS

KNOWN_CONFIDENCE = ("high", "medium", "user_stated")


def _rules_by_dimension(profile):
    grouped = {}
    for rule in profile.get("rules", []):
        dimension = rule.get("dimension")
        grouped.setdefault(dimension, []).append(rule)
    return grouped


def _known(grouped, dimension):
    """该维度是否有可用的已知信息。"""
    return [
        rule for rule in grouped.get(dimension, [])
        if rule.get("confidence") in KNOWN_CONFIDENCE and rule.get("status") != "retired"
    ]


def _keywords_in(rule):
    text = str(rule.get("text", ""))
    for evidence in rule.get("evidence", []):
        text += " " + str(evidence.get("stat", ""))
    return text


def compare_stomachs(profile_a, profile_b):
    grouped_a = _rules_by_dimension(profile_a)
    grouped_b = _rules_by_dimension(profile_b)

    shared, a_only, b_only, insufficient = [], [], [], []
    for dimension in schema.RULE_DIMENSIONS:
        a_known = _known(grouped_a, dimension)
        b_known = _known(grouped_b, dimension)
        if a_known and b_known:
            shared.append(dimension)
        elif a_known:
            a_only.append(dimension)
            insufficient.append({
                "dimension": dimension,
                "reason": "只有一方有数据",
            })
        elif b_known:
            b_only.append(dimension)
            insufficient.append({
                "dimension": dimension,
                "reason": "只有一方有数据",
            })

    agreements, conflicts = [], []
    for dimension in shared:
        a_known = _known(grouped_a, dimension)
        b_known = _known(grouped_b, dimension)
        clash = None
        if dimension == "hard_avoid":
            for rule in a_known:
                for other in b_known:
                    if _keywords_in(rule) and any(
                        hint in _keywords_in(rule) and hint in _keywords_in(other)
                        for hint in TASTE_HINTS
                    ):
                        clash = (rule, other)
        for a_rule in a_known:
            if clash:
                break
            for b_rule in b_known:
                a_text, b_text = _keywords_in(a_rule), _keywords_in(b_rule)
                if any(hint in a_text and hint in b_text for hint in TASTE_HINTS) and dimension != "hard_avoid":
                    agreements.append({
                        "dimension": dimension,
                        "a": a_rule.get("text"),
                        "b": b_rule.get("text"),
                        "basis": "双方在同一维度上有同向记录",
                    })
                    break

        if clash:
            conflicts.append({
                "dimension": dimension,
                "a": clash[0].get("text"),
                "b": clash[1].get("text"),
                "basis": "一方明确忌口，另一方在同一项上常点",
            })

    # 只展示一个真实分歧
    surfaced_conflict = conflicts[:1]

    compromise = None
    for dimension, template in (
        ("staple", "点一份双方都能接受的主食，分着吃"),
        ("side", "配餐只点一份，谁吃谁拿"),
        ("drink", "饮料各点各的，别互相将就"),
    ):
        if dimension in shared:
            compromise = template
            break
    if compromise is None and shared:
        compromise = "先在双方都常点的那一类里挑一样，各退一步"

    return {
        "shared_dimensions": sorted(shared),
        "a_only_dimensions": sorted(a_only),
        "b_only_dimensions": sorted(b_only),
        "agreements": agreements,
        "conflicts": surfaced_conflict,
        "insufficient": insufficient,
        "compromise": compromise,
        "sample_note": (
            "比较只基于双方共同已知的维度；不重叠的部分说明样本不足，"
            "不代表不合拍。"
        ),
        "no_fabricated_number_reason": (
            "没有全量用户库，也没有统计模型，因此不提供任何合拍百分比或评分。"
        ),
        "is_relationship_judgement": False,
    }
