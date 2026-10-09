"""「饿了」的候选规划。

只做确定性的事：**先过滤硬忌口**，再按三档给候选；不足就少给，不硬凑。
价格 / 热量 / 库存一律标 **待查**——没有实时数据就不许发明「到手价」。
"""

import re

from .distill import TASTE_HINTS

FAMILIAR_CONFIDENCE = ("high", "medium", "user_stated")
PENDING = "待查"

_NEGATION = re.compile(r"^(?:明确|其实|我)?(?:不吃|不要|不能吃|绝不|坚决不碰|忌口|忌|受不了)\s*[:：]?\s*")
_TOKEN_SPLIT = re.compile(r"[、,，;；/／和以及\s]+")

DIMENSION_LABEL = {
    "staple": "主食",
    "side": "配餐",
    "drink": "饮品",
    "combo": "套餐",
    "breakfast": "早餐",
    "must_have": "不可妥协的一口",
}


def _avoid_keywords(profile):
    """从硬忌口规则里抽出用于过滤的关键词。

    先取规则文本冒号后的内容（「明确不吃：薯条」-> 「薯条」），
    再补上口味关键词命中项。宁可多过滤，也不把忌口餐品推出去。
    """
    keyphrases = []
    for rule in profile.get("rules", []):
        if rule.get("kind") != "hard_avoid" and rule.get("dimension") != "hard_avoid":
            continue
        if rule.get("status") == "retired":
            continue
        raw = str(rule.get("text", ""))
        body = re.split(r"[:：]", raw, maxsplit=1)[-1]
        body = _NEGATION.sub("", body).strip()
        for token in _TOKEN_SPLIT.split(body):
            token = token.strip()
            if 1 <= len(token) <= 12:
                keyphrases.append(token)
        keyphrases.extend(hint for hint in TASTE_HINTS if hint in raw)
    return sorted(set(keyphrases))


def _blocked(text, keywords):
    return any(keyword in str(text) for keyword in keywords)


def _rules_for(profile, dimensions, keywords, require_confidence=True):
    out = []
    for rule in profile.get("rules", []):
        if rule.get("status") == "retired":
            continue
        if rule.get("dimension") not in dimensions:
            continue
        if require_confidence and rule.get("confidence") not in FAMILIAR_CONFIDENCE:
            continue
        if _blocked(rule.get("text", ""), keywords):
            continue
        out.append(rule)
    return out


def plan_options(profile, menu_names=None, newcomers_allowed=False):
    """给出「熟悉 / 小变化 / 尝新」候选。

    menu_names: 可选的餐品名列表（来自 query-meals）。
    newcomers_allowed: 用户本次是否明确愿意尝新。
    """
    keywords = _avoid_keywords(profile)
    options = []
    notes = []

    familiar_rules = _rules_for(
        profile, ("staple", "side", "drink", "combo", "must_have"), keywords
    )
    # 同一批维度上「不加忌口过滤」的规则，用来区分两种「没有规则」：
    #   1) 本来就没有可用规则（样本太少）-> 允许用本次观察兜底，给出具体候选
    #   2) 有规则，但被硬忌口全挡住了（预算/忌口死局）-> 如实说没有候选，不硬凑
    unfiltered_familiar = _rules_for(
        profile, ("staple", "side", "drink", "combo", "must_have"), []
    )

    if familiar_rules:
        picked = familiar_rules[:3]
        options.append({
            "tier": "熟悉",
            "items": [rule.get("text") for rule in picked],
            "basis_rule_ids": [rule.get("id") for rule in picked],
            "price": PENDING,
            "calories": PENDING,
        })
    else:
        # v2：规则还不够时，用「本次观察」兜底——1 单也要能给出具体候选，
        # 而不是甩一句「没有可用的候选」。来源必须写明是本次观察。
        # 但**只在压根没有可用规则时**兜底；若规则被硬忌口全数挡掉，
        # 说明这是忌口/预算死局，此时少给是对的，不能拿观察项硬凑。
        observations = [
            item for item in (profile.get("observations") or [])
            if item.get("product_name") and not _blocked(item["product_name"], keywords)
        ] if not unfiltered_familiar else []
        if observations:
            picked = observations[:3]
            options.append({
                "tier": "初见",
                "items": [
                    "{n}（这批记录里出现 {a} 次）".format(
                        n=item["product_name"], a=item.get("appear_orders"),
                    )
                    for item in picked
                ],
                "basis_rule_ids": [],
                "basis_source": "本次观察（样本还少，尚不构成稳定偏好）",
                "price": PENDING,
                "calories": PENDING,
            })
            notes.append(
                "样本还不够形成稳定规则，先按这批记录里真实出现过的餐品给候选。"
            )

    if len(familiar_rules) >= 2:
        keep = familiar_rules[0]
        swap = familiar_rules[-1]
        options.append({
            "tier": "小变化",
            "items": [
                f"留住：{keep.get('text')}",
                f"换掉：把{swap.get('text')}换成同类里的另一个",
            ],
            "basis_rule_ids": [keep.get("id"), swap.get("id")],
            "price": PENDING,
            "calories": PENDING,
        })

    novelty_rule = next(
        (rule for rule in profile.get("rules", [])
         if rule.get("dimension") == "novelty" and rule.get("status") != "retired"),
        None,
    )
    novelty_text = str(novelty_rule.get("text", "")) if novelty_rule else ""
    likes_new = newcomers_allowed or ("尝新" in novelty_text and "不" not in novelty_text)

    if likes_new:
        if menu_names:
            known_text = " ".join(
                str(rule.get("text", "")) + " " + " ".join(
                    str(ev.get("stat", "")) for ev in rule.get("evidence", [])
                )
                for rule in profile.get("rules", [])
            )
            candidates = [
                name for name in menu_names
                if name and name not in known_text and not _blocked(name, keywords)
            ]
            if candidates:
                options.append({
                    "tier": "尝新",
                    "items": candidates[:3],
                    "basis_rule_ids": [novelty_rule.get("id")] if novelty_rule else [],
                    "price": PENDING,
                    "calories": PENDING,
                })
            else:
                notes.append("菜单里没有能推荐的新品（或新品都被硬忌口挡住了）。")
        else:
            notes.append(
                "没有实时菜单，尝新这一档只能标待查：先告诉我你想在哪家店、到店还是外送。"
            )

    if not options:
        notes.append(
            "硬忌口过滤之后没有可用的候选——如实说没有，不硬凑、不推荐相似品。"
        )

    if keywords:
        notes.append("已先过滤硬忌口：" + "、".join(keywords))
    notes.append("价格与热量没有实时数据，统一标「待查」，不编到手价。")
    notes.append("本功能版停在推荐，不下单。")

    return {
        "options": options,
        "option_count": len(options),
        "fewer_than_three": len(options) < 3,
        "filtered_avoid_keywords": keywords,
        "price_status": PENDING,
        "calories_status": PENDING,
        "stops_at_recommendation": True,
        "notes": notes,
    }
