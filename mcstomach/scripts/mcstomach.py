#!/usr/bin/env python3
"""mcstomach —— 麦门胃的确定性工具。

只做四类可靠的事：**订单归一、证据计数、白名单导出、结构校验**。
不生成人设、不写文案、不碰 MCP、不下单。

用法：
    python3 mcstomach.py normalize --raw order-list.json --out norm.json
    python3 mcstomach.py distill   --orders norm.json --answers answers.json \
                                   --points points.json --out profile.json
    python3 mcstomach.py validate  --profile profile.json
    python3 mcstomach.py export    --profile profile.json --out ./我的胃
    python3 mcstomach.py feedback  --profile profile.json --rule-id r_x \
                                   --signal negative --note "今天没那么想吃薯条"
    python3 mcstomach.py render-card --card share-card.json --style I \
                                   --image ./我的角色.png --out ./分享/分享胃.html
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import distill as distill_mod  # noqa: E402
from lib import export as export_mod    # noqa: E402
from lib import feedback as feedback_mod  # noqa: E402
from lib import normalize as normalize_mod  # noqa: E402
from lib import render_card as render_card_mod  # noqa: E402
from lib import validate as validate_mod   # noqa: E402
from lib import visual as visual_mod       # noqa: E402
from lib import persona as persona_mod     # noqa: E402


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _dump(payload, path=None):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)


def cmd_normalize(args):
    facts = normalize_mod.normalize(
        _load(args.raw),
        max_returnable=args.max_returnable,
        as_of=args.as_of,
        fetch_state=args.fetch_state,
        amount_unit=args.amount_unit,
    )
    _dump(facts, args.out)
    counts = facts["counts"]
    print(
        "[normalize] 原始 {raw} 笔 → 有效 {valid} 笔，剔除取消 {canceled} 笔，"
        "疑似代点 {group} 笔，可归于本人 {personal} 笔；样本档位 {tier}；"
        "来源状态 {state}".format(
            raw=counts["raw_orders"],
            valid=counts["valid_orders"],
            canceled=counts["excluded_canceled"],
            group=counts["group_suspected_pending"],
            personal=counts["personal_valid_orders"],
            tier=facts["stats"]["tier"],
            state=facts.get("source_state"),
        ),
        file=sys.stderr,
    )
    return 0


def cmd_distill(args):
    facts = _load(args.orders)
    answers = _load(args.answers) if args.answers else {}
    answer_list = answers.get("answers", []) if isinstance(answers, dict) else answers
    points = _load(args.points) if args.points else None
    profile = distill_mod.distill(
        facts, answers=answer_list, points=points, owner_label=args.owner,
    )
    _dump(profile, args.out)
    print(
        "[distill] 规则 {n} 条，待确认问题 {q} 条，样本档位 {tier}，身份 {name}".format(
            n=len(profile["rules"]),
            q=len(profile["unresolved"]),
            tier=profile["sample"]["tier"],
            name=profile["naming"]["primary"] or "（未给身份）",
        ),
        file=sys.stderr,
    )
    return 0


def cmd_validate(args):
    profile = _load(args.profile)
    share_card = _load(args.share_card) if args.share_card else None
    errors, warnings = validate_mod.validate_profile(profile, share_card)
    for item in warnings:
        print(f"[warn] {item}")
    for item in errors:
        print(f"[error] {item}")
    if errors:
        print(f"[validate] 不通过：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"[validate] 通过：0 个错误，{len(warnings)} 个警告")
    return 0


def cmd_export(args):
    profile = _load(args.profile)
    facts = _load(args.orders) if args.orders else None
    if args.public_counting:
        profile.setdefault("sharing", {})["public_counting"] = True
    share_card = export_mod.build_share_card(profile, args.public_counting or None)
    errors, warnings = validate_mod.validate_profile(profile, share_card)
    for item in warnings:
        print(f"[warn] {item}")
    if errors:
        for item in errors:
            print(f"[error] {item}")
        print("[export] 校验未通过，已中止导出。")
        return 1
    paths = export_mod.export(
        profile, args.out, public_counting=args.public_counting or None, facts=facts,
    )
    for label, path in paths.items():
        print(f"[export] {label}: {path}")
    return 0


def cmd_feedback(args):
    profile = _load(args.profile)
    updated, summary = feedback_mod.apply_feedback(
        profile,
        rule_id=args.rule_id,
        signal=args.signal,
        note=args.note or "",
        add_constraint=args.add_constraint,
    )
    if summary.get("error"):
        print(f"[error] {summary['error']}")
        return 1
    _dump(updated, args.out)
    errors, warnings = validate_mod.validate_profile(updated)
    for item in warnings:
        print(f"[warn] {item}")
    for item in errors:
        print(f"[error] {item}")
    print(
        "[feedback] 更新 {u}，新增 {a}，退休 {r}".format(
            u=summary["updated"] or "无",
            a=summary["added"] or "无",
            r=summary["retired"] or "无",
        )
    )
    return 0


def cmd_assign_visual(args):
    """首次分配视觉主题：20% 复古像素隐藏款，80% 毛绒常规。

    一个胃档案只抽一次：profile 里已有合法 visual.theme 就原样沿用，
    改昵称、重导出、重出图都不重抽、不再调用随机源。
    """
    profile = _load(args.profile)
    try:
        updated, summary = visual_mod.assign_visual(profile)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 1
    _dump(updated, args.out)
    errors, warnings = validate_mod.validate_profile(updated)
    for item in warnings:
        print(f"[warn] {item}")
    for item in errors:
        print(f"[error] {item}")
    if errors:
        print("[assign-visual] 校验未通过，输出仅供核对，不得直接使用。")
        return 1
    if summary["reused"]:
        print(f"[assign-visual] 已有结果，原样沿用：{summary['theme']}（未调用随机源）")
    else:
        label = "复古像素隐藏款" if summary["theme"] == "retro_pixel" else "毛绒常规款"
        print(f"[assign-visual] 首次分配完成：{summary['theme']}（{label}）")
    return 0


def cmd_persona(args):
    """可选个性化：饮料称号 / 展示昵称 / 市级装饰候选。

    只写 profile 的 persona 段：不改口味 facts/rules/naming，不动已保存主题。
    context.json 可选字段（只认这些，contactName 等一律不当昵称）：
        nickname / mcd_nickname / host_nickname / city /
        delivery_addresses（完整地址仅在内存解析市级，不落盘） / confirmed_drink
    """
    profile = _load(args.profile)
    context = _load(args.context) if args.context else {}
    if not isinstance(context, dict):
        print("[error] context 必须是 JSON 对象")
        return 1
    try:
        updated, summary = persona_mod.apply_persona(profile, context)
    except ValueError as exc:
        print(f"[error] {exc}")
        return 1
    _dump(updated, args.out)
    errors, warnings = validate_mod.validate_profile(updated)
    for item in warnings:
        print(f"[warn] {item}")
    for item in errors:
        print(f"[error] {item}")
    if errors:
        print("[persona] 校验未通过，输出仅供核对，不得直接使用。")
        return 1
    persona = updated.get("persona") or {}
    print(
        "[persona] 称号候选状态 {status}；昵称 {nick}；城市 {city}".format(
            status=persona.get("drink_candidates_status") or "empty",
            nick=persona.get("nickname") or "（无 → 可询问，可跳过）",
            city=persona.get("city") or "（无）",
        )
    )
    if persona.get("drink_title"):
        print(f"[persona] 已确认称号：{persona['drink_title']}（锚定：{persona.get('drink')}）")
    else:
        print("[persona] 称号尚未确认；候选见 persona.drink_candidates，经本人确认后传 confirmed_drink")
    if summary["changed"]:
        print(f"[persona] 更新字段：{'、'.join(summary['changed'])}")
    else:
        print("[persona] 无变化（沿用已有结果）")
    if args.print_image_prompt:
        print("[persona] ---- 整图生图提示词 · 个性化条目 ----")
        print(persona_mod.image_prompt_block(updated))
    return 0


def cmd_narrative(args):
    """把模型/本人写的昵称、宣言、故事、发现、相处说明写回画像。"""
    profile = _load(args.profile)
    narrative = _load(args.narrative)
    updated, summary = distill_mod.apply_narrative(profile, narrative)
    if summary.get("error"):
        print(f"[error] {summary['error']}")
        print("[error] 画像未被改动。")
        return 1
    _dump(updated, args.out)
    for item in summary.get("applied") or []:
        print(f"[narrative] 写回 {item}")
    for item in summary.get("rejected") or []:
        print(f"[narrative] 拒绝 {item}")
    if not summary.get("applied"):
        print("[narrative] 没有任何字段被写回（检查 narrative.source / primary / statement）")
    errors, warnings = validate_mod.validate_profile(updated)
    for item in warnings:
        print(f"[warn] {item}")
    for item in errors:
        print(f"[error] {item}")
    if errors:
        return 1
    # 有字段被拒绝时也不能当作成功——避免「退出 0 但内容没进去」
    return 1 if summary.get("rejected") else 0


def cmd_render_card(args):
    """把白名单分享卡渲染成单文件个人分享 HTML。

    这是「能力桥」：只负责把已确认的内容排成可发送的单文件，
    不替任何人挑选正式视觉，也不生成图片本身。
    """
    try:
        card = _load(args.card)
        image = render_card_mod.load_image_data_uri(args.image) if args.image else None
        page = render_card_mod.render_card(
            card, args.style, image_data_uri=image, demo=args.demo,
            sample_label=args.sample_label,
        )
    except ValueError as exc:
        print(f"[error] {exc}")
        return 1
    out = os.path.abspath(args.out)
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(
        "[render-card] 风格 {style}；{img}；输出 {out}（{size} 字节，单文件，无外链）".format(
            style=args.style,
            img="已内联角色图" if image else "未给图 → 文字卡",
            out=out,
            size=os.path.getsize(out),
        ),
        file=sys.stderr,
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mcstomach",
        description="麦门胃：订单归一、证据计数、白名单导出、结构校验。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("normalize", help="把 order-list 原始返回归一并计数")
    p.add_argument("--raw", required=True, help="order-list 的返回 JSON（或其中的 list）")
    p.add_argument("--max-returnable", type=int, default=10, help="order-list 单次上限，默认 10")
    p.add_argument("--as-of", help="计算「距今多少天」的基准时间，默认现在")
    p.add_argument("--fetch-state", default="ok", choices=["ok", "not_connected", "error"],
                   help="本次取数状态：ok / not_connected / error（不把故障判成零消费）")
    p.add_argument("--amount-unit",
                   help="金额单位（如 CNY）。**未核实就别传**，不传则不给任何金额结论")
    p.add_argument("--out", help="输出路径；省略则打印到 stdout")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("distill", help="由归一事实生成 profile.json")
    p.add_argument("--orders", required=True, help="normalize 的输出")
    p.add_argument("--answers", help="备用问卷答案 JSON")
    p.add_argument("--points", help="query-my-account 的返回 JSON（只读 accumulativePoint）")
    p.add_argument("--owner", default="我", help="画像主人的展示标签，默认「我」")
    p.add_argument("--out", help="输出路径；省略则打印到 stdout")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("validate", help="校验 profile.json（可选带分享卡）")
    p.add_argument("--profile", required=True)
    p.add_argument("--share-card", help="可选：分享卡 JSON，一并校验白名单")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("export", help="导出私密画像 + 个人 SKILL.md + 证书 + 分享卡")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True, help="输出目录")
    p.add_argument("--orders", help="可选：归一事实，用于附上执行证据")
    p.add_argument("--public-counting", action="store_true",
                   help="公开卡是否显示积分数值；默认不显示，只显示称号")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("feedback", help="用餐反馈：增量更新并保留原依据")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rule-id", help="要更新的规则 id")
    p.add_argument("--signal", choices=["negative", "positive"], default="negative")
    p.add_argument("--note", help="反馈原文（会做隐私清洗与注入标记）")
    p.add_argument("--add-constraint", dest="add_constraint",
                   help="本人明确声明的新约束（如「以后不要香菜」）")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser(
        "assign-visual",
        help="首次分配视觉主题（20%% 复古像素隐藏款 / 80%% 毛绒常规）；已有结果原样沿用",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_assign_visual)

    p = sub.add_parser(
        "persona",
        help="可选个性化：饮料称号 / 展示昵称 / 市级装饰（只写 persona 段，不影响主题与口味）",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--context", help="可选个性化输入 JSON（显式字段；contactName 等不当昵称）")
    p.add_argument("--out", required=True)
    p.add_argument("--print-image-prompt", action="store_true",
                   help="同时打印整张生图提示词里的个性化条目（主题/主标题/署名/城市装饰/隐私禁项）")
    p.set_defaults(func=cmd_persona)

    p = sub.add_parser(
        "narrative",
        help="把模型/本人写的昵称与宣言写回画像（引用的规则 id 必须真实存在）",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--narrative", required=True, help="模型生成的叙事 JSON")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_narrative)

    p = sub.add_parser(
        "render-card",
        help="把 share-card.json 渲染成单文件个人分享 HTML（可内联角色图）",
    )
    p.add_argument("--card", required=True, help="export 产出的 share-card.json（白名单字段）")
    p.add_argument("--style", required=True, choices=list(render_card_mod.STYLE_IDS),
                   help="方向 ID：I 为当前推荐（通用名「被这一口治愈」）；"
                        "J 为按用户参考追加的手绘版（沿用 I 的信息结构）；A / B / C 兼容但不再推荐")
    p.add_argument("--image", help="已由生图能力产出的本地 PNG/JPEG；缺省则输出文字卡")
    p.add_argument("--out", required=True, help="输出的 HTML 文件路径（单文件）")
    p.add_argument("--demo", action="store_true",
                   help="演示/测试运行：卡上标注虚构标记，不冒充某人真实数据")
    p.add_argument("--sample-label", help="自定义虚构标记文案（仅在 --demo 时生效）")
    p.set_defaults(func=cmd_render_card)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
