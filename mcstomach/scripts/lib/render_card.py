"""把通过校验的白名单分享卡渲染成**单文件**个人分享 HTML。

设计边界（对应「分享成品闭环」的要求）：
- 数据源只能是 `share-card.json`（白名单字段）。传进来完整 profile 会被明确拒绝；
- 所有文本走 `html.escape`，图片 base64 内联，**零网络请求**；
- 默认**不渲染日期**（`generated_date` 即使存在也不上卡），不带金额／地址／订单 ID；
- 缺角色图时输出**明确的文字卡**，绝不借用包内固定鱼堡头像冒充某个人的专属角色；
- 页面里不出现开发术语，也不画二维码。

这个命令只是「能力桥」：它负责把已确认的内容排成可发送的单文件卡。
`I`（通用风格名「被这一口治愈」）是当前推荐方向；`J` 是按用户参考追加的手绘版，
**沿用 I 的整块信息结构**，只换字体/色板/边线；`A/B/C` 是更早的版式候选，**兼容但不再推荐**。
"""

import base64
import html
import os

from . import privacy, schema

STYLE_IDS = ("A", "B", "C", "I", "J")

#: 目前唯一\"已选定\"的方向。渲染器不替任何人挑风格，只是把这件事记下来。
RECOMMENDED_STYLE = "I"

#: 单张图的体积上限，防止把超长 base64 塞进 HTML。
MAX_IMAGE_BYTES = 8 * 1024 * 1024

TYPE_FALLBACK = "本次口味（样本不足）"

#: 只要出现这些顶层键，就说明传进来的是 profile.json 而不是分享卡。
PROFILE_ONLY_KEYS = (
    "rules", "observations", "narrative", "certificate", "sample",
    "facts", "unresolved", "naming", "level", "sharing", "notes",
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

#: 形态说明：让「这个类型是怎么来的」有交代，且不把话说满。
_KIND_NOTES = {
    "orders": "类型由订单推断，样本范围见上。",
    "stated": "类型来自本人自述，不是订单推断。",
    "snapshot": "样本还不够形成稳定类型，这张只描述这次的口味。",
}

#: I 方向：卡内必须自己讲清楚的**样本局限**（截图之后仍在，不靠卡外附注）。
_I_LIMITS = {
    "orders": "只统计本人已完成的订单，且最多只看到最近 10 笔；没点过 ≠ 不喜欢。",
    "stated": "类型来自本人自述，不是订单推断，样本可能不足以代表全部口味。",
    "snapshot": "样本还不足以形成稳定类型，这张只描述这次看到的口味。",
}

#: stated **但这次确实读到了订单**（orders_seen > 0）时换这句。
#: 真人卡上同时有「本人认领的昵称」和「订单算出的偏好」，
#: 若还写「类型来自本人自述，不是订单推断」，会让人误以为这张卡没接过订单。
_I_LIMITS_STATED_WITH_ORDERS = "昵称与部分偏好经本人确认；样本不代表全部口味。"

#: I 方向的固定邀请语。
_I_INVITE = "把你的胃发来，看看能不能拼一桌"
_I_FOOT_IMAGE = "插画为 AI 生成的原创角色 · 娱乐性解读，非人格诊断"
_I_FOOT_TEXT = "文字版分享卡 · 未使用任何他人的角色插画"

#: 缺图/0 单时的标题（不贴稳定类型，也不留「待生成」大洞）。
_I_EMPTY_TITLE = "胃还没开张"
_I_EMPTY_SUB = "这次接口没有返回订单记录。可以补上订单，或者先用自述告诉我你的口味。"

#: 「有订单但类型还没成立」时的标题——**绝不是「还没开张」**。
_I_PARTIAL_TITLE = "本次口味"

_DEMO_BADGE = "虚构示例 · 仅测试"

_PLACEHOLDER = (
    '<div class="ph">'
    "<b>专属角色插画 · 待生成</b>"
    "<span>这一版先用文字卡；等你的角色图生成，这里就是它的位置。</span>"
    "</div>"
)

_FOOT_WITH_IMAGE = "插画为 AI 生成的原创卡通 · 娱乐性解读"
_FOOT_WITHOUT_IMAGE = "文字版分享卡 · 未使用任何他人的角色插画"


# ------------------------------------------------------------------ 基础工具

def _pkg_root():
    """skill 包根目录（scripts/lib/ -> 上三级）。解压到新目录也照样成立。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _esc(value):
    """任何进入 HTML 的文本都必须先转义。换行折成空格，避免排版被撑破。"""
    if value is None:
        return ""
    return html.escape(str(value).replace("\r", " ").replace("\n", " "), quote=True)


def _logo_data_uri():
    """金拱门用官方原图内联，不重绘、不拉网络。缺文件就返回 None。"""
    path = os.path.join(_pkg_root(), "assets", "brand", "麦当劳官网标志.png")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        raw = handle.read()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _logo_tag():
    uri = _logo_data_uri()
    if not uri:
        return ""
    return '<img src="%s" alt="金拱门">' % uri


def load_image_data_uri(path):
    """把本地 PNG / JPEG 读成 data URI。只认这两种格式，且限体积。"""
    if not os.path.isfile(path):
        raise ValueError("找不到图片文件：" + str(path))
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        raise ValueError(
            "图片过大（%.1f MB），上限 %d MB；请先压一下再传。"
            % (size / 1024 / 1024, MAX_IMAGE_BYTES // 1024 // 1024)
        )
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(_PNG_MAGIC):
        mime = "image/png"
    elif raw.startswith(_JPEG_MAGIC):
        mime = "image/jpeg"
    else:
        raise ValueError("只接受 PNG / JPEG 图片；其它格式请先转码。")
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def guard_card(card):
    """确认拿到的是分享卡，而不是混进私密字段的 profile。不通过就抛错。"""
    if not isinstance(card, dict):
        raise ValueError("分享卡必须是一个 JSON 对象。")
    leaked = [key for key in PROFILE_ONLY_KEYS if key in card]
    if leaked:
        raise ValueError(
            "这看起来是 profile.json，不是 share-card.json（含私密字段："
            + "、".join(leaked) + "）。分享卡请用 export 产出的 share-card.json。"
        )
    if card.get("schema") != schema.SHARE_CARD_SCHEMA_ID:
        raise ValueError(
            "schema 不是 %s，拒绝渲染。" % schema.SHARE_CARD_SCHEMA_ID
        )
    over = privacy.check_whitelist(card)
    if over:
        raise ValueError("分享卡出现白名单外字段：" + "、".join(over))


# ------------------------------------------------------------------ 片段拼装

def _kind_note(card):
    """形态说明的**统一出口**：卡内 `.i-limit` 与卡外附注必须同一句，不能打架。

    `stated` 且**确实读到了订单**（orders_seen > 0）时，
    真人卡上的偏好一部分是订单算出来的，就不能再说「不是订单推断」。
    """
    if card.get("card_kind") == "stated" and int(card.get("orders_seen") or 0) > 0:
        return _I_LIMITS_STATED_WITH_ORDERS
    return None


def _art(card, image_data_uri):
    if image_data_uri:
        return '<img src="%s" alt="%s">' % (
            image_data_uri, _esc(card.get("type") or "角色插画"),
        )
    return _PLACEHOLDER


def _foot(card, image_data_uri):
    return '<div class="foot">%s<span>%s</span></div>' % (
        _logo_tag(),
        _esc(_FOOT_WITH_IMAGE if image_data_uri else _FOOT_WITHOUT_IMAGE),
    )


def _card_a(card, image_data_uri, badge):
    name = card.get("type") or TYPE_FALLBACK
    parts = ['<section class="card cardA">']
    parts.append('<div class="a-top"><span>我的麦门胃</span>%s</div>' % badge)
    parts.append('<div class="art">%s</div>' % _art(card, image_data_uri))
    parts.append('<div class="a-name">%s</div>' % _esc(name))
    if card.get("declaration"):
        parts.append('<p class="a-decl">%s</p>' % _esc(card["declaration"]))
    if card.get("evidence_line"):
        parts.append('<div class="a-basis">%s</div>' % _esc(card["evidence_line"]))
    if card.get("how_to_use"):
        parts.append('<div class="a-rule"><b>怎么用</b>%s</div>' % _esc(card["how_to_use"]))
    parts.append('<div class="spacer"></div>')
    if card.get("challenge"):
        parts.append('<div class="a-invite">%s</div>' % _esc(card["challenge"]))
    parts.append(_foot(card, image_data_uri))
    parts.append("</section>")
    return "".join(parts)


def _card_b(card, image_data_uri, badge):
    name = card.get("type") or TYPE_FALLBACK
    parts = ['<section class="card cardB">']
    parts.append(
        '<div class="b-ribbon"><span class="t">麦门角色收藏卡</span>%s</div>' % badge
    )
    parts.append('<div class="art">%s</div>' % _art(card, image_data_uri))
    parts.append('<div class="b-plate"><span>%s</span></div>' % _esc(name))
    meta_left = "口味记录 · 图鉴版"
    meta_right = card.get("level_label") or ""
    parts.append(
        '<div class="b-meta"><span>%s</span><span>%s</span></div>'
        % (_esc(meta_left), _esc(meta_right))
    )
    if card.get("evidence_line"):
        parts.append('<div class="b-basis">依据 · %s</div>' % _esc(card["evidence_line"]))
    if card.get("declaration"):
        parts.append('<p class="b-decl">%s</p>' % _esc(card["declaration"]))
    if card.get("how_to_use"):
        parts.append('<div class="b-basis">怎么用 · %s</div>' % _esc(card["how_to_use"]))
    parts.append('<div class="spacer"></div>')
    if card.get("challenge"):
        parts.append('<div class="b-invite">%s</div>' % _esc(card["challenge"]))
    parts.append(_foot(card, image_data_uri))
    parts.append("</section>")
    return "".join(parts)


def _card_c(card, image_data_uri, badge):
    name = card.get("type") or TYPE_FALLBACK
    parts = ['<section class="card cardC">']
    parts.append('<div class="scene">')
    if image_data_uri:
        parts.append(
            '<img src="%s" alt="%s">' % (image_data_uri, _esc(name))
        )
        parts.append('<div class="scrim"></div>')
    else:
        parts.append(_PLACEHOLDER)
    parts.append(
        '<div class="c-top"><span class="banner">%s</span>%s</div>' % (_esc(name), badge)
    )
    if card.get("declaration"):
        parts.append('<div class="bubble">%s</div>' % _esc(card["declaration"]))
    parts.append("</div>")
    parts.append('<div class="c-panel">')
    if card.get("evidence_line"):
        parts.append(
            '<div class="c-basis">依据 · %s</div>' % _esc(card["evidence_line"])
        )
    if card.get("how_to_use"):
        parts.append('<div class="c-rule">%s</div>' % _esc(card["how_to_use"]))
    if card.get("challenge"):
        parts.append('<div class="c-invite">%s</div>' % _esc(card["challenge"]))
    parts.append(_foot(card, image_data_uri))
    parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)


def _card_i(card, image_data_uri, badge):
    """I · 被这一口治愈：360×480 竖版，上半场景、下半暖白信息区。

    两条硬规矩：
    - **样本边界必须在卡内**——`source_range` 与 `_I_LIMITS` 都排在 `.cardI` 里面，
      截图之后依然在，不靠卡外附注；
    - **缺图不给「待生成」大洞**：整张卡退化成排版完整的文字卡，标题走「胃还没开张」，
      且**永远不借用包里任何一张固定角色图**。
    """
    name = card.get("type") or TYPE_FALLBACK
    kind = card.get("card_kind")

    parts = ['<section class="card cardI">']

    if image_data_uri:
        parts.append('<div class="i-scene">')
        parts.append('<img src="%s" alt="%s">' % (image_data_uri, _esc(name)))
        parts.append(
            '<div class="i-top"><span class="i-strip">我的麦门胃</span>%s</div>' % badge
        )
        parts.append("</div>")
    else:
        # 文字卡：没有大洞，也没有别人的角色图。
        # **有订单 ≠ 没开张**：只有 1 笔真实订单时，类型还没成立，
        # 但这也绝不是「胃还没开张」——两者必须分开说。
        seen = int(card.get("orders_seen") or 0)
        if card.get("type"):
            head = card["type"]
            sub = "角色插画还没生成，这一版先走文字。"
            if seen:
                sub += "依据是 %d 笔可归于本人的订单。" % seen
        elif seen:
            head = _I_PARTIAL_TITLE
            sub = (
                "这次读到 %d 笔订单，还不够形成稳定类型；"
                "角色插画还没生成，这一版先走文字。" % seen
            )
        else:
            head, sub = _I_EMPTY_TITLE, _I_EMPTY_SUB
        parts.append('<div class="i-empty">')
        parts.append(
            '<div class="i-top"><span class="i-strip">我的麦门胃</span>%s</div>' % badge
        )
        parts.append('<p class="i-et">%s</p>' % _esc(head))
        parts.append('<p class="i-es">%s</p>' % _esc(sub))
        parts.append("</div>")

    parts.append('<div class="i-body">')
    if card.get("type"):
        parts.append('<div class="i-name">%s</div>' % _esc(card["type"]))
    if card.get("declaration"):
        parts.append('<p class="i-decl">%s</p>' % _esc(card["declaration"]))
    if card.get("evidence_line"):
        parts.append('<div class="i-basis">%s</div>' % _esc(card["evidence_line"]))
    if card.get("order_rule"):
        parts.append(
            '<div class="i-rule"><b>点餐规则</b>%s</div>' % _esc(card["order_rule"])
        )
    elif card.get("how_to_use"):
        # 没有真实选餐建议时，栏目就叫「怎么用」——不许把文件使用说明冒充点餐规则。
        parts.append(
            '<div class="i-rule"><b>怎么用</b>%s</div>' % _esc(card["how_to_use"])
        )

    limits = []
    if card.get("source_range"):
        limits.append("来源：" + str(card["source_range"]))
    note = _kind_note(card) or _I_LIMITS.get(kind)
    if note:
        limits.append(note)
    if limits:
        parts.append('<div class="i-limit">%s</div>' % _esc("　".join(limits)))

    parts.append('<div class="i-spacer"></div>')
    parts.append('<div class="i-invite">%s</div>' % _esc(_I_INVITE))
    parts.append(
        '<div class="i-foot">%s<span>%s</span></div>'
        % (_logo_tag(), _esc(_I_FOOT_IMAGE if image_data_uri else _I_FOOT_TEXT))
    )
    parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)


def _card_j(card, image_data_uri, badge):
    """J · 手绘追加版：**沿用 I 的整块信息结构**，只换外壳类名。

    结构、字段、样本边界全部照搬 I（同一份白名单、同一套 `.i-*` 元素），
    由 `.cardJ` 这层 CSS 覆盖字体/色板/边线，去配合扁平手绘插画。
    这样两份卡不会因为「版式不同」而在证据与边界上产生分歧。
    """
    html = _card_i(card, image_data_uri, badge)
    return html.replace(
        '<section class="card cardI">', '<section class="card cardI cardJ">', 1
    )


def _notes(card):
    """卡外的附注：只写有依据的，没有就不写。"""
    items = []
    if card.get("source_range"):
        items.append("来源：" + _esc(card["source_range"]))
    label = card.get("level_label") or ""
    note = card.get("level_disclaimer") or ""
    if label or note:
        items.append("段位：%s —— %s" % (_esc(label), _esc(note)))
    kind_note = _kind_note(card) or _KIND_NOTES.get(card.get("card_kind"))
    if kind_note:
        items.append(_esc(kind_note))
    if card.get("disclaimer"):
        items.append(_esc(card["disclaimer"]))
    return "".join("<p>%s</p>" % item for item in items)


# --------------------------------------------------------------------- 渲染

def render_card(card, style, image_data_uri=None, demo=False, sample_label=None):
    """把分享卡渲染成单文件 HTML 字符串。

    :param card: share-card.json 的内容（白名单字段）
    :param style: "A" / "B" / "C" / "I"，显式选择的方向（I 为当前推荐）
    :param image_data_uri: 已生成的角色图（data URI）；None 表示输出文字卡
    :param demo: 演示/测试运行时加虚构标记，不冒充某人真实数据
    :param sample_label: 自定义虚构标记文案；仅在 demo=True 时生效
    """
    if style not in STYLE_IDS:
        raise ValueError("风格只能是 %s 之一。" % " / ".join(STYLE_IDS))
    guard_card(card)

    badge = ""
    if demo:
        badge = '<span class="badge-demo">%s</span>' % _esc(sample_label or _DEMO_BADGE)
    builders = {"A": _card_a, "B": _card_b, "C": _card_c, "I": _card_i, "J": _card_j}
    body = builders[style](card, image_data_uri, badge)

    title = (card.get("type") or "麦门胃") + " · 麦门胃"
    out = (
        _PAGE
        .replace("__CSS__", _CSS)
        .replace("__TITLE__", _esc(title))
        .replace("__CARD__", body)
        .replace("__NOTES__", _notes(card))
    )
    if "http://" in out or "https://" in out:
        raise ValueError("生成的 HTML 里出现了外链，会破坏单文件离线可用。")
    return out


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
__CARD__
<div class="notes">__NOTES__</div>
</body>
</html>
"""

_CSS = """
:root{
  --bg:#F3EDE2;--ink:#231F1C;--ink-soft:#7C7268;--hairline:rgba(35,31,28,.16);
  --mcd-red:#DA291C;--mcd-red-deep:#A8160C;--mcd-yellow:#FFC72C;--mcd-yellow-soft:#FFF0C4;--cream:#FFF9F1;
  --font-round:"Yuanti SC","YouYuan","Hiragino Maru Gothic ProN","PingFang SC","Hiragino Sans GB",sans-serif;
  --font-hei:"PingFang SC","Hiragino Sans GB","Heiti SC","Microsoft YaHei",sans-serif;
  --font-mono:"SF Mono","Menlo","Consolas","Courier New",monospace;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);font-family:var(--font-hei);-webkit-font-smoothing:antialiased;
  display:flex;flex-direction:column;align-items:center;gap:20px;padding:34px 12px 40px;min-height:100vh;}
.card{width:100%;max-width:420px;position:relative;overflow:hidden;}
.badge-demo{font-family:var(--font-mono);font-size:9px;letter-spacing:.1em;border:1px dashed rgba(35,31,28,.35);
  padding:3px 7px;border-radius:999px;color:#6E6459;background:rgba(255,255,255,.7);white-space:nowrap;}
.ph{width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;
  padding:22px;text-align:center;border:2px dashed rgba(218,41,28,.32);border-radius:14px;
  background:repeating-linear-gradient(135deg,rgba(218,41,28,.045) 0 11px,rgba(255,199,44,.10) 11px 22px);}
.ph b{font-family:var(--font-round);font-size:14.5px;color:var(--mcd-red-deep);}
.ph span{font-size:11.5px;line-height:1.75;color:#7C7268;max-width:250px;}
.foot{display:flex;align-items:center;justify-content:center;gap:7px;font-size:9.5px;letter-spacing:.05em;}
.foot img{height:14px;width:auto;display:block;}
.notes{width:100%;max-width:420px;font-size:11px;line-height:1.9;color:var(--ink-soft);}
.notes p{margin:0 0 4px;}

/* A · 我的麦门胃 */
.cardA{background:var(--cream);border:3px solid var(--mcd-red);border-radius:22px;padding:14px 16px 13px;
  display:flex;flex-direction:column;min-height:500px;box-shadow:0 14px 30px rgba(168,22,12,.16);}
.cardA .a-top{display:flex;align-items:center;justify-content:space-between;gap:8px;
  font-family:var(--font-round);font-size:12px;font-weight:800;color:var(--mcd-red);letter-spacing:.08em;}
.cardA .art{height:224px;margin:12px 0 0;border-radius:16px;
  background:radial-gradient(circle at 50% 42%,#FFFDF8 0%,#FBF1E1 72%,#F6E8D2 100%);
  display:flex;align-items:center;justify-content:center;overflow:hidden;}
.cardA .art img{width:88%;height:100%;object-fit:contain;display:block;}
.cardA .a-name{font-family:var(--font-round);font-weight:800;font-size:30px;line-height:1.16;margin:14px 0 0;
  color:#1E1A17;letter-spacing:.03em;word-break:break-word;}
.cardA .a-decl{font-size:14px;line-height:1.7;color:#5F564D;margin:8px 0 0;word-break:break-word;}
.cardA .a-basis{align-self:flex-start;margin:12px 0 0;padding:6px 12px;border-radius:999px;
  background:var(--mcd-yellow-soft);border:1px solid rgba(218,41,28,.18);font-size:11.5px;color:#6A5218;
  word-break:break-word;}
.cardA .a-rule{font-size:12px;line-height:1.75;color:#7C7268;margin:12px 0 0;word-break:break-word;}
.cardA .a-rule b{font-family:var(--font-mono);font-size:9px;letter-spacing:.16em;color:var(--mcd-red);
  font-weight:400;display:block;margin-bottom:2px;}
.cardA .spacer{flex:1;min-height:10px;}
.cardA .a-invite{margin:0 -16px;padding:11px 16px;text-align:center;background:var(--mcd-red);color:#fff;
  font-family:var(--font-round);font-weight:800;font-size:16px;letter-spacing:.1em;word-break:break-word;}
.cardA .foot{margin-top:11px;color:var(--ink-soft);}

/* B · 麦门角色收藏卡 */
.cardB{background:linear-gradient(180deg,#FFFBF2 0%,#FFF3D9 100%);border:3px solid var(--mcd-red);
  border-radius:20px;display:flex;flex-direction:column;min-height:500px;box-shadow:0 14px 30px rgba(168,22,12,.18);}
.cardB .b-ribbon{background:var(--mcd-red);color:#fff;display:flex;align-items:center;justify-content:space-between;
  gap:8px;padding:9px 15px;}
.cardB .b-ribbon .t{font-family:var(--font-round);font-weight:800;font-size:13px;letter-spacing:.16em;}
.cardB .b-ribbon .badge-demo{border-color:rgba(255,255,255,.6);color:#FFE9DF;background:rgba(255,255,255,.12);}
.cardB .art{height:196px;margin:13px 15px 0;border-radius:14px;overflow:hidden;
  background:radial-gradient(circle at 50% 45%,#FFFDF6 0%,#FCEFD5 100%);border:1px solid rgba(218,41,28,.14);
  display:flex;align-items:center;justify-content:center;}
.cardB .art img{width:78%;height:100%;object-fit:contain;display:block;}
.cardB .b-plate{background:var(--mcd-yellow);color:var(--mcd-red-deep);margin:14px 15px 0;padding:8px 12px;
  border-radius:10px;transform:skewY(-2deg);text-align:center;}
.cardB .b-plate span{display:block;transform:skewY(2deg);font-family:var(--font-round);font-weight:800;
  font-size:23px;letter-spacing:.08em;word-break:break-word;}
.cardB .b-meta{display:flex;justify-content:space-between;gap:8px;margin:11px 15px 0;font-family:var(--font-mono);
  font-size:10px;letter-spacing:.08em;color:#8A7B5F;}
.cardB .b-basis{margin:12px 15px 0;font-size:12px;line-height:1.7;color:#5F564D;word-break:break-word;}
.cardB .b-decl{margin:10px 15px 0;font-size:12.5px;line-height:1.7;color:#5F564D;word-break:break-word;}
.cardB .spacer{flex:1;min-height:10px;}
.cardB .b-invite{margin:0 15px 11px;padding:10px 12px;border-radius:12px;text-align:center;
  border:2px solid var(--mcd-red);color:var(--mcd-red);font-family:var(--font-round);font-weight:800;
  font-size:15px;letter-spacing:.08em;background:#FFFDF7;word-break:break-word;}
.cardB .foot{color:#8A7B5F;padding-bottom:12px;}

/* C · 胃的餐盘小剧场 */
.cardC{border-radius:22px;background:linear-gradient(160deg,#F6E9D6 0%,#EFD9B8 100%);
  box-shadow:0 14px 32px rgba(120,80,40,.26);display:flex;flex-direction:column;}
.cardC .scene{position:relative;height:330px;overflow:hidden;}
.cardC .scene img{width:100%;height:100%;object-fit:cover;display:block;}
.cardC .scrim{position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(35,31,28,.44) 0%,rgba(35,31,28,0) 34%,rgba(35,31,28,0) 62%,rgba(35,31,28,.12) 100%);}
.cardC .c-top{position:absolute;top:0;left:0;right:0;display:flex;align-items:flex-start;justify-content:space-between;
  gap:8px;padding:14px 15px;}
.cardC .banner{background:var(--mcd-red);color:#fff;font-family:var(--font-round);font-weight:800;font-size:20px;
  letter-spacing:.06em;padding:7px 14px;border-radius:10px;box-shadow:0 4px 0 var(--mcd-red-deep);word-break:break-word;}
.cardC .bubble{position:absolute;left:22px;right:22px;bottom:22px;background:#FFFDF7;border-radius:16px;
  padding:14px 16px;box-shadow:0 10px 24px rgba(60,40,20,.22);font-family:var(--font-round);font-weight:800;
  font-size:16.5px;line-height:1.55;color:#201C19;word-break:break-word;}
.cardC .bubble::after{content:"";position:absolute;left:34px;bottom:-11px;width:0;height:0;
  border-left:11px solid transparent;border-right:11px solid transparent;border-top:12px solid #FFFDF7;}
.cardC .c-panel{background:rgba(255,251,244,.95);padding:15px 17px 13px;}
.cardC .c-basis{font-family:var(--font-mono);font-size:10.5px;letter-spacing:.04em;color:#8A7B5F;word-break:break-word;}
.cardC .c-rule{font-size:12px;line-height:1.75;color:#6E6459;margin:8px 0 0;word-break:break-word;}
.cardC .c-invite{margin:10px 0 0;font-family:var(--font-round);font-weight:800;font-size:15px;color:var(--mcd-red);
  letter-spacing:.06em;word-break:break-word;}
.cardC .foot{margin-top:10px;color:#8A7B5F;}
.cardC .scene .ph{border:0;border-radius:0;height:100%;
  background:repeating-linear-gradient(135deg,rgba(255,255,255,.45) 0 11px,rgba(255,199,44,.34) 11px 22px);}

/* I · 被这一口治愈 —— 360×480 竖版，上半场景／下半暖白信息区（当前推荐方向） */
.cardI{width:360px;max-width:none;min-height:480px;border-radius:20px;background:#FFFCF7;
  display:flex;flex-direction:column;box-shadow:0 16px 36px rgba(120,80,60,.20);}
.cardI .i-scene{position:relative;height:240px;flex:0 0 auto;display:flex;align-items:flex-end;
  justify-content:center;background:linear-gradient(90deg,#E9CBBE 0%,#F5E3DA 50%,#EDD5C9 100%);}
.cardI .i-scene img{height:100%;width:auto;max-width:100%;object-fit:contain;display:block;}
.cardI .i-top{position:absolute;top:0;left:0;right:0;z-index:2;display:flex;align-items:center;
  justify-content:space-between;gap:8px;padding:11px 14px;}
.cardI .i-strip{font-family:var(--font-round);font-weight:800;font-size:11.5px;letter-spacing:.14em;
  color:#7A4A32;background:rgba(255,253,249,.84);border-radius:999px;padding:4px 11px;white-space:nowrap;}
.cardI .i-body{padding:15px 20px 13px;display:flex;flex-direction:column;flex:1;background:#FFFCF7;}
.cardI .i-name{font-family:var(--font-round);font-weight:800;font-size:27px;line-height:1.2;color:#4A2E23;
  letter-spacing:.02em;word-break:break-word;}
.cardI .i-decl{margin:7px 0 0;font-size:13.5px;line-height:1.65;color:#6E5A4E;word-break:break-word;}
.cardI .i-basis{align-self:flex-start;margin:11px 0 0;padding:5px 11px;border-radius:8px;background:#FFEFE6;
  border:1px solid rgba(218,41,28,.16);font-size:11.5px;line-height:1.5;color:#8A4A2E;word-break:break-word;}
.cardI .i-rule{margin:11px 0 0;font-size:12px;line-height:1.7;color:#6E5A4E;word-break:break-word;}
.cardI .i-rule b{display:block;font-family:var(--font-mono);font-size:9px;letter-spacing:.16em;
  color:var(--mcd-red);font-weight:400;margin-bottom:2px;}
.cardI .i-limit{margin:10px 0 0;font-size:10.5px;line-height:1.65;color:#9C8D83;word-break:break-word;}
.cardI .i-spacer{flex:1;min-height:12px;}
.cardI .i-invite{margin:12px 0 0;padding:9px 12px;border-radius:12px;text-align:center;background:#FFF3EC;
  border:1px dashed rgba(218,41,28,.34);font-family:var(--font-round);font-weight:800;font-size:14px;
  letter-spacing:.04em;color:#C4241A;word-break:break-word;}
.cardI .i-foot{margin:10px 0 0;display:flex;align-items:center;justify-content:center;gap:6px;
  font-size:9.5px;line-height:1.5;color:#A2948A;text-align:center;}
.cardI .i-foot img{height:13px;width:auto;display:block;flex:0 0 auto;}
.cardI .i-empty{position:relative;flex:0 0 auto;padding:52px 26px 28px;text-align:center;
  background:linear-gradient(180deg,#F7DCD0 0%,#FBE9E1 100%);}
.cardI .i-et{font-family:var(--font-round);font-weight:800;font-size:24px;line-height:1.3;color:#4A2E23;margin:0;}
.cardI .i-es{margin:9px 0 0;font-size:12.5px;line-height:1.75;color:#7C6A5E;}
/* 窄屏等比缩小：zoom 不接受 calc()，只能分档写死 */
@media (max-width:388px) and (min-width:377px){.cardI{zoom:.96;}}
@media (max-width:376px) and (min-width:361px){.cardI{zoom:.93;}}
@media (max-width:360px) and (min-width:341px){.cardI{zoom:.87;}}
@media (max-width:340px) and (min-width:321px){.cardI{zoom:.80;}}
@media (max-width:320px){.cardI{zoom:.78;}}

/* J · 手绘追加版：沿用 I 的信息结构，只换字体/色板/边线去配扁平手绘插画 */
.cardJ{background:#FFFDF8;border:2px solid #3A3733;border-radius:14px;box-shadow:none;
  font-family:var(--font-round);}
.cardJ .i-scene{background:#FBF7F0;}
.cardJ .i-body{background:#FFFDF8;}
.cardJ .i-name{color:#33302C;letter-spacing:.02em;}
.cardJ .i-decl{color:#6B635A;}
.cardJ .i-strip{background:#FFFDF8;border:1.5px solid #3A3733;color:#3A3733;}
.cardJ .i-basis{background:#FFF6E6;border:1.5px solid #3A3733;border-radius:10px;color:#5A5148;}
.cardJ .i-rule{color:#6B635A;}
.cardJ .i-rule b{color:#C4241A;}
.cardJ .i-limit{color:#9A9088;}
.cardJ .i-invite{background:#FFF6E6;border:1.5px dashed #3A3733;border-radius:10px;color:#C4241A;}
.cardJ .i-foot{color:#9A9088;}
.cardJ .i-empty{background:#FBF7F0;}
.cardJ .i-et{color:#33302C;}
.cardJ .i-es{color:#7A726A;}
"""
