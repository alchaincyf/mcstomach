"""餐品粗分类词典与名称归并。

⚠️ 这是**启发式关键词表**，不是麦当劳官方分类。
原则：
1. 优先匹配**更具体**的名称（套餐 > 甜点 > 主食 > 配餐 > 饮品）；
2. 识别不出来一律归为 "other" 并保留原名，绝不猜、绝不为了凑结论硬归类；
3. 分类失败不影响揭晓——依然可以按具体餐品名的重复次数讲事实。
"""

import re

#: 名字里带这些词的是**套餐/组合**，它本身不是一道「主食选择」。
COMBO_KEYWORDS = ["套餐", "组合", "拼", "全餐", "桶", "双人", "分享"]

#: 甜点必须排在主食前面，否则「蛋卷冰淇淋」会因为「卷」被当成主食。
DESSERT_KEYWORDS = ["冰淇淋", "蛋卷", "圣代", "新地", "甜筒", "派", "蛋糕", "奶昔"]

STAPLE_KEYWORDS = [
    "巨无霸", "麦香鱼", "麦香鸡", "麦满分", "堡", "卷",
    "饭", "粥", "松饼", "热香饼", "三明治", "肉夹馍", "包子",
]

#: 配餐。**油条归这里，不归主食**：麦当劳早餐里它的位置和薯饼同格
#: （麦满分套餐可配薯饼也可配油条），是「堡之外再加的那一样」。
#: 若把它算成主食，「堡＋油条」一顿就会被数成两种主食，
#: 主食多样性会被虚高（曾把 3 款堡数成 4 种主食）。
SIDE_KEYWORDS = [
    "薯条", "鸡块", "麦乐鸡", "麦辣鸡翅", "鸡翅", "玉米杯", "苹果片",
    "沙拉", "洋葱圈", "薯饼", "油条", "鸡排", "香骨鸡",
]

DRINK_KEYWORDS = [
    "可乐", "雪碧", "芬达", "咖啡", "拿铁", "美式", "摩卡", "红茶", "绿茶",
    "奶茶", "橙汁", "果汁", "牛奶", "豆浆", "矿泉水", "纯净水", "柠檬",
    "汽水", "冰摇", "气泡", "水",
]

#: 酱料/加料/调味包：**不算套餐、不算主食、不参与多样性**。
#: v2 修正：名字里带「拼」「组合」的酱料（如「鸡薯双全四拼酱」）曾被误判成套餐。
CONDIMENT_KEYWORDS = ["酱", "蘸", "料包", "调味", "沾料", "糖包", "奶精", "番茄沙司"]

#: 判断顺序即优先级。**酱料最先**（免得「四拼酱」被套餐词吃掉），随后套餐、甜点、主食、配餐、饮品。
_ORDERED = [
    ("condiment", CONDIMENT_KEYWORDS),
    ("combo", COMBO_KEYWORDS),
    ("dessert", DESSERT_KEYWORDS),
    ("staple", STAPLE_KEYWORDS),
    ("side", SIDE_KEYWORDS),
    ("drink", DRINK_KEYWORDS),
]

#: 出现这些词说明它是一道**真正的餐品**，即使名字里带「酱」也不是调味料。
#: 用于压掉「酱香鸡腿堡」这类误判。
#: 注意：不要把「沙拉」放进来——「沙拉酱」是一包酱，不是一份沙拉。
_FOOD_MARKERS = [
    "堡", "卷", "饭", "粥", "薯条", "鸡块", "麦乐鸡", "鸡翅", "冰淇淋",
    "新地", "圣代", "派", "可乐", "咖啡", "拿铁", "美式", "橙汁", "奶茶",
    "松饼", "热香饼", "麦满分", "三明治", "玉米杯", "苹果片",
]

#: 饮料属性关键词（v2：无糖/含糖、冰/热不许合并）
ICED_KEYWORDS = ["冰", "冷"]
HOT_KEYWORDS = ["热", "暖"]
ZERO_SUGAR_KEYWORDS = ["无糖", "零度", "健怡", "零卡", "0糖", "无蔗糖", "无热量"]
SUGAR_FREE_ALIAS = "zero_sugar"

FRIES_KEYWORD = "薯条"

#: 名称噪声前缀（只在开头整体匹配，不拆单字，避免把「新地」削成「地」）
_NOISE_PREFIXES = ("新升级", "全新升级", "升级版", "全新", "升级")
_PARENS = re.compile(r"[（(][^）)]*[）)]")
_TRAILING_CODE = re.compile(r"[_\-]\d{2,}$")
_SPACES = re.compile(r"\s+")

#: 早餐窗口（小时，左闭右开，跨零点单独处理）
BREAKFAST_WINDOW = (5, 10.5)
NIGHT_WINDOW_START = 21.0
NIGHT_WINDOW_END = 5.0


def classify(name):
    """把餐品名粗分类。识别不出来返回 'other'。

    顺序：酱料 → 套餐 → 甜点 → 主食 → 配餐 → 饮品。
    酱料用 `is_condiment()` 判定（带食物词的「沙拉酱」不会被误当成调味料，
    「鸡薯双全四拼酱」则会被正确判成调味料）。
    """
    if not name:
        return "other"
    text = str(name)
    if is_condiment(text):
        return "condiment"
    for category, keywords in _ORDERED:
        if category == "condiment":
            continue
        for keyword in keywords:
            if keyword in text:
                return category
    return "other"


def is_condiment(name):
    """是不是酱料/加料/调味包。

    「鸡薯双全四拼酱」→ True（尽管名字里有「拼」）；
    「酱香鸡腿堡」→ False（它是主食，不是调料）。
    """
    if not name:
        return False
    text = str(name)
    if not any(keyword in text for keyword in CONDIMENT_KEYWORDS):
        return False
    return not any(marker in text for marker in _FOOD_MARKERS)


def is_combo_name(name):
    """名字里带套餐词的才算套餐；酱料/加料一律不算。"""
    if not name or is_condiment(name):
        return False
    return any(keyword in str(name) for keyword in COMBO_KEYWORDS)


def drink_attrs(name):
    """饮料的温度与糖分属性。**只认名字里写明的，不猜。**

    返回 {"temperature": ice/hot/unknown, "sugar": zero/unknown}
    """
    text = str(name or "")
    if any(keyword in text for keyword in ZERO_SUGAR_KEYWORDS):
        sugar = "zero_sugar"
    else:
        sugar = "unknown"
    if any(keyword in text for keyword in ICED_KEYWORDS):
        temperature = "iced"
    elif any(keyword in text for keyword in HOT_KEYWORDS):
        temperature = "hot"
    else:
        temperature = "unknown"
    return {"temperature": temperature, "sugar": sugar}


def is_fries(name):
    """只有名字里真的含「薯条」才算薯条。

    「鸡薯双全四拼酱」这类含「薯」的产品**不算薯条**，
    也不参与主食计数——它是拼盘。
    """
    return bool(name) and FRIES_KEYWORD in str(name)


def merge_key(name):
    """把同一道餐品的不同写法合成一个键。

    例：「新升级麦香鱼_1547」→「麦香鱼」；
    这样**套餐子项**与**单点**的同一道主食能合并计数。
    """
    if not name:
        return None
    text = str(name)
    text = _PARENS.sub("", text)
    text = _TRAILING_CODE.sub("", text)
    for prefix in _NOISE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = _SPACES.sub("", text).strip()
    return text or str(name).strip()


def segment_of(hour_float):
    """由小时（可含小数）给出粗时段标签。"""
    if hour_float is None:
        return "unknown"
    if BREAKFAST_WINDOW[0] <= hour_float < BREAKFAST_WINDOW[1]:
        return "breakfast"
    if hour_float >= NIGHT_WINDOW_START or hour_float < NIGHT_WINDOW_END:
        return "night"
    return "main"
