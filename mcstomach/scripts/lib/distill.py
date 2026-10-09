"""从归一事实生成 profile.json。

只做「证据 → 候选规则 → 命名建议」的确定性部分。
措辞润色、幽默、追问话术由 SKILL.md 与 references 里的流程负责，不在这里堆规则引擎。

两条硬约束：
- **命名与宣言必须绑同一批证据 ID 和计数**，不许各自套模板；
- 状态未知的订单不进统计，因此不可能撑起 `high` 或稳定类型。
"""

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta

from . import privacy, schema

CN_TZ = timezone(timedelta(hours=8))

REPEAT_MIN_ORDERS = 3
BREAKFAST_MIN_ORDERS = 3
BREAKFAST_MIN_RATIO = 0.5
VARIETY_FOR_SINGLE = 3

#: 一轮最多 2 题、总计最多 5 题
MAX_QUESTIONS = 5
MAX_QUESTIONS_PER_ROUND = 2

#: 命名种子 —— 原创方案，不是用户真实结果，也不是现成热门梗。
NAME_SYNONYMS = {
    "薯条常驻嘉宾": "薯条赞助商",
    "麦门早八生": "早八麦门人",
    "菜单钉子户": "菜单不换派",
    "单品唯粉": "只认这一口",
    "固定搭档": "雷打不动二人组",
}
FRIES_NAME = "薯条常驻嘉宾"

#: 自述版命名模板（dimension -> 外号模板）。只用于本人主动确认的自述偏好。
STATED_TEMPLATES = {
    "must_have": "{item}不可妥协",
    "side": "{item}常驻嘉宾",
    "staple": "{item}钉子户",
    "drink": "{item}专一",
}
STATED_PRIORITY = ["must_have", "side", "staple", "drink"]


def _now(now=None):
    if now is None:
        return datetime.now(CN_TZ)
    if isinstance(now, str):
        return datetime.fromisoformat(now)
    return now


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _safe_id(text):
    cleaned = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fa5]+", "_", str(text))
    return cleaned.strip("_") or "x"


def _short_name(name):
    """去掉名称里的括号补充和套餐后缀，让外号读起来像人话。"""
    if not name:
        return "那一口"
    text = re.split(r"[（(]", str(name))[0].strip()
    text = re.sub(r"(套餐|组合)$", "", text).strip()
    return text or "那一口"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _empty_naming():
    return {
        "source": None,
        "locked": False,
        "primary": None,
        "habit_tag": None,
        "candidates": [],
        "basis": None,
        "basis_rule_ids": [],
        "evidence_ids": [],
        "basis_counts": [],
        "disclaimer": schema.DISCLAIMER,
    }


def _confidence_for(entry, personal_orders):
    """置信度只看**已完成的本人订单**里的独立加点次数。

    状态未知的订单根本不在 personal_orders 里，所以不可能撑起 high。
    """
    if personal_orders < REPEAT_MIN_ORDERS or entry["appear_orders"] < REPEAT_MIN_ORDERS:
        return "low"
    if entry["standalone_orders"] >= 2:
        return "high"
    return "medium"


def _dimension_for(category):
    return {
        "drink": ("drink", "habit", "常点【{name}】"),
        "staple": ("staple", "preference", "主食里反复出现【{name}】"),
        "combo": ("combo", "habit", "反复点【{name}】这个套餐"),
        "dessert": ("side", "habit", "甜点反复出现【{name}】"),
    }.get(category, ("side", "habit", "配餐里反复出现【{name}】"))


def _recurring_rules(facts):
    rules = []
    personal = facts["counts"]["personal_valid_orders"]
    for entry in facts["stats"]["product_freq"]:
        if entry["appear_orders"] < REPEAT_MIN_ORDERS:
            continue
        dimension, kind, template = _dimension_for(entry["category"])
        confidence = _confidence_for(entry, personal)
        stat = (
            f"{personal} 笔可归于本人的订单里【{entry['name']}】出现 "
            f"{entry['appear_orders']} 次，其中 {entry['standalone_orders']} 次为独立加点"
        )
        if entry["combo_orders"]:
            stat += f"，{entry['combo_orders']} 次来自套餐内含"
        rules.append({
            "id": f"r_{dimension}_{_safe_id(entry['key'])}",
            "text": template.format(name=entry["name"]),
            "dimension": dimension,
            "kind": kind,
            "evidence": [{
                "type": "order_stat",
                "stat": stat,
                "sample": f"{personal} 笔",
                "strength": {
                    "high": "high", "medium": "medium",
                    "low": "low", "user_stated": "user_stated",
                }[confidence],
                "source": "order-list",
            }],
            "confidence": confidence,
            "confirmed_by": None,
            "status": "active",
            "history": [],
        })
    return rules


def _pattern_rules(facts):
    rules = []
    stats = facts["stats"]
    personal = facts["counts"]["personal_valid_orders"]
    if personal < REPEAT_MIN_ORDERS:
        return rules

    if stats["distinct_staple"] >= 2:
        rules.append({
            "id": "r_staple_variety",
            "text": f"主食不固定，订单里出现过 {stats['distinct_staple']} 种不同主食",
            "dimension": "staple",
            "kind": "habit",
            "evidence": [{
                "type": "order_stat",
                "stat": (
                    f"{personal} 笔可归于本人的订单里，主食分别是 "
                    + "、".join(stats["staple_names"])
                ),
                "sample": f"{personal} 笔",
                "strength": "high",
                "source": "order-list",
            }],
            "confidence": "high",
            "confirmed_by": None,
            "status": "active",
            "history": [],
        })

    breakfast = stats["segment_freq"].get("breakfast", 0)
    if breakfast >= BREAKFAST_MIN_ORDERS and breakfast / personal >= BREAKFAST_MIN_RATIO:
        rules.append({
            "id": "r_segment_breakfast",
            "text": f"订单多落在早餐时段（05:00–10:30）：{breakfast} 笔",
            "dimension": "breakfast",
            "kind": "habit",
            "evidence": [{
                "type": "order_stat",
                "stat": f"{personal} 笔可归于本人的订单里，{breakfast} 笔时间落在早餐窗口",
                "sample": f"{personal} 笔",
                "strength": "medium",
                "source": "order-list",
            }],
            "confidence": "medium",
            "confirmed_by": None,
            "status": "active",
            "history": [],
        })
    return rules


_ANSWER_RULE_MAP = {
    "hard_avoid": ("hard_avoid", "hard_avoid", "明确不吃：{text}"),
    "must_have": ("must_have", "preference", "不可妥协的一口：{text}"),
    "budget": ("budget", "preference", "价格取舍：{text}"),
    "novelty": ("novelty", "preference", "尝新倾向：{text}"),
    "staple": ("staple", "preference", "自述常点：{text}"),
    "side": ("side", "preference", "自述常点：{text}"),
    "drink": ("drink", "preference", "自述常点：{text}"),
    "session": ("session", "preference", "自述情况：{text}"),
}


def _answer_rules(answers):
    rules = []
    for index, answer in enumerate(answers):
        dimension = answer.get("dimension")
        if dimension not in _ANSWER_RULE_MAP:
            continue
        mapped, kind, template = _ANSWER_RULE_MAP[dimension]
        raw = (answer.get("text") or "").strip()
        if not raw:
            continue
        # 自述文本也可能夹带隐私或指令：清洗 + 标记，绝不执行
        text, scrub_hits, injection = privacy.sanitize_user_text(raw)
        evidence = {
            "type": "user_statement",
            "stat": text,
            "sample": "用户自述",
            "strength": "user_stated",
            "source": "questionnaire",
        }
        if scrub_hits:
            evidence["scrubbed"] = scrub_hits
        if injection:
            evidence["injection_flagged"] = injection
        rules.append({
            "id": f"r_stated_{mapped}_{index}",
            "text": template.format(text=text),
            "dimension": mapped,
            "kind": kind,
            "evidence": [evidence],
            "confidence": "user_stated",
            "confirmed_by": "user",
            "status": "active",
            "history": [],
        })
    return rules


def _unresolved(facts):
    """只对真正有歧义的点提问，且总量受控。"""
    questions = []
    grouped = [
        item for item in facts["items"]
        if item["included"] and item["group_suspected"]
    ]
    if grouped:
        biggest = max(
            grouped,
            key=lambda item: sum(
                _num(p.get("quantity")) for p in item["products"]
                if p["source"] == "standalone"
            ),
        )
        names = [
            p["name"] for p in biggest["products"]
            if p["source"] == "standalone" and p["name"]
        ]
        label = names[0] if names else "同一餐品"
        top_qty = max(
            [_num(p.get("quantity")) for p in biggest["products"]
             if p["source"] == "standalone"] or [0]
        )
        questions.append({
            "id": "q_group_1",
            "question": f"有一笔点了 {int(top_qty)} 份【{label}】，是你自己吃的，还是帮人带的？",
            "dimension": "session",
            "reason": "疑似代点/团体单，影响该笔能否计入个人偏好",
        })
    unconfirmed = [
        item for item in facts["items"] if item["status_class"] == "unconfirmed"
    ]
    if unconfirmed:
        questions.append({
            "id": "q_status_1",
            "question": "有几笔订单的状态我这边读不出来，那几次你吃上了吗？",
            "dimension": "session",
            "reason": "订单状态未能识别，已排除在偏好统计之外",
        })
    return questions


#: 口味关键词，用来找「自述忌口 vs 订单实际」的矛盾
TASTE_HINTS = [
    "辣", "牛", "猪", "鸡", "鱼", "虾", "香菜", "洋葱", "生菜", "番茄",
    "芝士", "咖啡", "冰", "甜", "咸", "酸", "奶", "蛋",
]


def _conflict_questions(facts, answers):
    """自述忌口与订单实际冲突时，先问最有区分度的一句。"""
    personal_products = set()
    for order in facts["items"]:
        if order["status_class"] != "completed" or order["group_suspected"]:
            continue
        for product in order["products"]:
            if product["name"]:
                personal_products.add(product["name"])

    questions = []
    for index, answer in enumerate(answers):
        if answer.get("dimension") != "hard_avoid":
            continue
        text = str(answer.get("text") or "").strip()
        if not text:
            continue
        hit = None
        for name in sorted(personal_products):
            if any(kw in text and kw in name for kw in TASTE_HINTS):
                hit = name
                break
        if hit:
            questions.append({
                "id": f"q_conflict_{index}",
                "question": (
                    f"你说「{text}」，但订单里有【{hit}】"
                    "——那是给别人点的，还是这条要改？"
                ),
                "dimension": "hard_avoid",
                "reason": "自述忌口与订单证据冲突，先问清再落规则",
            })
    return questions


def _rule_id_for(entry, facts):
    dimension, _kind, _template = _dimension_for(entry["category"])
    return f"r_{dimension}_{_safe_id(entry['key'])}"


def _binding_counts(entry, personal):
    return {
        "product_name": entry["name"],
        "appear_orders": entry["appear_orders"],
        "standalone_orders": entry["standalone_orders"],
        "combo_orders": entry["combo_orders"],
        "personal_orders": personal,
        "is_fries": bool(entry.get("is_fries")),
        "category": entry["category"],
    }


def _order_naming(facts):
    """由订单模式命名。命名与依据计数绑在一起。"""
    naming = _empty_naming()
    stats = facts["stats"]
    personal = facts["counts"]["personal_valid_orders"]
    if stats["tier"] != "type_ready" or personal < REPEAT_MIN_ORDERS:
        return naming

    freq = stats["product_freq"]
    if not freq:
        return naming

    top = freq[0]
    ties = [e for e in freq if e["appear_orders"] >= personal]
    fries = next(
        (e for e in freq if e.get("is_fries") and e["appear_orders"] >= REPEAT_MIN_ORDERS),
        None,
    )
    single_staple = [
        e for e in freq
        if e["category"] == "staple" and e["appear_orders"] >= REPEAT_MIN_ORDERS
    ]
    combo_repeat = [
        e for e in freq
        if e["category"] == "combo" and e["appear_orders"] >= REPEAT_MIN_ORDERS
    ]
    breakfast = stats["segment_freq"].get("breakfast", 0)

    primary = None
    habit_tag = None
    picked = []

    if len(ties) >= 2:
        # 两道餐品同频 -> 说组合偏好，不要压成「只爱某一个」
        primary = "固定搭档"
        picked = ties[:2]
        habit_tag = " + ".join(_short_name(e["name"]) for e in picked)
    elif len(ties) == 1 and len(freq) <= VARIETY_FOR_SINGLE:
        primary = "单品唯粉"
        picked = ties
    elif fries is not None and stats["distinct_staple"] >= 2:
        primary = FRIES_NAME
        picked = [fries]
    elif single_staple and stats["distinct_staple"] <= 1:
        primary = "菜单钉子户"
        picked = single_staple[:1]
    elif combo_repeat and stats["distinct_staple"] <= 1:
        primary = "菜单钉子户"
        picked = combo_repeat[:1]
    elif breakfast >= BREAKFAST_MIN_ORDERS and breakfast / personal >= 0.6:
        primary = "麦门早八生"
        picked = []
    elif top["appear_orders"] >= REPEAT_MIN_ORDERS:
        primary = f"{_short_name(top['name'])}常客"
        picked = [top]
    else:
        # 没有任何餐品真正重复出现：不发明「常客」，输出本次口味
        return naming

    # 双保险：命名引用「薯条」但没有任何薯条证据 -> 不给身份
    if "薯条" in primary and fries is None:
        return naming

    if habit_tag is None:
        if primary != FRIES_NAME and fries is not None:
            habit_tag = "薯条不缺席"
        elif stats["distinct_staple"] >= 2:
            habit_tag = "主食自由派"
        elif breakfast >= BREAKFAST_MIN_ORDERS:
            habit_tag = "早高峰选手"

    candidates = [primary]
    if NAME_SYNONYMS.get(primary):
        candidates.append(NAME_SYNONYMS[primary])

    ids = [_rule_id_for(e, facts) for e in picked]
    if not picked and breakfast >= BREAKFAST_MIN_ORDERS:
        ids = ["r_segment_breakfast"]

    naming.update({
        "source": "orders",
        "primary": primary,
        "habit_tag": habit_tag,
        "candidates": candidates,
        "basis": "来自订单计数",
        "basis_rule_ids": list(ids),
        "evidence_ids": list(ids),
        "basis_counts": [_binding_counts(e, personal) for e in picked],
    })
    if not picked and breakfast:
        naming["basis_counts"] = [{
            "product_name": "早餐时段", "appear_orders": breakfast,
            "standalone_orders": 0, "combo_orders": 0,
            "personal_orders": personal, "is_fries": False,
            "category": "breakfast",
        }]
    return naming


#: 自述里的口语前缀，命名前要削掉
_STATED_LEADIN = re.compile(
    r"^(?:我)?(?:一定要有|必须要有|不能没有|不可以没有|少不了的?是?|"
    r"最离不开的?是?|一定要|必须有|每次都点|常点|一般点|通常是|基本都点)"
)
_STATED_SPLIT = re.compile(r"[，,、。;；:：/／\s]+")


def _stated_item(raw):
    """从自述句子里抠出餐品名，供命名使用。"""
    text = _STATED_SPLIT.split(str(raw).strip())[0]
    text = re.sub(r"^(?:不|不能|别)吃", "", text)
    text = _STATED_LEADIN.sub("", text).strip()
    if len(text) > 8:
        tail = re.split(r"[的是]", text)[-1].strip()
        text = tail if 0 < len(tail) <= 8 else text[:8]
    return text


def _stated_naming(answers, stated_rules):
    """自述版命名：问卷是自述数据，可以生成趣味类型，但必须标明来源。"""
    naming = _empty_naming()
    if not stated_rules:
        return naming
    by_dimension = {}
    for rule in stated_rules:
        by_dimension.setdefault(rule["dimension"], rule)

    picked = []
    for dimension in STATED_PRIORITY:
        rule = by_dimension.get(dimension)
        if not rule:
            continue
        raw = str((rule.get("evidence") or [{}])[0].get("stat", ""))
        item = _stated_item(raw)
        if not item:
            continue
        picked.append((dimension, item, rule))
        if len(picked) >= 2:
            break

    if not picked:
        return naming

    dimension, item, rule = picked[0]
    primary = STATED_TEMPLATES[dimension].format(item=_short_name(item))

    # 只有自述里真的提到薯条，才允许「薯条」字样
    if "薯条" in primary and "薯条" not in str(rule.get("evidence")):
        return _empty_naming()

    naming.update({
        "source": "user_stated",
        "primary": primary,
        "habit_tag": (_short_name(picked[1][1]) if len(picked) > 1 else None),
        "candidates": [primary],
        "basis": "来自你的自述（未接订单）",
        "basis_rule_ids": [r["id"] for _d, _i, r in picked],
        "evidence_ids": [r["id"] for _d, _i, r in picked],
        "basis_counts": [],
    })
    return naming


def _confirmed_must_have(rules):
    return any(
        r.get("dimension") == "must_have" and r.get("confirmed_by") == "user"
        for r in (rules or [])
    )


def _self_statement(naming, facts=None, rules=None):
    """宣言必须和命名绑同一批证据与计数。"""
    primary = naming.get("primary")
    counts = naming.get("basis_counts") or []
    evidence_ids = list(naming.get("evidence_ids") or [])
    if not primary:
        freq = ((facts or {}).get("stats") or {}).get("product_freq") or []
        names = "、".join(e["name"] for e in freq[:2] if e["name"])
        if names:
            text = f"这次的口味：{names}。样本还少，先不下结论。"
        else:
            text = "还没有足够的订单可以下结论——先不下结论。"
        return {"text": text, "evidence_ids": [], "basis_rule_ids": []}

    if naming.get("source") == "user_stated":
        first = (naming.get("basis_rule_ids") or [None])[0]
        text = f"你自己说的：{primary}。这条来自你的自述，不是订单算出来的。"
        return {"text": text, "evidence_ids": evidence_ids, "basis_rule_ids": [first]}

    if primary == "固定搭档" and len(counts) >= 2:
        a, b = counts[0], counts[1]
        if a["appear_orders"] == b["appear_orders"]:
            pair = f"在 {a['personal_orders']} 笔里都出现 {a['appear_orders']} 次"
        else:
            pair = (
                f"在 {a['personal_orders']} 笔里分别出现 "
                f"{a['appear_orders']} 次和 {b['appear_orders']} 次"
            )
        text = (
            f"【{a['product_name']}】和【{b['product_name']}】{pair}"
            "——固定搭档，反复同框。"
        )
        # 「不可妥协」是订单证明不了的，只有本人明确确认过才配写
        if _confirmed_must_have(rules):
            text += "你自己确认过：少了哪一样都算没吃成。"
    elif counts and counts[0].get("appear_orders") and counts[0]["appear_orders"] >= counts[0]["personal_orders"]:
        if counts[0]["is_fries"]:
            # 只有**每一笔都出现**才配说「从头坐到尾」
            text = "主角换了又换，薯条从头坐到尾。"
        else:
            text = f"本胃已把【{counts[0]['product_name']}】设成默认选项。"
    elif counts:
        first = counts[0]
        text = (
            f"出现最多的是【{first['product_name']}】，"
            f"{first['personal_orders']} 笔里出现了 {first['appear_orders']} 次。"
        )
    else:
        text = f"{primary}——依据你的订单时间段，不是每一笔都有它。"
    return {"text": text, "evidence_ids": evidence_ids,
            "basis_rule_ids": list(naming.get("basis_rule_ids") or [])}


def _points_value(points):
    if points is None:
        return None
    if isinstance(points, (int, float, str)):
        return points
    if isinstance(points, dict):
        if "accumulativePoint" in points:
            return points["accumulativePoint"]
        data = points.get("data")
        if isinstance(data, dict) and "accumulativePoint" in data:
            return data["accumulativePoint"]
    return None


def _source_kind(counts, answers):
    if counts["raw_orders"] <= 0:
        return "questionnaire"
    return "mixed" if answers else "mcp_orders"


def _roles(answers):
    roles = {"profile_owner": "self", "payer": "self", "receiver": "self"}
    for answer in answers:
        if answer.get("question_id") == "q5" or answer.get("dimension") == "roles":
            text = str(answer.get("text") or "")
            if re.search(r"帮|同事|家人|朋友|一起|带了|顺带", text):
                roles["receiver"] = "self_and_others"
    return roles


def _profile_id(counts, rules, stamp):
    payload = json.dumps(
        {
            "raw": counts["raw_orders"],
            "personal": counts["personal_valid_orders"],
            "rules": sorted(rule["id"] for rule in rules),
            "date": stamp[:10],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"sto_{digest[:8]}"


# ------------------------------------------------------------------ v2 事实摘要

def _brief_entry(entry):
    """把归一里的一条餐品统计压成可携带的短摘要。

    键名用 `product_name`，避免撞上隐私黑名单里的 `^name$`。
    """
    item = {
        "product_name": entry.get("name"),
        "appear_orders": entry.get("appear_orders"),
        "standalone_orders": entry.get("standalone_orders"),
        "combo_orders": entry.get("combo_orders"),
        "category": entry.get("category"),
    }
    if entry.get("attrs"):
        item["attrs"] = entry["attrs"]
    return item


def _facts_summary(facts):
    """把 facts 压成能进画像、能进个人胃文件的最小摘要。"""
    time_block = facts.get("time") or {}
    amount = facts.get("amount") or {}
    structure = facts.get("structure") or {}
    groups = facts.get("by_category") or {}
    return {
        "as_of": time_block.get("as_of"),
        "window": {
            "earliest": time_block.get("earliest"),
            "latest": time_block.get("latest"),
            "latest_personal": time_block.get("latest_personal"),
            "window_span_days": time_block.get("window_span_days"),
            "days_since_last_visible": time_block.get("days_since_last_visible"),
            "days_since_last_personal": time_block.get("days_since_last_personal"),
            "recency_state": time_block.get("recency_state"),
            "parsed": time_block.get("parsed"),
            "unparsed": time_block.get("unparsed"),
            "future": time_block.get("future"),
            "max_returnable": time_block.get("max_returnable"),
            "claims": list(time_block.get("claims") or []),
            "cautions": list(time_block.get("cautions") or []),
        },
        "amount": {
            "unit": amount.get("unit"),
            "unit_source": amount.get("unit_source"),
            "included_orders": amount.get("included_orders"),
            "with_amount": amount.get("with_amount"),
            "missing_rate": amount.get("missing_rate"),
            "total": amount.get("total"),
            "mean": amount.get("mean"),
            "median": amount.get("median"),
            "min": amount.get("min"),
            "max": amount.get("max"),
            "range": amount.get("range"),
            "dispersion": amount.get("dispersion"),
            "stability": amount.get("stability"),
            "stability_basis": amount.get("stability_basis"),
            "shared_orders_with_amount": amount.get("shared_orders_with_amount"),
            "mixed_unit_warning": amount.get("mixed_unit_warning"),
            "notes": list(amount.get("notes") or []),
        },
        "structure": {
            "basis": structure.get("basis"),
            "combo_only": structure.get("combo_only"),
            "standalone_only": structure.get("standalone_only"),
            "mixed": structure.get("mixed"),
            "unknown": structure.get("unknown"),
            "scope": structure.get("scope"),
        },
        "by_category": {
            key: [_brief_entry(e) for e in (value or [])[:4]]
            for key, value in groups.items()
        },
        "novelty": facts.get("novelty") or {},
    }


def _observations(facts):
    """本次观察：低样本时也要把**真实餐品细节**带进个人胃。"""
    out = []
    for entry in (facts["stats"]["product_freq"] or [])[:6]:
        if not entry.get("name"):
            continue
        out.append(_brief_entry(entry))
    return out


# ------------------------------------------------------------------ v2 叙事层

def _discoveries(facts, rules):
    """有几条给几条的发现。**每一条都必须指回真实事实，不凑数。**"""
    personal = facts["counts"]["personal_valid_orders"]
    stats = facts["stats"]
    freq = stats["product_freq"]
    groups = facts.get("by_category") or {}
    structure = facts.get("structure") or {}
    amount = facts.get("amount") or {}
    time_block = facts.get("time") or {}
    out = []

    def rule_ids(match_name=None, dimension=None):
        ids = []
        for rule in rules:
            if match_name and match_name in str(rule.get("text", "")):
                ids.append(rule["id"])
            elif dimension and rule.get("dimension") == dimension:
                ids.append(rule["id"])
        return ids

    # 1) 主食多样性
    if stats["distinct_staple"] >= 2:
        out.append({
            "dimension": "staple",
            "text": f"主食换过 {stats['distinct_staple']} 种（{'、'.join(stats['staple_names'])}）",
            "evidence": f"{personal} 笔可归于本人的订单里的主食分布",
            "evidence_ids": rule_ids(dimension="staple"),
            "confidence": "high",
        })

    # 2) 固定搭配（同频两道）
    ties = [e for e in freq if e["appear_orders"] >= personal] if personal else []
    if len(ties) >= 2:
        a, b = ties[0], ties[1]
        out.append({
            "dimension": "combo",
            "text": (
                f"【{a['name']}】和【{b['name']}】在 {personal} 笔里都出现 "
                f"{a['appear_orders']} 次——同进同出"
            ),
            "evidence": "两道餐品出现笔数相同，接近每单都有",
            "evidence_ids": rule_ids(match_name=a["name"]),
            "confidence": "high",
        })

    # 3) 饮料固定（具体饮料，不合并糖分与冰热）
    drinks = groups.get("drink") or []
    if drinks and drinks[0]["appear_orders"] >= max(2, personal - 1):
        top_drink = drinks[0]
        attr_bits = []
        attrs = top_drink.get("attrs") or {}
        if attrs.get("temperature") == "hot":
            attr_bits.append("常是热的")
        elif attrs.get("temperature") == "iced":
            attr_bits.append("常是冰的")
        if attrs.get("sugar") == "zero_sugar":
            attr_bits.append("且是无糖")
        suffix = ("（" + "、".join(attr_bits) + "）") if attr_bits else ""
        out.append({
            "dimension": "drink",
            "text": (
                f"饮料几乎固定：【{top_drink['name']}】"
                f"{personal} 笔里出现 {top_drink['appear_orders']} 次{suffix}"
            ),
            "evidence": "按具体饮料计数，未把无糖与含糖、冰与热合并",
            "evidence_ids": rule_ids(match_name=top_drink["name"], dimension="drink"),
            "confidence": "high" if top_drink["appear_orders"] >= 3 else "medium",
        })

    # 4) 配餐常驻
    sides = groups.get("side") or []
    if sides and sides[0]["appear_orders"] >= 3:
        top_side = sides[0]
        out.append({
            "dimension": "side",
            "text": (
                f"配餐里【{top_side['name']}】出现 {top_side['appear_orders']} 次"
                f"（其中 {top_side['combo_orders']} 次是套餐自带）"
            ),
            "evidence": "区分独立加点与套餐内含",
            "evidence_ids": rule_ids(match_name=top_side["name"]),
            "confidence": "high" if top_side["standalone_orders"] >= 2 else "medium",
        })

    # 5) 套餐结构
    if structure.get("combo_only") or structure.get("standalone_only") or structure.get("mixed"):
        parts = []
        for key, label in (("combo_only", "只点套餐"), ("standalone_only", "只单点"),
                           ("mixed", "套餐加加点")):
            value = structure.get(key) or 0
            if value:
                parts.append(f"{label} {value} 笔")
        out.append({
            "dimension": "session",
            "text": "下单结构：" + "、".join(parts),
            "evidence": "订单级四类互斥统计，酱料/加料不算套餐",
            "evidence_ids": [],
            "confidence": "high",
        })

    # 6) 时段
    segment_freq = stats["segment_freq"]
    for segment, label in (("breakfast", "早餐窗口"), ("night", "夜间窗口")):
        count = segment_freq.get(segment, 0)
        if count >= 3:
            out.append({
                "dimension": "breakfast" if segment == "breakfast" else "session",
                "text": f"{count} 笔落在{label}",
                "evidence": f"{personal} 笔里的时段分布",
                "evidence_ids": rule_ids(dimension="breakfast"),
                "confidence": "medium",
            })
            break

    # 7) 时间：久未下单 / 窗口跨度（只在日期可靠时讲）
    #    注意维度要用 recency / window，不能复用 session——
    #    否则会被上面「下单结构」那条占掉名额，档案变旧这件事就说不出口了。
    days_since = time_block.get("days_since_last_visible")
    if days_since is not None and time_block.get("recency_state") == "stale":
        out.append({
            "dimension": "recency",
            "text": f"最近一笔可见下单距今 {days_since} 天，这份口味档案有点旧了",
            "evidence": "按可解析日期计算；不外推成全渠道没吃过",
            "evidence_ids": [],
            "confidence": "high",
        })
    elif time_block.get("window_span_days") and personal >= 3:
        out.append({
            "dimension": "window",
            "text": (
                f"这批记录横跨 {time_block['window_span_days']} 天，"
                f"共 {personal} 笔可归于本人"
            ),
            "evidence": "可见窗口内的跨度，不外推月频或年频",
            "evidence_ids": [],
            "confidence": "high",
        })

    # 8) 金额：**只在单位核实后才讲**，且只讲「排得整齐」，不讲消费能力
    if amount.get("unit_source") == "caller" and amount.get("stability") == "stable":
        out.append({
            "dimension": "budget",
            "text": "这几张小票的金额排得挺整齐",
            "evidence": (
                f"{amount['with_amount']} 笔有金额的个人有效单，"
                f"变异系数 {amount['dispersion']['value']}（≤0.15）"
            ),
            "evidence_ids": [],
            "confidence": "medium",
        })

    # 去重（同维度只留第一条），最多 5 条
    seen = set()
    unique = []
    for item in out:
        if item["dimension"] in seen:
            continue
        seen.add(item["dimension"])
        unique.append(item)
    return unique[:5]


def _secondary_trait(naming, facts):
    """次特点：**必须是另一个维度**，且真的与主身份有张力。没有就不给。"""
    primary_counts = naming.get("basis_counts") or []
    primary_category = primary_counts[0].get("category") if primary_counts else None
    stats = facts["stats"]
    groups = facts.get("by_category") or {}
    personal = facts["counts"]["personal_valid_orders"]

    drinks = groups.get("drink") or []
    sides = groups.get("side") or []

    # 主身份偏主食/套餐 -> 看饮料是否反而固定
    if primary_category in ("staple", "combo"):
        if drinks and drinks[0]["appear_orders"] >= max(2, personal - 1):
            return {
                "dimension": "drink",
                "text": (
                    f"主食这边换着来，饮料却很专一——"
                    f"【{drinks[0]['name']}】{personal} 笔里出现 {drinks[0]['appear_orders']} 次"
                ),
                "evidence": "主食多样性与饮料频次来自同一批个人有效单",
            }
        if sides and sides[0]["appear_orders"] >= 3:
            return {
                "dimension": "side",
                "text": f"主食之外，【{sides[0]['name']}】几乎没缺席",
                "evidence": f"配餐出现 {sides[0]['appear_orders']} 次",
            }
        return None

    # 主身份偏饮料 -> 看主食是否反而在换
    if primary_category == "drink":
        if stats["distinct_staple"] >= 2:
            return {
                "dimension": "staple",
                "text": (
                    f"饮料定得住，主食却换过 {stats['distinct_staple']} 种"
                    f"（{'、'.join(stats['staple_names'])}）"
                ),
                "evidence": "两件事来自同一批订单，互不矛盾",
            }
        return None

    # 主身份是组合/固定搭档 -> 看时段
    if primary_category is None and naming.get("primary") == "固定搭档":
        segment_freq = stats["segment_freq"]
        for segment, label in (("breakfast", "早餐窗口"), ("night", "夜间窗口")):
            count = segment_freq.get(segment, 0)
            if count >= 3:
                return {
                    "dimension": "session",
                    "text": f"这对搭档基本出现在{label}（{count} 笔）",
                    "evidence": "时段来自同一批个人有效单",
                }
        return None

    return None


def _getting_along(facts):
    """胃的相处说明：哪些可换、哪些别动、下次怎么问它。

    **低样本只做本次描述。** 1 笔既不能证明「主食基本不换」，也不能证明「饮料会换」，
    更推不出「习惯点套餐」——那是把一个类别数当成了习惯频率。
    「习惯 / 会换 / 别动」这类长期断言至少要 3 笔可归于本人的订单，
    或者由**本人明确自述**确认后另行写入。
    """
    stats = facts["stats"]
    structure = facts.get("structure") or {}
    personal = facts["counts"]["personal_valid_orders"]
    lines = []

    if personal == 0:
        return lines

    if personal < 3:
        names = [e["name"] for e in stats["product_freq"] if e["name"]]
        if names:
            listing = "、".join(f"【{n}】" for n in names[:4])
            if personal == 1:
                lines.append(f"这一次点的是：{listing}。")
            else:
                lines.append(f"这 {personal} 笔里出现过的是：{listing}。")
        lines.append(
            f"样本只有 {personal} 笔——什么该留、什么可以调整，现在还谈不上；"
            "攒到 3 笔以上，我才敢说「习惯」这种话。"
        )
        return lines

    full_attendance = [
        e for e in stats["product_freq"]
        if e["appear_orders"] >= personal and personal >= 2 and e["name"]
    ]
    if full_attendance:
        lines.append(
            "每次都出现的：" + "、".join(f"【{e['name']}】" for e in full_attendance[:3])
            + "——改动它风险最高。"
        )
    if stats["distinct_staple"] >= 2:
        lines.append(
            f"主食换着来（{stats['distinct_staple']} 种），这一块可以动。"
        )
    elif stats["staple_names"]:
        lines.append("主食基本不换，这一块建议别动。")

    drinks = (facts.get("by_category") or {}).get("drink") or []
    if drinks and drinks[0]["appear_orders"] >= max(2, personal - 1):
        lines.append(f"饮料认准【{drinks[0]['name']}】，换别的容易被嫌弃。")
    elif drinks:
        lines.append("饮料会换，可以拿来当变动项。")

    if structure.get("combo_only"):
        lines.append("习惯点套餐，直接给整套比拆开单点更顺手。")
    elif structure.get("standalone_only"):
        lines.append("习惯单点，别默认推套餐。")
    elif structure.get("mixed"):
        lines.append("套餐和加点混着点，可以先报套餐再问要不要加。")
    return lines


def _next_order_rule(facts, rules):
    """下一次点单规则：一句话，可执行。"""
    stats = facts["stats"]
    personal = facts["counts"]["personal_valid_orders"]
    freq = stats["product_freq"]
    groups = facts.get("by_category") or {}
    drinks = groups.get("drink") or []

    anchor = None
    if freq:
        anchor = freq[0]["name"]
    if not anchor:
        return "下次先问我想吃什么，别替我决定。"

    spare = ""
    if drinks and drinks[0]["name"] != anchor and drinks[0]["appear_orders"] >= 2:
        spare = f"饮料照旧配【{drinks[0]['name']}】。"
    elif stats["distinct_staple"] >= 2:
        other = [n for n in stats["staple_names"] if n != anchor]
        if other:
            spare = f"想换口味就从【{other[0]}】里挑一个。"
    if not spare:
        spare = "想换口味时，再给我一个备选。"
    return f"先看【{anchor}】；{spare}（依据：{personal} 笔可归于本人的订单）"


def _narrative(facts, rules, naming, statement, model=None):
    """叙事块：主身份＋次特点＋发现＋相处说明＋下次规则。

    `model` 传入模型写的文本时走 `apply_narrative` 的写回路径；
    这里只负责**代码产候选**，不把趣味文本锁死成模板。
    """
    discoveries = _discoveries(facts, rules)
    secondary = _secondary_trait(naming, facts)
    narrative = {
        "source": "code",
        "primary_identity": {
            "display_name": naming.get("primary"),
            "statement": statement.get("text"),
            "evidence_ids": list(naming.get("evidence_ids") or []),
        },
        "secondary_trait": secondary,
        "discoveries": discoveries,
        "getting_along": _getting_along(facts),
        "next_order_rule": _next_order_rule(facts, rules),
        "counts": {
            "discoveries": len(discoveries),
            "has_secondary": bool(secondary),
        },
        "note": (
            "候选由脚本按真实事实生成；模型可以另写更贴切的外号与说法，"
            "写回后 `source` 会变成 model/user，并必须指回同一批证据。"
        ),
    }
    return narrative


#: `narrative` 命令**支持**写回的键。其余键一律整份拒绝，不静默吞掉。
#:
#: `evidence_line` 是唯一允许写回的非叙事键：它只改「依据怎么说」，
#: 不改任何计数。当订单里同时有主食多样、饮料常驻这类已核聚合时，
#: 「每一笔都落在早餐时段」这种只讲一半的说法可以被换成完整口径
#: （例：`3 笔单人早餐 · 3 款不同主食 · 都有咖啡`）。
#: 约束：写回的文字**只能复述画像里已经存在的聚合**，不得引入新的数字或新事实。
WRITABLE_NARRATIVE_KEYS = (
    "source", "primary", "statement", "story", "evidence_ids",
    "discoveries", "secondary_trait", "getting_along", "next_order_rule",
    "evidence_line",
)

#: 发现条数上限（有几条给几条，最多这个数）。
MAX_DISCOVERIES = 5


def _clean_text(value, field, summary):
    """清一遍自由文本；命中指令注入就拒绝。返回 (text|None)。"""
    cleaned, _hits, injection = privacy.sanitize_user_text(str(value).strip())
    if injection:
        summary["rejected"].append(f"{field} 命中指令注入，已拒绝写回")
        return None
    return cleaned


def _build_discoveries(raw, known_ids):
    """校验并构造写回的 discoveries。**任何一条不合法就整份拒绝**。

    返回 (entries, error)；error 非空表示拒绝，调用方不得改动画像。
    """
    if not isinstance(raw, list):
        return None, "narrative.discoveries 必须是数组"
    if len(raw) > MAX_DISCOVERIES:
        return None, f"narrative.discoveries 最多 {MAX_DISCOVERIES} 条（有几条给几条）"
    built = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"narrative.discoveries[{index}] 不是对象"
        text = str(item.get("text") or "").strip()
        if not text:
            return None, f"narrative.discoveries[{index}] 缺少 text"
        ids = item.get("evidence_ids") or []
        if not isinstance(ids, list):
            return None, f"narrative.discoveries[{index}].evidence_ids 必须是数组"
        for rid in ids:
            if rid not in known_ids:
                return None, (
                    f"narrative.discoveries[{index}] 引用了不存在的规则 id：{rid}"
                )
        evidence = str(item.get("evidence") or "").strip()
        if not ids and not evidence:
            return None, (
                f"narrative.discoveries[{index}] 缺少 evidence（每条发现必须有依据，"
                "给 evidence_ids 或写明 evidence）"
            )
        cleaned, _hits, injection = privacy.sanitize_user_text(text)
        if injection:
            return None, f"narrative.discoveries[{index}].text 命中指令注入，已拒绝写回"
        built.append({
            "dimension": str(item.get("dimension") or "custom").strip() or "custom",
            "text": cleaned,
            "evidence": evidence or "由本人/模型提供，evidence_ids 见本条目",
            "evidence_ids": list(ids),
            "confidence": str(item.get("confidence") or "user_stated"),
        })
    return built, None


def apply_narrative(profile, narrative):
    """把模型（或本人）生成的昵称/宣言/故事/发现/相处说明写回画像。

    硬约束：
    - **不支持的键 → 整份拒绝**（返回 error），不静默吞掉；
    - 引用的规则 ID 必须真实存在，悬空 → 整份拒绝；
    - 事实不动：只换说法与组织方式，计数与证据集合不变。
    - `evidence_line` 只许复述画像里已有的聚合，**不许引入新的数字**。
    """
    if not isinstance(profile, dict) or not isinstance(narrative, dict):
        return profile, {"error": "profile 或 narrative 不是对象"}

    unknown = sorted(set(narrative) - set(WRITABLE_NARRATIVE_KEYS))
    if unknown:
        return profile, {
            "error": "narrative 含不支持的键，已整份拒绝（不静默吞掉）："
                     + "、".join(unknown)
                     + "；支持的键：" + "、".join(WRITABLE_NARRATIVE_KEYS),
        }

    known_ids = {rule.get("id") for rule in profile.get("rules", [])}
    naming = dict(profile.get("naming") or {})
    statement = dict(profile.get("self_statement") or {})
    narrative_block = dict(profile.get("narrative") or {})
    summary = {"applied": [], "rejected": []}

    def _ids_ok(ids):
        return all(rid in known_ids for rid in (ids or []))

    requested = narrative.get("evidence_ids")
    if requested is None:
        requested = list(naming.get("evidence_ids") or [])
    if not _ids_ok(requested):
        return profile, {"error": "narrative.evidence_ids 引用了不存在的规则 id"}

    source = narrative.get("source") or "model"
    if source not in schema.NARRATIVE_SOURCES:
        return profile, {"error": f"narrative.source 非法：{source!r}"}

    # --- 发现：先整份校验，任何一条不合法都不动画像 ---
    new_discoveries = None
    if "discoveries" in narrative:
        new_discoveries, error = _build_discoveries(narrative.get("discoveries"), known_ids)
        if error:
            return profile, {"error": error}

    new_primary = (narrative.get("primary") or "").strip()
    if new_primary:
        old = naming.get("primary")
        candidates = list(naming.get("candidates") or [])
        if old and old not in candidates:
            candidates.insert(0, old)
        if new_primary not in candidates:
            candidates.append(new_primary)
        naming.update({
            "primary": new_primary,
            "candidates": candidates,
            "source": "user_claimed" if source == "user" else naming.get("source") or "user_stated",
            "locked": source == "user",
            "evidence_ids": list(requested),
            "basis_rule_ids": list(requested),
        })
        summary["applied"].append(f"naming.primary -> {new_primary}")

    new_statement = (narrative.get("statement") or "").strip()
    if new_statement:
        cleaned = _clean_text(new_statement, "statement", summary)
        if cleaned is not None:
            statement.update({
                "text": cleaned,
                "evidence_ids": list(requested),
                "basis_rule_ids": list(requested),
            })
            summary["applied"].append("self_statement.text")

    story = (narrative.get("story") or "").strip()
    if story:
        cleaned = _clean_text(story, "story", summary)
        if cleaned is not None:
            narrative_block["story"] = cleaned
            summary["applied"].append("narrative.story")

    raw_secondary = narrative.get("secondary_trait")
    if raw_secondary is not None:
        if not isinstance(raw_secondary, dict) or not str(raw_secondary.get("text") or "").strip():
            return profile, {"error": "narrative.secondary_trait 必须是含 text 的对象"}
        cleaned = _clean_text(raw_secondary["text"], "secondary_trait.text", summary)
        if cleaned is None:
            return profile, {"error": "narrative.secondary_trait.text 命中指令注入"}
        narrative_block["secondary_trait"] = {
            "dimension": str(raw_secondary.get("dimension") or "custom"),
            "text": cleaned,
            "evidence": str(raw_secondary.get("evidence") or "由本人/模型提供"),
        }
        summary["applied"].append("narrative.secondary_trait")

    raw_getting_along = narrative.get("getting_along")
    if raw_getting_along is not None:
        if isinstance(raw_getting_along, str):
            raw_getting_along = [raw_getting_along]
        if not isinstance(raw_getting_along, list):
            return profile, {"error": "narrative.getting_along 必须是字符串或字符串数组"}
        lines = []
        for line in raw_getting_along:
            cleaned = _clean_text(line, "getting_along", summary)
            if cleaned is None:
                return profile, {"error": "narrative.getting_along 命中指令注入"}
            if cleaned:
                lines.append(cleaned)
        narrative_block["getting_along"] = lines
        summary["applied"].append(f"narrative.getting_along（{len(lines)} 条）")

    raw_next_rule = narrative.get("next_order_rule")
    if raw_next_rule is not None:
        cleaned = _clean_text(raw_next_rule, "next_order_rule", summary)
        if cleaned is None:
            return profile, {"error": "narrative.next_order_rule 命中指令注入"}
        if not cleaned:
            return profile, {"error": "narrative.next_order_rule 不能为空"}
        narrative_block["next_order_rule"] = cleaned
        summary["applied"].append("narrative.next_order_rule")

    if new_discoveries is not None:
        narrative_block["discoveries"] = new_discoveries
        summary["applied"].append(f"narrative.discoveries（{len(new_discoveries)} 条）")

    narrative_block["source"] = source
    if naming.get("primary"):
        narrative_block["primary_identity"] = {
            "display_name": naming.get("primary"),
            "statement": statement.get("text"),
            "evidence_ids": list(requested),
        }
    narrative_block["counts"] = {
        "discoveries": len(narrative_block.get("discoveries") or []),
        "has_secondary": bool(narrative_block.get("secondary_trait")),
    }

    profile["naming"] = naming
    profile["self_statement"] = statement
    profile["narrative"] = narrative_block

    # `evidence_line` 写回：只换依据的说法，**不动任何计数**。
    # 放在最后统一落盘，避免中途出错留下半截证书。
    raw_evidence_line = narrative.get("evidence_line")
    if raw_evidence_line is not None:
        cleaned = _clean_text(raw_evidence_line, "evidence_line", summary)
        if cleaned is None:
            return profile, {"error": "narrative.evidence_line 命中指令注入"}
        if not cleaned:
            return profile, {"error": "narrative.evidence_line 不能为空"}
        certificate = dict(profile.get("certificate") or {})
        certificate["evidence_line"] = cleaned
        profile["certificate"] = certificate
        summary["applied"].append("certificate.evidence_line")

    if profile.get("certificate"):
        profile["certificate"]["declaration"] = statement.get("text") or ""
    return profile, summary


def distill(facts, answers=None, points=None, owner_label="我", now=None):
    """生成 profile.json 的 dict。"""
    answers = answers or []
    moment = _now(now)
    stamp = _iso(moment)
    counts = dict(facts["counts"])
    stats = facts["stats"]

    order_rules = _recurring_rules(facts) + _pattern_rules(facts)
    stated_rules = _answer_rules(answers)
    rules = order_rules + stated_rules

    tier = stats["tier"]
    if counts["raw_orders"] <= 0:
        tier = "snapshot_only"

    naming = _order_naming(facts)
    if tier != "type_ready" or not naming["primary"]:
        stated = _stated_naming(answers, stated_rules)
        if stated["primary"]:
            naming = stated
    if tier != "type_ready" and naming.get("source") == "orders":
        # 样本不足不得用订单命名
        naming = _stated_naming(answers, stated_rules)

    # 悬空引用防线：命名引用的规则 ID 必须真的存在，否则不给名字
    existing_ids = {rule["id"] for rule in rules}
    referenced = list(naming.get("evidence_ids") or [])
    if naming.get("primary") and (
        not referenced or any(rid not in existing_ids for rid in referenced)
    ):
        naming = _stated_naming(answers, stated_rules)
        referenced = list(naming.get("evidence_ids") or [])
        if naming.get("primary") and (
            not referenced or any(rid not in existing_ids for rid in referenced)
        ):
            naming = _empty_naming()

    statement = _self_statement(naming, facts, rules)
    unresolved = _unresolved(facts) + _conflict_questions(facts, answers)
    unresolved = unresolved[:MAX_QUESTIONS]

    facts_summary = _facts_summary(facts)
    observations = _observations(facts)
    narrative = _narrative(facts, rules, naming, statement)
    source_state = facts.get("source_state") or "normal"

    state, label = schema.level_for_points(_points_value(points))

    top = stats["product_freq"][0] if stats["product_freq"] else None
    binding = naming.get("basis_counts") or []
    if len(binding) >= 2:
        evidence_line = (
            f"{binding[0]['personal_orders']} 笔可归于本人的订单里"
            f"【{binding[0]['product_name']}】出现 {binding[0]['appear_orders']} 次、"
            f"【{binding[1]['product_name']}】出现 {binding[1]['appear_orders']} 次"
        )
    elif binding:
        if binding[0].get("category") == "breakfast":
            # 时段型依据：写成「每一笔都落在早餐时段」，比「【早餐时段】出现 N 次」更像人话。
            evidence_line = (
                f"{binding[0]['personal_orders']} 笔可归于本人的订单里，"
                "每一笔都落在早餐时段"
            )
        else:
            evidence_line = (
                f"{binding[0]['personal_orders']} 笔可归于本人的订单里"
                f"【{binding[0]['product_name']}】出现 {binding[0]['appear_orders']} 次"
            )
    elif top and counts["personal_valid_orders"]:
        evidence_line = (
            f"{counts['personal_valid_orders']} 笔可归于本人的订单里"
            f"【{top['name']}】出现 {top['appear_orders']} 次"
        )
    elif naming.get("primary"):
        evidence_line = "依据你的自述，尚无订单计数可展示"
    else:
        evidence_line = "尚无足够的订单计数可展示"

    if counts["raw_orders"] > 0:
        dropped = []
        if counts["excluded_canceled"]:
            dropped.append(f"{counts['excluded_canceled']} 笔已取消")
        if counts["status_unconfirmed"]:
            dropped.append(f"{counts['status_unconfirmed']} 笔状态未知")
        if counts["group_suspected_pending"]:
            dropped.append(f"{counts['group_suspected_pending']} 笔疑似代点")
        if dropped:
            source_range = (
                f"最近 {counts['raw_orders']} 笔订单（其中 "
                + "、".join(dropped) + "，均未计入）"
            )
        else:
            breakfast = stats["segment_freq"].get("breakfast", 0)
            personal_n = counts["personal_valid_orders"]
            if personal_n and breakfast == personal_n:
                # 全部落在早餐窗口：来源就说清楚是「本次可见的 N 笔个人早餐」，
                # 不写成含糊的「最近 N 笔订单」。
                source_range = f"仅基于本次可见的 {personal_n} 笔个人早餐"
            else:
                source_range = f"最近 {counts['raw_orders']} 笔订单"
    elif source_state == "fetch_failed":
        # 故障不能写成「你没点过」。
        source_range = "本次接口读取失败，未拿到订单"
    elif source_state == "not_connected":
        source_range = "本次没有连接账号，未读到订单"
    elif answers:
        # 确有其人自述，但这次确实没读到订单——两件事分开说。
        source_range = "仅你的自述（本次未读到订单）"
    else:
        # 0 单且没有任何自述：只说接口这次没给到订单，不替用户发言。
        source_range = "本次接口未返回订单"

    profile = {
        "schema": schema.SCHEMA_ID,
        "profile_id": _profile_id(counts, rules, stamp),
        "owner_label": owner_label,
        "created_at": stamp,
        "updated_at": stamp,
        "roles": _roles(answers),
        "source": {
            "kind": _source_kind(counts, answers),
            "valid_orders": counts["valid_orders"],
            "personal_valid_orders": counts["personal_valid_orders"],
            "excluded_canceled": counts["excluded_canceled"],
            "status_unconfirmed": counts["status_unconfirmed"],
            "group_suspected_pending": counts["group_suspected_pending"],
            "pending_confirmation": counts["pending_confirmation"],
            "max_returnable": counts["max_returnable"],
            "note": "order-list 最多返回最近 10 笔，非全量；本 profile 不含任何原始订单明细",
        },
        "sample": {
            "tier": tier,
            "source_state": source_state,
            "source_state_label": schema.SOURCE_STATE_LABELS.get(source_state, ""),
            "stale_archive": source_state == "stale",
            "repeated_pattern": bool(stats["repeated_pattern"]) and counts["raw_orders"] > 0,
        },
        "facts": facts_summary,
        "observations": observations,
        "narrative": narrative,
        "source_state": source_state,
        "rules": rules,
        "unresolved": unresolved,
        "naming": naming,
        "self_statement": statement,
        "level": {
            "state": state,
            "label": label,
            "policy": schema.LEVEL_POLICY,
            "source_field": "query-my-account.accumulativePoint",
            "public_counting": False,
            "formula": "见 references/portfolio-schema.md §3（阈值公开可复算）",
        },
        "certificate": {
            "declaration": statement["text"] or "",
            "evidence_line": evidence_line,
            "source_range": source_range,
            "challenge": schema.CHALLENGE,
            "how_to_use": schema.HOW_TO_USE,
        },
        "sharing": {
            "public_counting": False,
            "whitelist_version": schema.WHITELIST_VERSION,
        },
        "asked_question_ids": list({
            a.get("question_id") for a in answers if a.get("question_id")
        }),
        "notes": list(facts.get("notes", [])),
    }
    return profile
