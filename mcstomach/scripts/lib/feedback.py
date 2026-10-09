"""用餐反馈：增量更新规则，保留原依据。

铁律：
- 一顿饭不能抹掉全部偏好；
- 原 evidence 永不覆盖，只追加 history；
- 只有同方向累计 >= 3 次，才允许把该条规则退休。
"""

from datetime import datetime, timezone, timedelta

from . import privacy, schema

CN_TZ = timezone(timedelta(hours=8))
RETIRE_THRESHOLD = 3


def _iso(now=None):
    moment = now or datetime.now(CN_TZ)
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return moment.isoformat(timespec="seconds")


def _downgrade(confidence):
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return confidence


def apply_feedback(profile, rule_id=None, signal="negative", note="",
                   add_constraint=None, now=None):
    """就地更新 profile 的副本，返回 (profile, summary)。"""
    import copy

    profile = copy.deepcopy(profile)
    stamp = _iso(now)
    summary = {"updated": [], "added": [], "retired": []}

    clean_note, scrub_hits, injection = privacy.sanitize_user_text(note or "")

    if rule_id:
        target = next((r for r in profile.get("rules", []) if r.get("id") == rule_id), None)
        if target is None:
            summary["error"] = f"找不到规则 {rule_id}"
            return profile, summary
        prev_confidence = target.get("confidence")
        entry = {
            "at": stamp,
            "signal": signal,
            "note": clean_note,
            "prev_confidence": prev_confidence,
        }
        if scrub_hits:
            entry["note_scrubbed"] = scrub_hits
        if injection:
            entry["injection_flagged"] = injection
        target.setdefault("history", []).append(entry)
        summary["updated"].append(rule_id)

        if signal == "negative":
            target["confidence"] = _downgrade(prev_confidence)
            negatives = sum(
                1 for h in target["history"]
                if h.get("signal") == "negative" and h.get("note") != "已退休"
            )
            if negatives >= RETIRE_THRESHOLD:
                target["status"] = "retired"
                summary["retired"].append(rule_id)
        elif signal == "positive" and prev_confidence == "medium":
            target["confidence"] = "high"

    if add_constraint:
        clean_text, hits, inject = privacy.sanitize_user_text(add_constraint)
        new_rule = {
            "id": f"r_stated_{stamp[:10].replace('-', '')}_{len(profile.get('rules', []))}",
            "text": clean_text,
            "dimension": "hard_avoid",
            "kind": "constraint",
            "evidence": [{
                "type": "user_statement",
                "stat": add_constraint,
                "sample": "用户自述",
                "strength": "user_stated",
                "source": "user_feedback",
            }],
            "confidence": "user_stated",
            "confirmed_by": "user",
            "status": "active",
            "history": [{"at": stamp, "signal": "stated", "note": clean_text}],
        }
        if hits:
            new_rule["history"][0]["note_scrubbed"] = hits
        if inject:
            new_rule["history"][0]["injection_flagged"] = inject
        profile.setdefault("rules", []).append(new_rule)
        summary["added"].append(new_rule["id"])

    profile["updated_at"] = stamp
    summary["level_policy"] = schema.LEVEL_POLICY
    return profile, summary
