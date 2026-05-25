# dataset/content_safety.py
"""
知数知圈 · 内容安全审核 v1

在素材进入标注流水线之前做三道过滤：

  层 1 — 关键词黑名单（毫秒级，本地规则）
           覆盖：违禁词、仇恨言论、色情、政治敏感等
  层 2 — 启发式规则（毫秒级）
           覆盖：重复刷屏、全符号/乱码、超短/超长
  层 3 — LLM 内容审核（可选，按配置开启）
           低置信度样本二次确认，返回 risk_score 0-1

通过所有层才允许进入流水线，任意一层拒绝即返回拒绝原因。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════════

# 是否启用 LLM 二次审核（需要 OPENAI_API_KEY，会有额外延迟/费用）
_LLM_SAFETY_ENABLED = os.environ.get("ENABLE_LLM_SAFETY", "false").lower() == "true"

# 内容长度限制
_MIN_CONTENT_LEN = 10       # 少于此直接拒绝
_MAX_CONTENT_LEN = 100_000  # 超过 10 万字符拒绝（防超大文件绕过）

# 重复检测：连续相同字符超过此比例视为刷屏
_REPEAT_RATIO_THRESHOLD = 0.5

# ════════════════════════════════════════════════════════════════════
# 层 1 — 关键词黑名单
# ════════════════════════════════════════════════════════════════════

# 分类管理，方便后续运营调整
_KEYWORD_CATEGORIES = {
    "violence": [
        "制造炸弹", "爆炸物配方", "毒气合成", "自制武器", "生化武器",
        "暗杀教程", "伤害他人", "人身攻击方法",
    ],
    "illegal": [
        "制毒方法", "冰毒合成", "贩毒渠道", "洗钱教程", "诈骗脚本",
        "黑产变现", "非法入侵", "盗取账号",
    ],
    "privacy": [
        "身份证号泄露", "银行卡信息", "开房记录查询", "人肉搜索",
        "个人隐私数据库", "手机号实名查询",
    ],
    "political_sensitive": [
        # 保守列表，仅覆盖明显违规内容，不误杀正常讨论
        "颠覆政府", "武装暴乱", "分裂国家",
    ],
    "spam": [
        "加微信返利", "日赚万元", "内部消息炒股", "一夜暴富",
        "免费领取红包", "点击链接领奖", "私聊发链接",
    ],
}

# 展平为列表，编译正则（避免运行时重复编译）
_FLAT_KEYWORDS: List[Tuple[str, str]] = []   # [(category, keyword), ...]
for _cat, _kws in _KEYWORD_CATEGORIES.items():
    for _kw in _kws:
        _FLAT_KEYWORDS.append((_cat, _kw))

# ════════════════════════════════════════════════════════════════════
# 数据类
# ════════════════════════════════════════════════════════════════════

@dataclass
class SafetyResult:
    passed:      bool
    risk_score:  float          # 0.0 = 完全安全，1.0 = 确定违规
    category:    str            # "" | "violence" | "illegal" | "privacy" | ...
    reason:      str            # 人类可读的拒绝原因
    layer:       str            # "keyword" | "heuristic" | "llm" | "pass"
    details:     dict           # 附加信息（命中关键词等）


# ════════════════════════════════════════════════════════════════════
# 各层实现
# ════════════════════════════════════════════════════════════════════

def _layer1_keyword(content: str) -> Optional[SafetyResult]:
    """关键词黑名单匹配，命中即拒绝。"""
    for category, keyword in _FLAT_KEYWORDS:
        if keyword in content:
            return SafetyResult(
                passed=False,
                risk_score=1.0,
                category=category,
                reason=f"内容包含违禁词（{category}类）",
                layer="keyword",
                details={"matched_keyword": keyword, "category": category},
            )
    return None  # 通过


def _layer2_heuristic(content: str) -> Optional[SafetyResult]:
    """启发式规则检测。"""

    # 1. 长度检查
    if len(content) < _MIN_CONTENT_LEN:
        return SafetyResult(
            passed=False, risk_score=0.3, category="quality",
            reason=f"内容过短（{len(content)} 字符），无实际价值",
            layer="heuristic", details={"length": len(content)},
        )
    if len(content) > _MAX_CONTENT_LEN:
        return SafetyResult(
            passed=False, risk_score=0.2, category="quality",
            reason=f"内容超过最大长度限制（{len(content)} > {_MAX_CONTENT_LEN}）",
            layer="heuristic", details={"length": len(content)},
        )

    # 2. 重复刷屏检测（最常见字符占比）
    if len(content) > 50:
        from collections import Counter
        counts = Counter(content)
        most_common_char, most_common_cnt = counts.most_common(1)[0]
        ratio = most_common_cnt / len(content)
        if ratio > _REPEAT_RATIO_THRESHOLD and most_common_char not in (' ', '\n'):
            return SafetyResult(
                passed=False, risk_score=0.6, category="spam",
                reason=f"内容疑似重复刷屏（字符 '{most_common_char}' 占比 {ratio:.0%}）",
                layer="heuristic",
                details={"repeat_char": most_common_char, "repeat_ratio": round(ratio, 3)},
            )

    # 3. 乱码检测（可见字符比例过低）
    printable = sum(1 for c in content if c.isprintable())
    if len(content) > 20 and printable / len(content) < 0.6:
        return SafetyResult(
            passed=False, risk_score=0.5, category="quality",
            reason="内容疑似乱码（可打印字符比例过低）",
            layer="heuristic",
            details={"printable_ratio": round(printable / len(content), 3)},
        )

    # 4. URL 炸弹（超过 5 个不同 URL 视为垃圾链接）
    urls = re.findall(r'https?://\S+', content)
    if len(urls) > 5:
        return SafetyResult(
            passed=False, risk_score=0.7, category="spam",
            reason=f"内容包含过多外链（{len(urls)} 个），疑似垃圾信息",
            layer="heuristic",
            details={"url_count": len(urls)},
        )

    return None  # 通过


async def _layer3_llm(content: str) -> Optional[SafetyResult]:
    """LLM 二次审核（仅在 ENABLE_LLM_SAFETY=true 时生效）。"""
    if not _LLM_SAFETY_ENABLED:
        return None

    try:
        import aiohttp
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import get_settings
        s = get_settings()
        api_key  = s.openai_api_key
        base_url = s.openai_base_url
        model    = s.openai_model
    except Exception:
        return None

    if not api_key:
        return None

    system = (
        "你是内容安全审核员。判断以下文本是否违规。"
        "违规类型：violence / illegal / privacy / political / spam / none"
        "输出严格 JSON，不含 markdown：{\"safe\":true/false,\"category\":\"...\",\"reason\":\"...\",\"score\":0-1}"
    )
    prompt = f"审核内容（前 500 字）：\n{content[:500]}"

    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 128,
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{base_url}/chat/completions",
                headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                raw  = data["choices"][0]["message"]["content"].strip()
                raw  = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
                import json
                result = json.loads(raw)
                if not result.get("safe", True):
                    return SafetyResult(
                        passed=False,
                        risk_score=float(result.get("score", 0.8)),
                        category=result.get("category", "unknown"),
                        reason=result.get("reason", "LLM 审核拒绝"),
                        layer="llm",
                        details=result,
                    )
    except Exception as e:
        print(f"⚠️  [content_safety] LLM 审核失败（降级放行）: {e}")

    return None  # LLM 失败或通过，放行


# ════════════════════════════════════════════════════════════════════
# 统一入口
# ════════════════════════════════════════════════════════════════════

async def check(content: str, content_type: str = "text") -> SafetyResult:
    """
    对一条素材内容做三层安全审核。

    Args:
        content:      原始内容字符串
        content_type: text / image / audio / video
                      非 text 类型只做启发式检查，跳过关键词

    Returns:
        SafetyResult.passed = True 表示安全，可进入流水线
    """
    # 非文本类型暂只做长度检查
    if content_type != "text":
        if len(content) < 4:
            return SafetyResult(
                passed=False, risk_score=0.2, category="quality",
                reason="非文本素材内容为空", layer="heuristic", details={},
            )
        return SafetyResult(
            passed=True, risk_score=0.0, category="",
            reason="", layer="pass", details={},
        )

    # 层 1 关键词
    result = _layer1_keyword(content)
    if result:
        return result

    # 层 2 启发式
    result = _layer2_heuristic(content)
    if result:
        return result

    # 层 3 LLM（可选）
    result = await _layer3_llm(content)
    if result:
        return result

    return SafetyResult(
        passed=True, risk_score=0.0, category="",
        reason="", layer="pass", details={},
    )


def check_sync(content: str, content_type: str = "text") -> SafetyResult:
    """同步版本（用于非 async 上下文）。"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有事件循环中，只跑前两层（跳过 LLM）
            r = _layer1_keyword(content)
            if r:
                return r
            r = _layer2_heuristic(content)
            if r:
                return r
            return SafetyResult(passed=True, risk_score=0.0, category="",
                                reason="", layer="pass", details={})
        return loop.run_until_complete(check(content, content_type))
    except RuntimeError:
        return asyncio.run(check(content, content_type))


# ── 批量检查 ──────────────────────────────────────────────────────

async def batch_check(
    items: list,          # [{"content": str, "content_type": str}, ...]
    concurrency: int = 10,
) -> List[SafetyResult]:
    """并发批量审核，返回与 items 等长的 SafetyResult 列表。"""
    import asyncio
    sem = asyncio.Semaphore(concurrency)

    async def _one(item):
        async with sem:
            return await check(item.get("content", ""), item.get("content_type", "text"))

    return await asyncio.gather(*[_one(i) for i in items])
