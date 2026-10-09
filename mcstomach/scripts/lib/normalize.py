"""订单归一 v2：把 order-list 的原始返回变成可核对的计数事实。

只做确定性的事：解析、分类、计数、标记可疑项。
**不做偏好结论**（那是 distill 的事），**不写入任何原始订单号/门店/坐标到导出物**。

v2 新增（对应 08 号任务的功能要求 1–4）：

- **时间事实**：解析完整日期、as_of、可见窗口跨度、相邻间隔、距最近一次下单天数；
  解析不了 / 未来时间 / 过期时间都**不冒充正常**，且明确 `order-list` 只返回最近 10 笔。
- **金额事实**：保留单位；总额/均单/中位数/区间**只算已确认的个人有效单**；
  共享单**单列整单金额**；单位不明时**不给任何金额结论**；
  稳定性改用**真正无量纲**的变异系数，且 `max-min<=10` 那种伪口径已被删除。
- **套餐结构**：订单级四类**互斥**统计（仅套餐 / 仅单点 / 混合 / 未知）；
  酱料、加料**不算套餐**，也不参与多样性。
- **分类明细**：主食 / 配餐 / 饮料 / 甜点 / 套餐 / 调味 分开计数；
  饮料保留温度与糖分属性，**不把无糖和含糖、冰和热糊在一起**。
- **新品留口**：没有可靠上市目录时 `novelty.claimable = False`，只能讲可见样本内的变化与回购。

三条硬口径保持不变：
1. **取消单剔除**；
2. **状态未知的单不进偏好统计**（降权不能只是注释）；
3. **套餐父项不算主食**，同名归一后与单点合并计数。
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from . import lexicon, schema

CN_TZ = timezone(timedelta(hours=8))

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

CANCEL_MARKERS = ("取消", "已退", "退款")
COMPLETE_MARKERS = ("完成", "已送达", "已取餐")

#: 团体单 / 代点启发式阈值（会误判，所以只是降权+追问，不是剔除）
GROUP_SINGLE_SKU_QTY = 3
GROUP_DISTINCT_ITEMS = 5
GROUP_TOTAL_QTY = 6

#: 距最近一次可见下单超过这么多天，算「久未下单」，走旧口味档案。
STALE_AFTER_DAYS = 90

#: 可见窗口内至少这么多笔才谈稳定性（无量纲变异系数）。
MIN_AMOUNT_SAMPLES = 3
#: 变异系数阈值：≤ 0.15 视为「排得比较整齐」。
CV_STABLE_MAX = 0.15

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y%m%d%H%M%S", "%Y%m%d",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d",
)

#: 这些字段若同现，说明金额可能以「分」计价——绝不与 realTotalAmount 混算。
_CENT_FIELDS = ("calculatePrice", "totalAmountCent", "payAmountCent", "amountCent")

#: 金额单位别名 → 规范单位。
#: 「分」在人民币语境下是确定口径，可以精确换算；**认不出来的单位一律不猜、不换**。
_UNIT_ALIASES = {
    "元": "CNY", "人民币": "CNY", "人民币元": "CNY", "rmb": "CNY", "cny": "CNY",
    "yuan": "CNY", "¥": "CNY", "￥": "CNY",
    "分": "CNY_CENT", "人民币分": "CNY_CENT", "cent": "CNY_CENT",
    "cents": "CNY_CENT", "fen": "CNY_CENT", "cny_cent": "CNY_CENT",
    "cent_cny": "CNY_CENT",
}
_UNIT_LABELS = {"CNY": "元", "CNY_CENT": "分"}


# ------------------------------------------------------------------ 基础解析

def _extract_list(raw):
    """从各种形态的输入里取出订单列表。取不到返回空列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, dict) and isinstance(data.get("list"), list):
            return data["list"]
        if isinstance(raw.get("list"), list):
            return raw["list"]
        if isinstance(data, list):
            return data
    return []


def _status_class(status_raw):
    if not status_raw or not str(status_raw).strip():
        return "unconfirmed"
    text = str(status_raw)
    for marker in CANCEL_MARKERS:
        if marker in text:
            return "canceled"
    for marker in COMPLETE_MARKERS:
        if marker in text:
            return "completed"
    return "unconfirmed"


def parse_when(value):
    """解析一个时间字段。返回 (datetime|None, state)。

    state ∈ {"parsed", "unparsed", "invalid"}；未来时间由调用方另行判定。
    **解析不出来不猜**：返回 None 并如实标 unparsed / invalid。
    """
    if value is None or value == "":
        return None, "unparsed"
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None, "unparsed"
        seconds = number / 1000.0 if number > 1e11 else number
        try:
            return datetime.fromtimestamp(seconds, CN_TZ), "parsed"
        except (OverflowError, OSError, ValueError):
            return None, "invalid"
    text = str(value).strip()
    if not text:
        return None, "unparsed"
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=CN_TZ)
        return moment.astimezone(CN_TZ), "parsed"
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ), "parsed"
        except ValueError:
            continue
    if not re.search(r"\d", text):
        return None, "unparsed"
    return None, "invalid"


def _hour_of(create_time):
    moment, state = parse_when(create_time)
    if state == "parsed" and moment is not None:
        return moment.hour + moment.minute / 60.0
    if not create_time or not isinstance(create_time, str):
        return None
    match = _TIME_RE.search(create_time)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23:
        return None
    return hour + minute / 60.0


def _to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_unit(unit):
    """把调用方给的单位规范化。认不出来就返回 None（**不猜**）。"""
    if unit is None:
        return None
    text = str(unit).strip()
    if not text or text.lower() == "unknown":
        return None
    return _UNIT_ALIASES.get(text.lower()) or _UNIT_ALIASES.get(text)


def _convert(value, canonical, target):
    """按 1 元 = 100 分精确换算。value 是**原始单位**下的数值。"""
    if value is None or canonical == target:
        return value
    if canonical == "CNY_CENT" and target == "CNY":
        return float((Decimal(str(value)) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP))
    return value


# ------------------------------------------------------------------ 餐品展开

def _expand_products(order):
    """展开一单的餐品。区分套餐内含（combo_included）与独立加点（standalone）。"""
    products = []
    entries = order.get("orderProductList")
    if not isinstance(entries, list):
        return products, False
    missing_name = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("productName")
        code = entry.get("productCode")
        if not name:
            missing_name = True
        combo_items = entry.get("comboItemList")
        has_combo = isinstance(combo_items, list) and bool(combo_items)
        # 套餐父项：名字里含套餐词，或结构上带 comboItemList。
        # 酱料/加料即使名字里有「拼」也不算套餐（v2 修正）。
        parent_is_combo = (has_combo or lexicon.is_combo_name(name)) \
            and not lexicon.is_condiment(name)
        products.append({
            "key": lexicon.merge_key(name) or code or "unknown",
            "name": name,
            "code": code,
            "source": "standalone",
            "quantity": entry.get("quantity"),
            "quantity_semantics": "clear",
            "is_combo_parent": parent_is_combo,
            "parent": None,
        })
        if has_combo:
            for child in combo_items:
                if not isinstance(child, dict):
                    continue
                child_name = child.get("name")
                if not child_name:
                    missing_name = True
                products.append({
                    "key": lexicon.merge_key(child_name) or child.get("productCode") or "unknown",
                    "name": child_name,
                    "code": child.get("productCode"),
                    "source": "combo_included",
                    "quantity": child.get("quantity"),
                    "quantity_semantics": "unclear",
                    "is_combo_parent": False,
                    "parent": name,
                })
    return products, missing_name


def _category_for(product):
    """套餐父项一律算套餐；酱料/加料算调味，不算主食。"""
    if product.get("is_combo_parent"):
        return "combo"
    name = product.get("name")
    if lexicon.is_condiment(name):
        return "condiment"
    if lexicon.is_combo_name(name):
        return "combo"
    return lexicon.classify(name)


def _structure_of(products):
    """订单级套餐结构。四类互斥：combo_only / standalone_only / mixed / unknown。

    **不用子项份数当订单比例**；酱料子项不算套餐。
    """
    if not products:
        return "unknown"
    tops = [p for p in products if p["source"] == "standalone"]
    if not tops:
        return "unknown"
    solid = [p for p in tops if _category_for(p) != "condiment"]
    if not solid:
        # 只有酱料/加料，判断不出这单到底吃了什么
        return "unknown"
    has_combo = any(p.get("is_combo_parent") for p in solid)
    others = [p for p in solid if not p.get("is_combo_parent")]
    if has_combo and not others:
        return "combo_only"
    if has_combo:
        return "mixed"
    return "standalone_only"


def _group_reasons(products):
    reasons = []
    top = [p for p in products if p["source"] == "standalone"]
    for item in top:
        quantity = _to_float(item.get("quantity"))
        if quantity is not None and quantity >= GROUP_SINGLE_SKU_QTY:
            reasons.append(f"single_sku_quantity>={GROUP_SINGLE_SKU_QTY}")
            break
    if len(top) >= GROUP_DISTINCT_ITEMS:
        reasons.append(f"distinct_items>={GROUP_DISTINCT_ITEMS}")
    total = sum(_to_float(p.get("quantity")) or 0 for p in top)
    if total >= GROUP_TOTAL_QTY:
        reasons.append(f"total_quantity>={GROUP_TOTAL_QTY}")
    codes = [p["code"] or p["key"] for p in top]
    if len(codes) != len(set(codes)):
        reasons.append("duplicate_top_level_entry")
    return reasons


# ------------------------------------------------------------------ 事实分块

def _time_block(items, as_of, max_returnable, future_boundary=None, boundary_basis=None):
    """时间事实。解析不了 / 未来 / 过期都不冒充正常。"""
    all_included = [i for i in items if i["included"]]
    parsed = [i for i in all_included if i["date_state"] == "parsed"]
    future = [i for i in all_included if i["date_state"] == "future"]
    unparsed = [i for i in all_included if i["date_state"] in ("unparsed", "invalid")]
    personal_parsed = [i for i in parsed if i["is_personal"]]

    moments = sorted(i["date_moment"] for i in parsed)
    earliest = moments[0] if moments else None
    latest = moments[-1] if moments else None
    span = (latest - earliest).days if earliest and latest else None

    intervals = [
        (b - a).days for a, b in zip(moments, moments[1:])
    ]
    intervals = [d for d in intervals if d >= 0]
    median_interval = None
    if intervals:
        ordered = sorted(intervals)
        mid = len(ordered) // 2
        median_interval = float(
            ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
        )

    def _days_since(moment):
        if moment is None:
            return None
        return (as_of.date() - moment.date()).days

    days_since_visible = _days_since(latest)
    latest_personal = max((i["date_moment"] for i in personal_parsed), default=None)
    days_since_personal = _days_since(latest_personal)

    if days_since_visible is None:
        recency = "unknown"
    elif days_since_visible > STALE_AFTER_DAYS:
        recency = "stale"
    else:
        recency = "fresh"

    within_window = None
    if len(moments) >= 2 and span and span > 0:
        within_window = {
            "orders": len(moments),
            "span_days": span,
            "orders_per_7d": round(len(moments) * 7.0 / span, 2),
            "scope": "可见窗口内",
            "extrapolated": False,
        }

    claims = []
    if earliest and latest:
        claims.append(f"这批记录横跨 {span} 天（{earliest.date()} 至 {latest.date()}）")
    if days_since_visible is not None:
        claims.append(f"最近一笔可见下单距今 {days_since_visible} 天")
    if within_window:
        claims.append(
            f"可见窗口内 {within_window['orders']} 笔 / {within_window['span_days']} 天"
            "（不外推到月频或年频）"
        )
    claims.append(f"最多只能返回最近 {max_returnable} 笔，不是全量记录")

    cautions = []
    if unparsed:
        cautions.append(f"{len(unparsed)} 笔时间无法解析，未参与跨度与间隔计算")
    if future:
        cautions.append(
            f"{len(future)} 笔时间晚于 as_of，按**未来记录隔离**："
            "不计入偏好统计、不进可见窗口，也不把天数抹成 0"
        )
    if future or unparsed:
        cautions.append("窗口不完整，不能当作完整观察期")
    cautions.append("本账号近期无记录 ≠ 这个人全渠道没吃过")

    return {
        "as_of": as_of.isoformat(timespec="seconds"),
        "future_boundary": (
            future_boundary.isoformat(timespec="seconds") if future_boundary else None
        ),
        "future_boundary_basis": boundary_basis,
        "future_boundary_note": (
            "as_of 只到「日」时，边界取当天 23:59:59——当天记录不算未来；"
            "as_of 带具体时刻时，边界就是那个时刻。"
            if boundary_basis else None
        ),
        "timezone_basis": "assumed_utc+8" if moments else "unknown",
        "max_returnable": max_returnable,
        "parsed": len(parsed),
        "unparsed": len(unparsed),
        "future": len(future),
        "earliest": earliest.date().isoformat() if earliest else None,
        "latest": latest.date().isoformat() if latest else None,
        "latest_personal": latest_personal.date().isoformat() if latest_personal else None,
        "window_span_days": span,
        "days_since_last_visible": days_since_visible,
        "days_since_last_personal": days_since_personal,
        "recency_state": recency,
        "intervals_days": intervals,
        "median_interval_days": median_interval,
        "within_window": within_window,
        "claims": claims,
        "cautions": cautions,
    }


def _amount_block(orders, personal_indexes, shared_indexes, unit=None):
    """金额事实。单位不明 → 只给计数，不给任何金额结论。

    单位口径（v2 修正）：

    - 调用方明确给「分」（人民币分）→ **精确换算成元**再展示（1 元 = 100 分），
      同时**保留原始单位与原始值**，并留下换算记录；
    - 给「元 / CNY / RMB」→ 直接使用，无需换算；
    - 给了单位但认不出来（例如非人民币）→ **不猜、不换算**，只保留原始值；
    - 完全没给单位 → 不给任何金额数值（原口径不变）；
    - 疑似「分」字段 → 单独列出，**不与 realTotalAmount 混算**。
    """
    canonical = _canonical_unit(unit)
    raw_unit = str(unit).strip() if unit is not None else ""
    # 给了单位但认不出来 → 仍然按调用方给的单位报数，**只是绝不换算**。
    unit_known = bool(raw_unit) and raw_unit.lower() != "unknown"

    personal_raw = []
    zero_orders = 0
    cent_fields_seen = []
    cent_field_values = []
    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            continue
        for field in _CENT_FIELDS:
            if field not in order:
                continue
            if field not in cent_fields_seen:
                cent_fields_seen.append(field)
            value = _to_float(order.get(field))
            if value is not None:
                cent_field_values.append(
                    {"order_index": index, "field": field, "value": value}
                )
        if index not in personal_indexes:
            continue
        value = _to_float(order.get("realTotalAmount"))
        if value is not None:
            personal_raw.append(value)
            if value == 0:
                zero_orders += 1

    shared_raw = []
    for index, order in enumerate(orders):
        if not isinstance(order, dict) or index not in shared_indexes:
            continue
        value = _to_float(order.get("realTotalAmount"))
        if value is not None:
            shared_raw.append(value)

    included = len(personal_indexes)
    with_amount = len(personal_raw)
    missing_orders = included - with_amount
    missing_rate = round(missing_orders / included, 3) if included else None

    # --- 单位换算：只在「认得出」且「需要换算」时执行，认不出来绝不猜 ---
    conversion_applied = unit_known and canonical == "CNY_CENT"
    if conversion_applied:
        display_values = [_convert(v, canonical, "CNY") for v in personal_raw]
        shared_values = [_convert(v, canonical, "CNY") for v in shared_raw]
        display_unit = "CNY"
    else:
        display_values = list(personal_raw)
        shared_values = list(shared_raw)
        display_unit = canonical or raw_unit or "unknown"

    total = mean = median = low = high = spread = dispersion = None
    stability = "unknown"
    if unit_known and with_amount >= 1:
        ordered = sorted(display_values)
        total = round(sum(ordered), 2)
        mean = round(sum(ordered) / with_amount, 2)
        mid = with_amount // 2
        median = ordered[mid] if with_amount % 2 else round((ordered[mid - 1] + ordered[mid]) / 2, 2)
        low, high = ordered[0], ordered[-1]
        spread = round(high - low, 2)
        if with_amount >= MIN_AMOUNT_SAMPLES and mean > 0:
            variance = sum((v - mean) ** 2 for v in ordered) / with_amount
            dispersion = round((variance ** 0.5) / mean, 4)
            stability = "stable" if dispersion <= CV_STABLE_MAX else "spread"

    conversion = {
        "applied": bool(conversion_applied),
        "from": _UNIT_LABELS.get(canonical, str(unit).strip()) if unit_known else None,
        "to": "元" if conversion_applied else None,
        "factor": 0.01 if conversion_applied else None,
        "original_values": list(personal_raw) if unit_known else [],
        "note": None,
    }
    if conversion_applied:
        conversion["note"] = (
            "按 1 元 = 100 分精确换算后展示；原始单位与原始值一并保留，可复算。"
        )
    elif unit_known and canonical == "CNY":
        conversion["note"] = "单位已是元，无需换算。"
    elif unit_known:
        conversion["note"] = (
            f"单位「{str(unit).strip()}」不在已知换算表内，**不猜、不换算**，只保留原始值。"
        )

    notes = []
    if not unit_known:
        notes.append("金额单位未核实，**不展示任何金额数值**，也不判断稳定或散")
    if conversion_applied:
        notes.append(
            f"输入单位为「{_UNIT_LABELS.get(canonical, canonical)}」，"
            "已按 1 元 = 100 分精确换算为元展示（原始值保留在 conversion 里）"
        )
    if unit_known and canonical is None:
        notes.append(
            f"单位「{raw_unit}」不在已知换算表内：**不猜、不换算**，只按该单位报数。"
        )
    if cent_fields_seen:
        notes.append(
            "检测到疑似以「分」计的字段（" + "、".join(cent_fields_seen) + "），"
            "已与 realTotalAmount 隔离，不混算"
        )
    if missing_rate:
        notes.append(
            f"{included} 笔个人有效单里 {with_amount} 笔有金额"
            f"（其中 0 元 {zero_orders} 笔、缺失 {missing_orders} 笔），缺失率 {missing_rate}"
        )
    if included > 0 and not shared_values:
        notes.append("金额只覆盖已确认的个人有效单，**不外推成年消费**")

    return {
        "unit": display_unit,
        "unit_label": _UNIT_LABELS.get(display_unit, display_unit),
        "unit_original": raw_unit or None,
        "unit_recognized": canonical is not None,
        "unit_source": "caller" if unit_known else "unspecified",
        "conversion": conversion,
        "included_orders": included,
        "with_amount": with_amount,
        "missing_amount_orders": missing_orders,
        "zero_amount_orders": zero_orders,
        "nonzero_amount_orders": with_amount - zero_orders,
        "missing_rate": missing_rate,
        "total": total,
        "mean": mean,
        "median": median,
        "min": low,
        "max": high,
        "range": spread,
        "dispersion": (
            {"metric": "coefficient_of_variation", "value": dispersion,
             "dimensionless": True}
            if dispersion is not None else None
        ),
        "stability": stability,
        "stability_basis": (
            "变异系数（无量纲）" if unit_known else "单位未核实，不给结论"
        ),
        "shared_orders_with_amount": len(shared_values),
        "shared_amounts": shared_values,
        "shared_amount_note": "共享单只列整单金额，不推人均、不推个人食量",
        "mixed_unit_warning": bool(cent_fields_seen),
        "cent_fields_seen": cent_fields_seen,
        "cent_field_values": cent_field_values,
        "cent_field_note": (
            "这些字段名自称以「分」为单位，但**币种与口径未经核实**，"
            "故未并入个人金额统计——**即使显式指定单位也不会采用**，"
            "它们始终留在隔离区。"
            if cent_fields_seen else None
        ),
        "notes": notes,
    }


def _novelty_block(product_freq, personal_count):
    """新品证据留口。没有可靠上市目录时，明确不可声称「新品」。"""
    seen_once = sorted({
        e["name"] for e in product_freq if e["appear_orders"] == 1 and e["name"]
    })
    seen_repeat = sorted({
        e["name"] for e in product_freq if e["appear_orders"] >= 2 and e["name"]
    })
    return {
        "catalog_available": False,
        "claimable": False,
        "state": "no_catalog",
        "repeat_names": seen_repeat,
        "seen_once_names": seen_once,
        "distinct_items": len([e for e in product_freq if e["name"]]),
        "note": (
            "没有可靠的上市日期/限定标记目录时，**不得**把"
            "「第一次在这批记录里出现」叫「新品」。"
            "只能讲可见样本内的变化与回购。"
        ),
    }


def _category_groups(product_freq):
    groups = {}
    for entry in product_freq:
        groups.setdefault(entry["category"], []).append(entry)
    return groups


# --------------------------------------------------------------------- 主流程

def normalize(raw, max_returnable=10, as_of=None, fetch_state="ok", amount_unit=None):
    """归一化订单。返回可直接落盘的 facts dict。

    fetch_state: "ok" | "not_connected" | "error"
        —— 用来区分「没接上」「接口失败」「账号确实空」，不把故障判成零消费。
    amount_unit: 金额单位（如 "CNY"）。**未核实就传 None**，此时不给任何金额结论。
    """
    if as_of is None:
        as_of = datetime.now(CN_TZ)
    elif isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=CN_TZ)

    # --- as_of 的时间边界 ---
    # as_of 只给到「日」时（00:00:00），边界是**这一天结束**：
    # 当天晚些的记录不能被判成未来，否则 9/28 会把 9/28 18:00 的单一并丢掉。
    # as_of 带具体时刻时，边界就是那个时刻。
    # **未来记录一律隔离**（不进偏好统计、不进可见窗口），
    # 也不用 max(0, age) 把负数天数抹平成 0。
    if (as_of.hour, as_of.minute, as_of.second, as_of.microsecond) == (0, 0, 0, 0):
        future_boundary = as_of.replace(hour=23, minute=59, second=59, microsecond=999999)
        boundary_basis = "as_of_date_only_end_of_day"
    else:
        future_boundary = as_of
        boundary_basis = "as_of_exact_moment"

    orders = _extract_list(raw)
    items = []
    notes = []
    future_isolated = 0

    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            notes.append(f"第 {index + 1} 条不是对象，已跳过。")
            continue
        status_raw = order.get("orderStatus")
        status_class = _status_class(status_raw)
        moment, date_state = parse_when(order.get("createTime"))
        if date_state == "parsed" and moment is not None and moment > future_boundary:
            date_state = "future"
            future_isolated += 1
        hour = _hour_of(order.get("createTime"))
        products, missing_name = _expand_products(order)
        if missing_name:
            notes.append(f"第 {index + 1} 条有餐品缺名称，已留空。")
        if not order.get("orderProductList"):
            notes.append(f"第 {index + 1} 条没有餐品明细，已留空。")
        reasons = _group_reasons(products)
        amount_raw = order.get("realTotalAmount")
        items.append({
            "index": index,
            "status_raw": status_raw,
            "status_class": status_class,
            "included": status_class != "canceled",
            # 未来记录隔离：不计入「可归于本人」，也就进不了偏好统计
            "is_personal": status_class == "completed" and not reasons
            and date_state != "future",
            "group_suspected": bool(reasons),
            "group_reasons": reasons,
            "segment": lexicon.segment_of(hour),
            "has_time": hour is not None,
            "date_state": date_state,
            "date_moment": moment if date_state == "parsed" else None,
            "date_iso": moment.date().isoformat() if moment else None,
            "structure": _structure_of(products),
            "products": products,
            "amount_present": _to_float(amount_raw) is not None,
            "amount_raw": amount_raw,
        })

    canceled = [i for i in items if i["status_class"] == "canceled"]
    valid = [i for i in items if i["included"]]
    unconfirmed = [i for i in valid if i["status_class"] == "unconfirmed"]
    grouped = [i for i in valid if i["group_suspected"]]
    personal = [i for i in valid if i["is_personal"]]
    pending = [i for i in valid if i not in personal]

    # --- 计数：只在「已完成且可归于本人」的订单上做偏好统计 ---
    freq = {}
    for order in personal:
        seen_standalone = set()
        seen_combo = set()
        for product in order["products"]:
            key = product["key"]
            entry = freq.setdefault(key, {
                "key": key,
                "observed_names": [],
                "name": None,
                "category": "other",
                "is_fries": False,
                "is_combo_parent": False,
                "is_condiment": False,
                "attrs": None,
                "appear_orders": 0,
                "standalone_orders": 0,
                "combo_orders": 0,
                "standalone_quantity": 0.0,
                "quantity_semantics_issue": False,
            })
            if product["name"] and product["name"] not in entry["observed_names"]:
                entry["observed_names"].append(product["name"])
            if product.get("is_combo_parent"):
                entry["is_combo_parent"] = True
            if product["source"] == "standalone":
                if key not in seen_standalone:
                    seen_standalone.add(key)
                    entry["standalone_orders"] += 1
                quantity = _to_float(product["quantity"])
                if quantity is not None:
                    entry["standalone_quantity"] += quantity
            else:
                if key not in seen_combo:
                    seen_combo.add(key)
                    entry["combo_orders"] += 1
                if product["quantity_semantics"] == "unclear":
                    entry["quantity_semantics_issue"] = True
        for key in seen_standalone | seen_combo:
            freq[key]["appear_orders"] += 1

    # 同一道餐品有多个写法时，取最短的那个当展示名
    for entry in freq.values():
        names = entry.pop("observed_names")
        entry["name"] = min(names, key=len) if names else None
        entry["category"] = _category_for({
            "name": entry["name"], "is_combo_parent": entry["is_combo_parent"],
        })
        entry["is_fries"] = lexicon.is_fries(entry["name"])
        entry["is_condiment"] = entry["category"] == "condiment"
        if entry["category"] == "drink":
            entry["attrs"] = lexicon.drink_attrs(entry["name"])

    product_freq = sorted(
        freq.values(),
        key=lambda e: (-e["appear_orders"], -e["standalone_orders"], str(e["name"])),
    )

    staple_names = sorted({
        entry["name"] for entry in product_freq
        if entry["category"] == "staple" and entry["name"]
    })
    combo_names = sorted({
        entry["name"] for entry in product_freq
        if entry["category"] == "combo" and entry["name"]
    })
    condiment_names = sorted({
        entry["name"] for entry in product_freq
        if entry["category"] == "condiment" and entry["name"]
    })
    unclassified = sorted({
        entry["name"] for entry in product_freq
        if entry["category"] == "other" and entry["name"]
    })

    # --- 订单级套餐结构（互斥，只算个人有效单）---
    structure = {"combo_only": 0, "standalone_only": 0, "mixed": 0, "unknown": 0}
    for order in personal:
        structure[order["structure"]] = structure.get(order["structure"], 0) + 1

    segment_freq = {"breakfast": 0, "main": 0, "night": 0, "unknown": 0}
    for order in personal:
        segment_freq[order["segment"]] += 1

    personal_indexes = {item["index"] for item in personal}
    shared_indexes = {item["index"] for item in valid if item["group_suspected"]}
    amount = _amount_block(orders, personal_indexes, shared_indexes, unit=amount_unit)

    time_block = _time_block(items, as_of, max_returnable,
                             future_boundary=future_boundary,
                             boundary_basis=boundary_basis)
    novelty = _novelty_block(product_freq, len(personal))

    # datetime 不能直接落盘：解析结果转成 ISO 字符串再上报
    for item in items:
        moment = item.get("date_moment")
        item["date_moment"] = moment.isoformat(timespec="seconds") if moment else None

    # --- 来源状态：分清「没接上 / 接口失败 / 账号真空 / 无有效样本 / 仅 1 单 / 久未下单 / 常规」---
    if fetch_state == "not_connected":
        source_state = "not_connected"
    elif fetch_state == "error":
        source_state = "fetch_failed"
    elif not items:
        source_state = "empty_account"
    elif not personal:
        source_state = "no_valid_samples"
    elif len(personal) == 1:
        source_state = "single_order"
    elif time_block["recency_state"] == "stale":
        source_state = "stale"
    else:
        source_state = "normal"

    # 只有真正的「时段模式」（早餐/夜间窗口）才算重复模式
    repeated_pattern = any(e["appear_orders"] >= 3 for e in product_freq) or any(
        segment_freq.get(segment, 0) >= 3 for segment in ("breakfast", "night")
    )
    personal_count = len(personal)
    tier = (
        "type_ready"
        if personal_count >= 3 and repeated_pattern
        else "snapshot_only"
    )

    # --- 说明 ---
    if not orders:
        notes.append("没有读到任何订单记录。")
    if fetch_state == "error":
        notes.append("本次读取接口失败，**不能**当成「账号没有记录」。")
    if fetch_state == "not_connected":
        notes.append("本次没有连接，走的是备用问卷入口。")
    if unconfirmed:
        notes.append(
            f"{len(unconfirmed)} 条订单状态未能识别，**已排除在偏好统计之外**，"
            "需要本人确认；它们不计入「已完成」的稳定类型门槛。"
        )
    if future_isolated:
        notes.append(
            f"{future_isolated} 条订单时间晚于 as_of，**按未来记录隔离**："
            "不算历史、不进偏好统计、不进可见窗口。"
        )
    if grouped:
        notes.append(f"{len(grouped)} 条疑似代点/团体单，已排除在偏好统计之外。")
    if pending and not unconfirmed and not grouped:
        notes.append(f"{len(pending)} 条订单未参与偏好统计。")
    if combo_names:
        notes.append(
            "以下套餐父项已按套餐处理，不算作主食种类：" + "、".join(combo_names)
        )
    if condiment_names:
        notes.append(
            "以下酱料/加料按调味处理：不算套餐、不算主食、不参与多样性："
            + "、".join(condiment_names)
        )
    if unclassified:
        notes.append(
            "以下餐品未能粗分类（按 other 处理，不计入主食种类，"
            "但仍按具体餐品名参与重复次数揭晓）：" + "、".join(unclassified)
        )

    return {
        "schema": schema.NORMALIZED_SCHEMA_ID,
        "max_returnable": max_returnable,
        "source_state": source_state,
        "counts": {
            "raw_orders": len(items),
            "excluded_canceled": len(canceled),
            "status_unconfirmed": len(unconfirmed),
            "valid_orders": len(valid),
            "group_suspected_pending": len(grouped),
            "personal_valid_orders": personal_count,
            "pending_confirmation": len(pending),
            "max_returnable": max_returnable,
        },
        "stats": {
            "distinct_staple": len(staple_names),
            "staple_names": staple_names,
            "combo_names": combo_names,
            "condiment_names": condiment_names,
            "unclassified_names": unclassified,
            "segment_freq": segment_freq,
            "product_freq": product_freq,
            "amount_stability": amount["stability"],
            "combo_quantity_used_in_totals": False,
            "quantity_semantics_note": (
                "套餐子项 quantity 的语义文档未穷举，未参与任何总额推断。"
            ),
            "repeated_pattern": repeated_pattern,
            "tier": tier,
        },
        "time": time_block,
        "amount": amount,
        "structure": {
            "basis": "订单级，四类互斥；酱料/加料不算套餐",
            "combo_only": structure["combo_only"],
            "standalone_only": structure["standalone_only"],
            "mixed": structure["mixed"],
            "unknown": structure["unknown"],
            "scope": "只统计已完成且可归于本人的订单",
            "note": "没有用子项份数当订单比例；套餐子项 quantity 语义不明，不参与计算",
        },
        "by_category": _category_groups(product_freq),
        "novelty": novelty,
        "items": items,
        "notes": notes,
    }
