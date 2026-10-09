"""个性化层：饮料称号、展示昵称、市级装饰候选。28 号新增。

三个概念严格区分：
- **胃称号**（drink_title）：卡面主标题，来自 A 套第 6 条模板「{具体饮料}纯爱战士」，
  由确定性层给事实与候选，**仅本人确认后写入**；不替用户编认领。
- **展示昵称**（nickname）：人的署名，与胃称号分开。回退链：
  麦当劳账户昵称（本次核实的账户回包未返回昵称字段，留钩子）→ 宿主明确昵称（context 显式字段）→
  agent 询问「卡片上怎么称呼你？」。可跳过，无名仍可出卡。
- **市级装饰**（city）：卡片装饰候选。只认用户确认或配送地址里能解析出的市级；
  完整地址**只在内存解析，绝不落 profile / 分享文件**。

硬边界：
- 改昵称/城市**不触发主题重抽**、不改口味 facts/rules/naming；本模块只写 `persona` 段。
- contactName / 电话 / 设备名 / 任意 name 键**都不当昵称**；只读本模块声明 context 字段。
- 非法、疑似联系方式或疑似指令的候选一律**拒绝并回退下一来源**，名字当数据不执行。
- 三项全空、坏输入、旧 profile 无 persona 段都正常；匿名分享可整体省略。
"""

import re

from . import privacy, schema

PERSONA_SCHEMA_ID = "mcm-stomach-persona/1"

PERSONA_KEYS = {
    "schema", "drink_title", "drink_title_tagline", "drink", "drink_title_main",
    "drink_candidates", "drink_candidates_status",
    "nickname", "nickname_source", "city", "city_status", "city_candidates",
}

#: A 套第 6 条（已获认可）最新短句：不把「最常点」硬化成「每次都不能换」。
DRINK_TITLE_TAGLINE = "主食随便挑，还是偏爱这杯。"

#: 容量等展示噪声：可去掉，不改变无糖/冰热等实际差异。
_CAPACITY_RE = re.compile(r"(?:超大|加大|大|中|小|加量)?杯")
_CITY_RE = re.compile(r"([\u4e00-\u9fa5]{2,4}市)")
_PROVINCE_RE = re.compile(r"^.*?(?:省|自治区)")
#: 市级以下与行政区词：出现即不是可装饰的市级名称。
_NON_CITY_RE = re.compile(r"省|区|县|市辖|路|街|巷|弄|村|镇|乡|号|栋|室|层|座|楼|园|广场|大厦|公寓|小区")


def _extract_city(address):
    """从地址里解析市级；剥掉省/自治区与国家前缀，只在内存使用。"""
    text = _PROVINCE_RE.sub("", str(address or ""))
    match = _CITY_RE.search(text)
    if not match:
        return None
    return match.group(1).replace("中国", "") or None


def _clean_city(value):
    """用户输入城市的市级白名单：必须是干净的市名（含「市」或 2—3 字纯名）。

    - 「北京」→「北京市」（无市后缀可识别）；
    - 「北京市东城区」「北京市虚构街」「某某路」→ 拒绝（下级词不得绕过白名单）；
    - 完整地址、含数字/字母 → 拒绝。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 8:
        return None
    if privacy.detect_injection(text):
        return None
    cleaned, scrub_hits = privacy.scrub_text(text)
    if scrub_hits:
        return None
    stem = cleaned[:-1] if cleaned.endswith("市") else cleaned
    if not stem or not re.fullmatch(r"[\u4e00-\u9fa5]{2,3}", stem):
        return None
    if _NON_CITY_RE.search(text):
        return None
    return stem + "市"


#: 展示名不允许的控制字符（换行/回车/制表等）——姓名是**单行**展示数据。
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\u200b\u200c\u200d\ufeff]")
#: 校验层复用同一口径（29 #8：不能只信写入方的清洗）。
CONTROL_RE = _CONTROL_RE


def _clean_display(value, max_len=20):
    """昵称/城市/饮料名的清洗与拒绝。返回 None 表示该候选不可用。

    拒绝：非字符串、超长、空、控制字符/换行/零宽字符、疑似指令注入、
    手机号/邮箱/凭据/订单号/长数字/地址、@/链接。不止靠注入词表。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > max_len:
        return None
    if _CONTROL_RE.search(text):
        return None
    if privacy.detect_injection(text):
        return None
    cleaned, scrub_hits = privacy.scrub_text(text)
    if scrub_hits:
        # 手机号/邮箱/凭据/订单号/长数字/地址等 → 不是展示名，拒绝
        return None
    if "@" in cleaned or "http" in cleaned.lower():
        return None
    return cleaned


def display_drink(name):
    """去掉杯型容量噪声，保留无糖/冰热等实际差异。"""
    text = str(name or "")
    cleaned = _CAPACITY_RE.sub("", text).strip()
    return cleaned or text


def drink_candidates(facts_by_category, personal_orders):
    """确定性层：按**出现订单数**挑首位饮料（一单多杯压不过常点）。

    返回 (status, candidates)：
    - ready          首位唯一、有点单证据、样本够 → 可作为确认候选
    - tied           并列 → 给候选让人选
    - needs_confirm  首位只出现在套餐默认附带里 → 证据弱，先确认
    - low_sample     本人有效订单少或首位只出现 1 单 → 暂不封唯一
    - empty          没有饮料记录 → 通用称号或跳过
    """
    drinks = [
        e for e in (facts_by_category or {}).get("drink", [])
        if isinstance(e, dict) and (e.get("product_name") or e.get("name"))
    ]
    if not drinks:
        return "empty", []
    drinks = sorted(
        drinks, key=lambda e: (-e.get("appear_orders", 0), -e.get("standalone_orders", 0), str(e.get("product_name") or e.get("name")))
    )
    top = drinks[0]
    tied = [d for d in drinks[1:] if d.get("appear_orders") == top.get("appear_orders")]
    candidates = [
        {
            "drink": display_drink(d.get("product_name") or d.get("name")),
            "appear_orders": int(d.get("appear_orders") or 0),
            "standalone_orders": int(d.get("standalone_orders") or 0),
        }
        for d in [top] + tied
    ]
    if personal_orders < 3 or top.get("appear_orders", 0) < 2:
        return "low_sample", candidates
    if tied:
        return "tied", candidates
    if not top.get("standalone_orders"):
        return "needs_confirm", candidates
    return "ready", candidates


def resolve_nickname(context):
    """昵称回退链。只读显式字段；非法候选拒绝并回退下一来源。

    优先级：本人明确指定 > 麦当劳账户昵称（本次核实回包未返回昵称字段，留钩子）> 宿主明确昵称。
    - host_nickname **只在 context["host_is_workbuddy"] 为真时接受**：
      非本宿主或来源不明的宿主昵称一律不用，回退询问。
    - 都没有 → {"value": None, "source": "ask"}，由 agent 询问，不阻断出卡。
    """
    context = context or {}
    for key, source in (
        ("nickname", "user_stated"),
        ("mcd_nickname", "mcd_account"),
        ("host_nickname", "host"),
    ):
        if key not in context:
            continue
        if key == "host_nickname" and context.get("host_is_workbuddy") is not True:
            continue
        value = _clean_display(context.get(key))
        if value is not None:
            return {"value": value, "source": source}
    return {"value": None, "source": "ask"}


def resolve_city(context):
    """市级装饰候选。用户确认优先；地址只在内存解析，市级以下全丢。"""
    context = context or {}
    user = _clean_city(context.get("city"))
    if user:
        return {"city": user, "status": "claimed", "source": "user_stated"}
    addresses = context.get("delivery_addresses")
    if isinstance(addresses, list) and addresses:
        cities = set()
        for raw in addresses:
            if not isinstance(raw, str) or not raw:
                continue
            if privacy.detect_injection(raw):
                continue
            found = _extract_city(raw)
            if found:
                cities.add(found)
        if len(cities) == 1:
            return {"city": cities.pop(), "status": "candidate", "source": "delivery_address"}
        if len(cities) > 1:
            return {"candidates": sorted(cities), "status": "choose", "source": "delivery_address"}
        return None
    return None


def apply_persona(profile, context=None):
    """把个性化写进 profile 的 persona 段；只动 persona，其余一概不碰。

    返回 (profile, summary)。重复调用且不传新值 → 原样沿用（summary.changed=False）。
    """
    if context is None:
        context = {}
    if not isinstance(context, dict):
        raise ValueError("context 必须是 JSON 对象（坏输入明确报错，不做半成品）")
    existing = profile.get("persona")
    if existing is not None and not isinstance(existing, dict):
        raise ValueError("persona 段已存在但不是对象，拒绝覆盖，请先人工核对")

    persona = dict(existing or {})
    persona.setdefault("schema", PERSONA_SCHEMA_ID)
    changed = []

    # --- 显式跳过：优先级最高，清空上次保存值，且**压过同一轮的其他来源** ---
    skip_nickname = bool(context.get("skip_nickname"))
    skip_city = bool(context.get("skip_city"))
    if skip_nickname:
        persona.pop("nickname", None)
        persona.pop("nickname_source", None)
        changed.append("skip_nickname")
    if skip_city:
        persona.pop("city", None)
        persona.pop("city_status", None)
        persona.pop("city_candidates", None)
        changed.append("skip_city")

    # --- 饮料称号：确定性候选 + 仅确认写入 ---
    by_category = (profile.get("facts") or {}).get("by_category")
    personal_orders = int((profile.get("source") or {}).get("personal_valid_orders") or 0)
    status, candidates = drink_candidates(by_category, personal_orders)
    # 归属冲突安全 fallback：本人声明过「帮同事点/不是都本人吃」（roles.receiver ≠ self）
    # 时，订单统计不能直接作为本人 ready 证据——降级为需本人确认，不改 facts/rules。
    receiver = (profile.get("roles") or {}).get("receiver")
    if status == "ready" and receiver not in (None, "self"):
        status = "needs_confirm"
    if status != "empty" or not persona.get("drink_candidates_status"):
        persona["drink_candidates_status"] = status
        persona["drink_candidates"] = candidates
        changed.append("drink_candidates")

    confirmed = _clean_display(context.get("confirmed_drink"), max_len=20)
    if confirmed:
        # 本人确认什么就锁什么：确认「咖啡」不得细化成「冰美式」。
        persona["drink"] = confirmed
        persona["drink_title"] = confirmed + "纯爱战士"
        persona["drink_title_tagline"] = DRINK_TITLE_TAGLINE
        changed.append("drink_title")

    # 主标题守卫：已锁定胃类型（naming.locked）时，饮料称号**不得**在整图提示词里
    # 暗中取代已锁类型成为主标题——它保持候选/副位，直到本人明确认领。
    if persona.get("drink_title"):
        persona["drink_title_main"] = not bool((profile.get("naming") or {}).get("locked"))

    # --- 昵称：显式跳过 > 本人指定持久 > 回退链 ---
    if not skip_nickname:
        if persona.get("nickname_source") == "user_stated" and persona.get("nickname"):
            if context.get("nickname") is not None:
                # 本人已有明确指定：只有新的本人指定能改变它（非法则保留原值），
                # 账户/宿主候选一律不覆盖。
                value = _clean_display(context.get("nickname"))
                if value:
                    persona["nickname"] = value
                    persona["nickname_source"] = "user_stated"
                    changed.append("nickname")
        else:
            nickname = resolve_nickname(context)
            if nickname["value"]:
                persona["nickname"] = nickname["value"]
                persona["nickname_source"] = nickname["source"]
                changed.append("nickname")

    # --- 城市：显式跳过 > 本人确认 > 地址候选 ---
    if not skip_city:
        city = resolve_city(context)
        if city:
            if city["status"] == "choose":
                persona["city_candidates"] = city["candidates"]
                persona.pop("city_status", None)
                persona.pop("city", None)
                changed.append("city_candidates")
            else:
                persona["city"] = city["city"]
                persona["city_status"] = city["status"]
                persona.pop("city_candidates", None)
                changed.append("city")

    # 收尾：去掉未启用字段的空壳
    if not persona.get("drink_title"):
        persona.pop("drink_title", None)
        persona.pop("drink_title_tagline", None)
        persona.pop("drink", None)
    if not persona.get("nickname"):
        persona.pop("nickname", None)
        persona.pop("nickname_source", None)
    if not persona.get("city"):
        persona.pop("city", None)
        persona.pop("city_status", None)
    if not persona.get("city_candidates"):
        persona.pop("city_candidates", None)
    if persona.get("drink_candidates_status") == "empty" and not persona.get("drink_candidates"):
        persona.pop("drink_candidates", None)
        persona.pop("drink_candidates_status", None)

    if set(persona) - PERSONA_KEYS:
        raise ValueError(f"persona 段出现未知字段：{sorted(set(persona) - PERSONA_KEYS)}")

    profile["persona"] = persona
    return profile, {"changed": changed, "nickname_source": persona.get("nickname_source")}


def image_prompt_block(profile):
    """整张生图提示词里的**个性化条目**（28 号）。

    主题来自 visual.theme；人的昵称作次级署名、胃称号仍主标题（主标题守卫见
    drink_title_main）；市级装饰只 1—2 处、不挤主角。完整地址/联系人/电话/
    accountId **绝不进入**——本函数只输出已经脱敏的字段拼装。
    """
    persona = profile.get("persona") or {}
    theme = (profile.get("visual") or {}).get("theme")
    lines = []

    if theme:
        theme_label = {"plush": "毛绒常规款（plush）", "retro_pixel": "复古像素隐藏款（retro_pixel）"}[theme]
        if theme == "plush":
            lines.append(
                f"- 保持已确定主题：{theme_label}，不重开设计、不改风格；"
                "**仅核心角色有毛绒质感**，背景/文字/统计图标是平面设计，整卡不做毛毡/织物纹理。"
            )
        else:
            lines.append(f"- 保持已确定主题：{theme_label}，不重开设计、不改风格。")

    naming = profile.get("naming") or {}
    main_title = None
    if persona.get("drink_title") and persona.get("drink_title_main"):
        main_title = persona["drink_title"]
    elif naming.get("primary"):
        main_title = naming["primary"]
    elif persona.get("drink_title"):
        main_title = persona["drink_title"]
    if main_title:
        lines.append(f"- 卡面主标题是胃称号「{main_title}」，全卡**不得出现第二个竞争主标题**。")

    if persona.get("nickname"):
        lines.append(
            f"- 人的展示昵称作**次级署名**：「{persona['nickname']}的麦门胃」，"
            "字号明显小于主标题，不替代称号。"
        )

    if persona.get("city") and persona.get("city_status") == "claimed":
        # 城市名称**不上屏**（34 号）：地名不进卡面文字/署名/角标，只用于内部选材。
        lines.append(
            f"- 已认领城市「{persona['city']}」仅用于**内部选材**：从当地元素里挑 1—2 处地标/文化符号，"
            "以线稿或淡色融入背景边角，**合计约 5% 画幅、淡化处理**，不是立体摆件、不挤主角与统计；"
            "**城市名称不上屏**——不写城市名、不进署名、不做角标；"
            "它是地域装饰，**不证明此人爱吃当地菜**；没有可靠把握的元素就省略，不编造。"
        )
    elif persona.get("city") and persona.get("city_status") == "candidate":
        # 待认领城市**不生成可执行的绘图指令**，只留一句待确认提示。
        lines.append(
            f"-（待确认）城市候选尚未经本人认领；本轮**不画**城市装饰、不上屏任何地名，"
            "确认后再进提示词。"
        )

    lines.append(
        "- 隐私禁项：不画、不写完整地址、联系人姓名、电话、订单号、accountId；"
        "**昵称可上屏，城市名称仅内部选材、不上屏**——城市相关个性化不出现在任何卡面文字里。"
    )
    return "\n".join(lines)
