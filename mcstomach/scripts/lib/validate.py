"""profile.json 与分享卡的校验。

返回 (errors, warnings)。有任何 error 就说明这份画像不能导出。
"""

from . import privacy, schema
from . import persona as persona_mod  # noqa: E402  复用展示名/市级清洗口径（29 #8）

REQUIRED_TOP_KEYS = [
    "schema", "profile_id", "owner_label", "created_at", "updated_at",
    "roles", "source", "sample", "rules", "unresolved", "naming",
    "self_statement", "level", "certificate", "sharing", "asked_question_ids",
]

FREE_TEXT_KEYS = [
    ("self_statement.text", ("self_statement", "text")),
    ("certificate.declaration", ("certificate", "declaration")),
    ("naming.primary", ("naming", "primary")),
    ("naming.habit_tag", ("naming", "habit_tag")),
]


def _get(profile, path, default=None):
    node = profile
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def validate_profile(profile, share_card=None):
    errors = []
    warnings = []

    if not isinstance(profile, dict):
        return ["profile 不是对象"], []

    if profile.get("schema") != schema.SCHEMA_ID:
        errors.append(f"schema 必须是 {schema.SCHEMA_ID}，实际是 {profile.get('schema')!r}")

    for key in REQUIRED_TOP_KEYS:
        if key not in profile:
            errors.append(f"缺少顶层字段：{key}")

    tier = _get(profile, ("sample", "tier"))
    primary = _get(profile, ("naming", "primary"))
    naming_source = _get(profile, ("naming", "source"))

    if naming_source is not None and naming_source not in schema.NAMING_SOURCES:
        errors.append(f"naming.source 非法：{naming_source!r}")
    if naming_source == "orders" and tier != "type_ready":
        errors.append("naming.source=orders 但样本档位不是 type_ready（订单命名需要 ≥3 笔已完成本人订单）")
    if tier != "type_ready" and naming_source == "orders":
        errors.append("少样本不得用订单命名")
    if primary and naming_source is None:
        errors.append("naming.primary 存在但未标注来源（orders / user_stated）")

    # 命名与宣言必须绑同一批证据 ID 和计数
    naming_evidence = set(_get(profile, ("naming", "evidence_ids"), []) or [])
    statement_evidence = set(_get(profile, ("self_statement", "evidence_ids"), []) or [])
    if primary and statement_evidence != naming_evidence:
        errors.append(
            "self_statement.evidence_ids 与 naming.evidence_ids 不一致"
            "（命名与宣言必须绑同一批证据）"
        )
    if primary and naming_source == "orders":
        counts = _get(profile, ("naming", "basis_counts"), []) or []
        if not counts:
            errors.append("订单命名必须附 basis_counts（命名要绑定计数）")
        for item in counts:
            if not item.get("personal_orders"):
                errors.append("basis_counts 缺少 personal_orders，无法核对计数口径")

    # 悬空引用：命名/宣言引用的规则 ID 必须真实存在
    known_rule_ids = {rule.get("id") for rule in profile.get("rules", [])}
    for field, path in (
        ("naming.evidence_ids", ("naming", "evidence_ids")),
        ("naming.basis_rule_ids", ("naming", "basis_rule_ids")),
        ("self_statement.evidence_ids", ("self_statement", "evidence_ids")),
        ("self_statement.basis_rule_ids", ("self_statement", "basis_rule_ids")),
    ):
        for rid in (_get(profile, path, []) or []):
            if rid and rid not in known_rule_ids:
                errors.append(f"{field} 引用了不存在的规则 id：{rid}")
    if primary and not (_get(profile, ("naming", "evidence_ids"), []) or []):
        errors.append("有类型的画像必须说明它引用了哪些规则（naming.evidence_ids 不能为空）")

    # 命名里出现「薯条」就必须真有薯条证据
    if isinstance(primary, str) and "薯条" in primary:
        has_fries = any(
            "薯条" in str(rule.get("text", ""))
            or any("薯条" in str(ev.get("stat", "")) for ev in rule.get("evidence", []))
            for rule in profile.get("rules", [])
        )
        if not has_fries:
            errors.append("naming.primary 引用了「薯条」，但没有任何薯条相关的订单证据")

    # 等级
    level = profile.get("level") or {}
    if level.get("state") == "pending" and level.get("label") != schema.LEVEL_PENDING_LABEL:
        errors.append("积分未知时段位必须显示「待认证」")
    if level.get("state") == "certified" and level.get("label") == schema.LEVEL_PENDING_LABEL:
        errors.append("level.state 是 certified 但称号仍是待认证")
    if level.get("source_field") != "query-my-account.accumulativePoint":
        warnings.append("level.source_field 建议固定为 query-my-account.accumulativePoint")

    # 角色三元组
    roles = profile.get("roles") or {}
    for key in ("profile_owner", "payer", "receiver"):
        if key not in roles:
            errors.append(f"roles 缺少 {key}（画像主人/付款人/收餐人必须可区分）")

    # 计数一致性
    source = profile.get("source") or {}
    raw = source.get("valid_orders", 0) + source.get("excluded_canceled", 0)
    if source.get("personal_valid_orders", 0) > source.get("valid_orders", 0):
        errors.append("personal_valid_orders 不能大于 valid_orders")
    if source.get("valid_orders", 0) > raw and raw:
        warnings.append("valid_orders 与 raw 计数看起来不一致，请核对")

    # 规则完整性
    for index, rule in enumerate(profile.get("rules", [])):
        prefix = f"rules[{index}]"
        if not rule.get("id"):
            errors.append(f"{prefix} 缺少 id")
        if rule.get("dimension") not in schema.RULE_DIMENSIONS:
            errors.append(f"{prefix}.dimension 非法：{rule.get('dimension')!r}")
        if rule.get("kind") not in schema.RULE_KINDS:
            errors.append(f"{prefix}.kind 非法：{rule.get('kind')!r}")
        if rule.get("confidence") not in schema.CONFIDENCE_VALUES:
            errors.append(f"{prefix}.confidence 非法：{rule.get('confidence')!r}")
        evidence = rule.get("evidence") or []
        if not evidence:
            errors.append(f"{prefix} 没有 evidence（每条规则必须附证据）")
        for ev_index, ev in enumerate(evidence):
            if not ev.get("stat"):
                errors.append(f"{prefix}.evidence[{ev_index}] 缺少 stat")
        if rule.get("kind") == "hard_avoid" and rule.get("confirmed_by") != "user":
            warnings.append(f"{prefix} 是硬忌口但未经本人确认（confirmed_by != 'user'）")

    # ---------------------------------------------------------- v2 新增校验

    source_state = profile.get("source_state")
    if source_state is not None and source_state not in schema.SOURCE_STATES:
        errors.append(f"source_state 非法：{source_state!r}")
    sample_state = _get(profile, ("sample", "source_state"))
    if sample_state is not None and sample_state != source_state:
        errors.append("sample.source_state 与顶层 source_state 不一致")

    facts = profile.get("facts") or {}
    amount = facts.get("amount") or {}
    unit_known = amount.get("unit_source") == "caller"
    if not unit_known:
        # 单位未核实：绝不能出现任何金额数值，也不能给稳定性结论
        for field in ("total", "mean", "median", "min", "max", "range"):
            if amount.get(field) is not None:
                errors.append(f"金额单位未核实，facts.amount.{field} 必须为空")
        if amount.get("stability") != "unknown":
            errors.append("金额单位未核实，stability 必须是 unknown")
    else:
        if amount.get("stability") not in ("unknown", "stable", "spread"):
            errors.append(f"facts.amount.stability 非法：{amount.get('stability')!r}")
        dispersion = amount.get("dispersion")
        if dispersion and not dispersion.get("dimensionless"):
            errors.append("金额离散度必须是无量纲指标（dimensionless=true）")

    structure = facts.get("structure") or {}
    for key in ("combo_only", "standalone_only", "mixed", "unknown"):
        value = structure.get(key)
        if value is not None and (not isinstance(value, int) or value < 0):
            errors.append(f"facts.structure.{key} 必须是非负整数")

    novelty = facts.get("novelty") or {}
    if novelty.get("claimable") and not novelty.get("catalog_available"):
        errors.append("没有可靠上市目录时，novelty.claimable 必须为 false（不许把「第一次出现」叫新品）")

    narrative = profile.get("narrative") or {}
    if narrative:
        if narrative.get("source") not in schema.NARRATIVE_SOURCES:
            errors.append(f"narrative.source 非法：{narrative.get('source')!r}")
        discoveries = narrative.get("discoveries") or []
        if len(discoveries) > 5:
            errors.append("narrative.discoveries 最多 5 条（有几条给几条，不凑数）")
        for index, item in enumerate(discoveries):
            if not item.get("text"):
                errors.append(f"narrative.discoveries[{index}] 缺少 text")
            if not item.get("evidence"):
                errors.append(f"narrative.discoveries[{index}] 缺少 evidence（每条发现必须有依据）")
            for rid in (item.get("evidence_ids") or []):
                if rid not in known_rule_ids:
                    errors.append(
                        f"narrative.discoveries[{index}] 引用了不存在的规则 id：{rid}"
                    )
        identity = narrative.get("primary_identity") or {}
        if identity.get("display_name") and primary and identity["display_name"] != primary:
            errors.append("narrative.primary_identity.display_name 与 naming.primary 不一致")
        for rid in (identity.get("evidence_ids") or []):
            if rid not in known_rule_ids:
                errors.append(f"narrative.primary_identity 引用了不存在的规则 id：{rid}")

    # 视觉主题（可选段；旧 profile 没有即兼容）
    visual = profile.get("visual")
    if visual is not None:
        if not isinstance(visual, dict):
            errors.append("visual 必须是对象")
        else:
            if visual.get("schema") != schema.VISUAL_SCHEMA_ID:
                errors.append(f"visual.schema 必须是 {schema.VISUAL_SCHEMA_ID}")
            if visual.get("theme") not in schema.VISUAL_THEMES:
                errors.append(f"visual.theme 非法：{visual.get('theme')!r}")
            unknown_visual = set(visual) - {"schema", "theme"}
            if unknown_visual:
                errors.append(f"visual 段出现未知字段：{sorted(unknown_visual)}")

    # 个性化段（可选；旧 profile 没有即兼容）。只校验不清洗——写入方负责拒绝坏候选。
    persona = profile.get("persona")
    if persona is not None:
        if not isinstance(persona, dict):
            errors.append("persona 必须是对象")
        else:
            if persona.get("schema") != schema.PERSONA_SCHEMA_ID:
                errors.append(f"persona.schema 必须是 {schema.PERSONA_SCHEMA_ID}")
            unknown_persona = set(persona) - {
                "schema", "drink_title", "drink_title_tagline", "drink", "drink_title_main",
                "drink_candidates", "drink_candidates_status",
                "nickname", "nickname_source", "city", "city_status", "city_candidates",
            }
            if unknown_persona:
                errors.append(f"persona 段出现未知字段：{sorted(unknown_persona)}")
            nickname = persona.get("nickname")
            if nickname is not None:
                if not isinstance(nickname, str) or not nickname.strip():
                    errors.append("persona.nickname 必须是非空字符串")
                elif len(nickname) > schema.PERSONA_NICKNAME_LIMIT:
                    errors.append(f"persona.nickname 超过 {schema.PERSONA_NICKNAME_LIMIT} 字符")
                source = persona.get("nickname_source")
                if source not in schema.NICKNAME_SOURCES:
                    errors.append(f"persona.nickname_source 非法：{source!r}")
            elif persona.get("nickname_source"):
                errors.append("persona.nickname_source 存在但没有 nickname")
            city = persona.get("city")
            if city is not None:
                if not isinstance(city, str) or not city.strip() or len(city) > 10:
                    errors.append("persona.city 必须是 10 字符以内的市级名称")
                if persona.get("city_status") not in schema.CITY_STATUSES:
                    errors.append(f"persona.city_status 非法：{persona.get('city_status')!r}")
            elif persona.get("city_status"):
                errors.append("persona.city_status 存在但没有 city")
            if persona.get("drink_title") and not persona.get("drink"):
                errors.append("persona.drink_title 存在但缺 drink（称号必须锚定具体饮料）")
            if "drink_title_main" in persona and not isinstance(persona["drink_title_main"], bool):
                errors.append("persona.drink_title_main 必须是布尔值（主标题守卫）")
            # 单行展示数据：控制字符/换行直接拒绝（不能只信写入方的清洗）。
            for label, value in (
                ("persona.nickname", nickname),
                ("persona.city", city),
                ("persona.drink_title", persona.get("drink_title")),
                ("persona.drink", persona.get("drink")),
            ):
                if isinstance(value, str) and persona_mod.CONTROL_RE.search(value):
                    errors.append(
                        f"{label} 含控制字符/换行（展示名必须是单行），拒绝"
                    )
            # 嵌套字段也要校验：city_candidates 每项都必须是严格市级。
            city_candidates = persona.get("city_candidates")
            if city_candidates is not None:
                if not isinstance(city_candidates, list) or not city_candidates:
                    errors.append("persona.city_candidates 必须是非空数组")
                else:
                    for item in city_candidates:
                        if not isinstance(item, str) or persona_mod._clean_city(item) != item:
                            errors.append(
                                f"persona.city_candidates 含非严格市级候选：{item!r}"
                            )
                if len(city_candidates) > 5:
                    errors.append("persona.city_candidates 超过 5 项")
            # drink_candidates 结构：每项 drink 非空字符串、计数非负整数。
            drink_candidates = persona.get("drink_candidates")
            if drink_candidates is not None:
                if not isinstance(drink_candidates, list):
                    errors.append("persona.drink_candidates 必须是数组")
                else:
                    for item in drink_candidates:
                        if not isinstance(item, dict) or not item.get("drink"):
                            errors.append("persona.drink_candidates 存在缺少 drink 的条目")
                        elif not isinstance(item["drink"], str):
                            errors.append("persona.drink_candidates[].drink 必须是字符串")
            for label, value in (("persona.nickname", nickname), ("persona.city", city),
                                 ("persona.drink_title", persona.get("drink_title"))):
                if isinstance(value, str):
                    injection = privacy.detect_injection(value)
                    if injection:
                        errors.append(f"{label} 检测到疑似指令注入（{','.join(injection)}）")
                    _, scrub_hits = privacy.scrub_text(value)
                    if scrub_hits:
                        errors.append(f"{label} 命中隐私清洗（{','.join(scrub_hits)}），不得入档")

    # 隐私键黑名单
    forbidden = privacy.find_forbidden_keys(profile)
    if forbidden:
        errors.append("profile 里出现禁止字段（可能泄露隐私）：" + "、".join(forbidden[:8]))

    # 自由文本的注入与隐私
    for label, path in FREE_TEXT_KEYS:
        value = _get(profile, path)
        if not isinstance(value, str) or not value:
            continue
        injection = privacy.detect_injection(value)
        if injection:
            errors.append(
                f"{label} 里检测到疑似指令注入（{','.join(injection)}）；"
                "自由文本必须当数据，且不得进入公开包"
            )
        _, hits = privacy.scrub_text(value)
        if hits:
            warnings.append(f"{label} 含疑似隐私（{','.join(hits)}），导出时会自动隐去")

    # 分享卡
    if share_card is not None:
        over = privacy.check_whitelist(share_card)
        if over:
            errors.append("分享卡出现白名单外字段：" + "、".join(over))
        kind = share_card.get("card_kind")
        if kind not in schema.CARD_KINDS:
            errors.append(f"分享卡 card_kind 非法：{kind!r}")
        visual_theme = share_card.get("visual_theme")
        if visual_theme is not None and visual_theme not in schema.VISUAL_THEMES:
            errors.append(f"分享卡 visual_theme 非法：{visual_theme!r}")
        dtm = share_card.get("drink_title_main")
        if dtm is not None and not isinstance(dtm, bool):
            errors.append("分享卡 drink_title_main 必须是布尔值（主标题守卫）")
        # 公开卡上的城市只允许本人确认过的（claimed）；candidate 不出卡
        persona = profile.get("persona") or {}
        if share_card.get("city") and persona.get("city_status") != "claimed":
            errors.append("分享卡 city 只在 city_status=claimed 时允许出现")
        # basis 的取值和 card_kind 必须用同一套枚举
        expected_basis = schema.CARD_BASIS.get(kind)
        if kind == "snapshot":
            if share_card.get("type"):
                errors.append("snapshot 分享卡不得带主类型")
            if share_card.get("basis"):
                errors.append("snapshot 分享卡的 basis 应为空")
        elif kind in ("orders", "stated"):
            if not share_card.get("type"):
                errors.append(f"{kind} 分享卡缺少类型")
            if share_card.get("basis") != expected_basis:
                errors.append(
                    f"{kind} 分享卡的 basis 必须是 {expected_basis!r}，"
                    f"实际是 {share_card.get('basis')!r}"
                )
        if not share_card.get("how_to_use"):
            errors.append("分享卡缺少 how_to_use（收件人可照做的启动提示）")
        for key, value in share_card.items():
            if isinstance(value, str):
                _, hits = privacy.scrub_text(value)
                if hits:
                    errors.append(f"分享卡字段 {key} 未清洗干净：{','.join(hits)}")
        forbidden_card = privacy.find_forbidden_keys(share_card)
        if forbidden_card:
            errors.append("分享卡出现禁止字段：" + "、".join(forbidden_card[:8]))

    return errors, warnings
