"""profile.json 的 schema 常量与共享口径。"""

SCHEMA_ID = "mcm-stomach/1"
NORMALIZED_SCHEMA_ID = "mcm-stomach-normalized/1"
SHARE_CARD_SCHEMA_ID = "mcm-stomach-share-card/1"
WHITELIST_VERSION = "wl-2"

LEVEL_POLICY = "mcm_fan_level_custom_v1"

#: 自定义娱乐段位。阈值公开可复算；**不是麦当劳官方评级**。
#: 只由 query-my-account.accumulativePoint（累计积分）决定。
LEVELS = [
    (500000, "麦门掌门"),
    (200000, "掌门弟子"),
    (100000, "长老"),
    (50000, "执事"),
    (20000, "亲传弟子"),
    (5000, "内门弟子"),
    (1000, "入门弟子"),
    (0, "外门弟子"),
]

LEVEL_PENDING_LABEL = "待认证"

LEVEL_DISCLAIMER = (
    "娱乐段位，非麦当劳官方评级；阈值公开可复算，"
    "只看累计积分，不看订单数。"
)

CHALLENGE = "把你的胃发来，看今晚谁迁就谁。"

DISCLAIMER = "昵称与类型为娱乐性解读，不是人格诊断。"

#: 收件人可照做的启动提示。**不依赖任何尚未发布的 URL**，也不画二维码。
HOW_TO_USE = "把这个胃文件交给你的 AI，跟它说「用这个胃帮我挑一餐」。"

#: 视觉主题（首次分配，一次抽签永久沿用）：
#: plush=毛绒常规（5 槽占 4，80%）；retro_pixel=复古像素隐藏款（5 槽占 1，20%）。
VISUAL_THEMES = ["plush", "retro_pixel"]
VISUAL_SCHEMA_ID = "mcm-stomach-visual/1"
VISUAL_THEME_LABELS = {"plush": "毛绒常规款", "retro_pixel": "复古像素隐藏款"}

#: 个性化段（28 号）：胃称号 / 展示昵称 / 市级装饰。可选段，三项均可省略。
PERSONA_SCHEMA_ID = "mcm-stomach-persona/1"
NICKNAME_SOURCES = ["user_stated", "mcd_account", "host"]
CITY_STATUSES = ["claimed", "candidate"]
#: 公开卡上的城市只带本人确认过的（claimed）；candidate 只是装饰候选。
PERSONA_NICKNAME_LIMIT = 20

#: 分享卡字段白名单。导出时只允许这些键出现。
SHARE_WHITELIST = [
    "schema",
    "card_version",
    "profile_id",
    "card_kind",
    "basis",
    "type",
    "habit_tag",
    "declaration",
    "evidence_line",
    "source_range",
    "order_rule",
    "orders_seen",
    "how_to_use",
    "level_label",
    "level_disclaimer",
    "challenge",
    "disclaimer",
    "generated_date",
    "sanitized_fields",
    "injection_flags",
    "visual_theme",
    "nickname",
    "drink_title",
    "drink_title_tagline",
    "drink_title_main",
    "city",
]

CONFIDENCE_ORDER = ["low", "medium", "high"]
CONFIDENCE_VALUES = ["low", "medium", "high", "user_stated"]
RULE_DIMENSIONS = [
    "staple", "side", "drink", "combo", "breakfast", "hard_avoid",
    "must_have", "session", "budget", "novelty",
]
RULE_KINDS = ["hard_avoid", "preference", "constraint", "habit"]

#: `order_rule`：可公开的**一条**选餐建议（脱敏，只含餐品名与计数）；
#: `orders_seen`：本人可见订单数（聚合计数，非明细），用于区分「真没订单」与「有单但类型未成立」。

#: 分享卡形态：orders=订单推断；stated=本人自述；snapshot=只描述这次口味
CARD_KINDS = ["orders", "stated", "snapshot"]
#: card_kind -> 对应的 basis 取值（必须是同一套枚举，不能一处写 stated 一处写 user_stated）
CARD_BASIS = {"orders": "orders", "stated": "user_stated"}
#: user_claimed = 本人在候选基础上认领/自起的外号
NAMING_SOURCES = ["orders", "user_stated", "user_claimed"]

#: 来源状态：分清「没接上 / 接口失败 / 账号真空 / 无有效样本 / 仅 1 单 / 久未下单 / 常规」
SOURCE_STATES = [
    "not_connected", "fetch_failed", "empty_account", "no_valid_samples",
    "single_order", "stale", "normal",
]
SOURCE_STATE_LABELS = {
    "not_connected": "本次没有连接",
    "fetch_failed": "本次读取失败",
    "empty_account": "本次未返回订单记录",
    "no_valid_samples": "有记录但没有可用样本",
    "single_order": "只有一笔可用订单",
    "stale": "最近一次可见下单距今较久",
    "normal": "常规",
}

#: 叙事来源：code=脚本产候选；model=模型基于事实写；user=本人认领
NARRATIVE_SOURCES = ["code", "model", "user"]

#: 订单级套餐结构（互斥）
ORDER_STRUCTURES = ["combo_only", "standalone_only", "mixed", "unknown"]
STRUCTURE_LABELS = {
    "combo_only": "只点了套餐",
    "standalone_only": "只单点",
    "mixed": "套餐＋加点混着",
    "unknown": "结构判断不出",
}


def level_for_points(accumulative_point):
    """累计积分 -> (state, label)。读不到就返回待认证。"""
    if accumulative_point is None:
        return "pending", LEVEL_PENDING_LABEL
    try:
        value = float(accumulative_point)
    except (TypeError, ValueError):
        return "pending", LEVEL_PENDING_LABEL
    for floor, label in LEVELS:
        if value >= floor:
            return "certified", label
    return "pending", LEVEL_PENDING_LABEL
