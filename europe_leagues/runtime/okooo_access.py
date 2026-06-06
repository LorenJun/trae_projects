from __future__ import annotations

import re


_TEXT_MARKERS = (
    "访问被阻断",
    "安全威胁",
    "您的访问被阻断",
    "sorry, your request has been blocked",
    "<title>405</title>",
    "请进行验证",
    "滑动到最右边",
    "拖动滑块",
    "请按住滑块",
    "验证码",
)

_HTML_MARKERS = (
    "captcha",
    "verify",
    "verification",
    "slider",
    "geetest",
    "aliyun",
    "nc_",
    "vcode",
)


def is_okooo_blocked_text(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    compact = re.sub(r"\s+", "", lowered)
    if any(marker in raw for marker in _TEXT_MARKERS[:5]):
        return True
    if any(marker in compact for marker in ("请进行验证", "滑动到最右边", "拖动滑块", "请按住滑块", "验证码")):
        return True
    if any(marker in lowered for marker in _TEXT_MARKERS[5:]):
        return True
    if "<canvas" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if "<iframe" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if "<img" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if re.search(r"<img[^>]+(?:width|height)\s*=\s*['\"]?(?:2\d\d|[3-9]\d\d)", lowered):
        if any(marker in lowered for marker in _HTML_MARKERS):
            return True
    if re.search(r"style\s*=\s*['\"][^'\"]*(?:width|height)\s*:\s*(?:2\d\d|[3-9]\d\d)px", lowered):
        if any(marker in lowered for marker in _HTML_MARKERS):
            return True
    return False
