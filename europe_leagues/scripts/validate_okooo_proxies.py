#!/usr/bin/env python3
"""Validate access availability for `m.okooo.com/match/odds.php`.

Features:
- Read proxy entries from a text file.
- Support direct-connect diagnostics without any proxy list.
- Test proxies concurrently against a target `odds.php` URL.
- Reuse the shared Okooo mobile headers/profile helpers from this repo.
- Emit console summary plus optional JSON/CSV artifacts.

Accepted proxy line formats:
- `http://host:port`
- `http://user:pass@host:port`
- `https://host:port`
- `host:port` (treated as `http://host:port`)

Ignored lines:
- blank lines
- lines starting with `#`
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from okooo_mobile_access import (  # noqa: E402
    cache_busted_okooo_url,
    mobile_headers,
    random_mobile_profile,
)


DEFAULT_MATCH_ID = "1302914"
DEFAULT_TIMEOUT = 20.0
SUCCESS_KEYWORDS = ("赔率", "欧赔", "即时", "公司", "澳客")
BLOCK_KEYWORDS = ("访问过于频繁", "验证码", "blocked", "forbidden", "deny", "denied", "waf")
PAGE_MARKERS = ("okooo", "matchid", "odds.php", "<title", "jsglobalversion")


@dataclass(frozen=True)
class ProxyEntry:
    raw: str
    normalized: str
    label: str


@dataclass
class ValidationResult:
    proxy: str
    proxy_label: str
    ok: bool
    status_code: int | None
    elapsed_ms: int
    content_length: int
    keyword_hits: list[str]
    blocked_hits: list[str]
    page_marker_hits: list[str]
    final_url: str
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="验证代理出口或本机直连是否可访问澳客移动端 odds.php 页面"
    )
    parser.add_argument(
        "--proxy-file",
        default="",
        help="代理列表文件路径；每行一个代理，支持 http(s):// 或 host:port",
    )
    parser.add_argument(
        "--direct-only",
        action="store_true",
        help="启用无代理直连诊断；未传 --proxy-file 时默认走该模式",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=5,
        help="直连模式请求次数，默认 5",
    )
    parser.add_argument(
        "--match-id",
        default=DEFAULT_MATCH_ID,
        help=f"目标 MatchID，默认 {DEFAULT_MATCH_ID}",
    )
    parser.add_argument(
        "--url",
        default="",
        help="直接指定目标 URL；若提供则优先于 --match-id",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="并发数，默认 10",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"单请求超时秒数，默认 {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="每个代理最大尝试次数，默认 1",
    )
    parser.add_argument(
        "--ok-min-keywords",
        type=int,
        default=1,
        help="判定成功时至少命中的关键字数量，默认 1",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="可选：保存完整结果到 JSON 文件",
    )
    parser.add_argument(
        "--csv-out",
        default="",
        help="可选：保存结果到 CSV 文件",
    )
    parser.add_argument(
        "--only-ok",
        action="store_true",
        help="控制台仅打印成功代理",
    )
    return parser.parse_args()


def target_url_from_args(args: argparse.Namespace) -> str:
    if args.url:
        return args.url.strip()
    return f"https://m.okooo.com/match/odds.php?MatchID={args.match_id}"


def should_use_direct_mode(args: argparse.Namespace) -> bool:
    return bool(args.direct_only or not args.proxy_file.strip())


def normalize_proxy(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("empty proxy")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported proxy scheme: {parsed.scheme}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("proxy must include host and port")
    return value


def load_proxies(proxy_file: str) -> list[ProxyEntry]:
    path = Path(proxy_file).expanduser().resolve()
    entries: list[ProxyEntry] = []
    seen: set[str] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            normalized = normalize_proxy(line)
        except ValueError as exc:
            print(f"[WARN] 跳过无效代理，第 {line_no} 行: {line} ({exc})", file=sys.stderr)
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        entries.append(ProxyEntry(raw=line, normalized=normalized, label=f"proxy-{len(entries) + 1:03d}"))
    if not entries:
        raise ValueError(f"未从代理文件读取到有效代理: {path}")
    return entries


def keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for item in keywords:
        needle = item.lower()
        if needle in lowered:
            hits.append(item)
    return hits


def is_successful_page(
    *,
    status_code: int,
    body: str,
    keyword_matches: list[str],
    blocked_matches: list[str],
    ok_min_keywords: int,
) -> tuple[bool, list[str]]:
    marker_hits = keyword_hits(body, PAGE_MARKERS)
    ok = (
        status_code == 200
        and not blocked_matches
        and (
            len(keyword_matches) >= ok_min_keywords
            or (len(marker_hits) >= 4 and len(body) >= 10000)
        )
    )
    return ok, marker_hits


def choose_profile():
    return random_mobile_profile()


def decode_response_text(response: requests.Response) -> str:
    declared = (response.encoding or "").lower()
    apparent = (response.apparent_encoding or "").lower()
    content_type = response.headers.get("Content-Type", "")
    header_match = re.search(r"charset=([A-Za-z0-9_-]+)", content_type, re.IGNORECASE)
    header_charset = header_match.group(1) if header_match else ""

    candidates = [
        header_charset,
        response.apparent_encoding or "",
        "gbk",
        "gb2312",
        response.encoding or "",
        "utf-8",
    ]
    if declared == "iso-8859-1" and apparent:
        candidates.insert(0, apparent)

    tried: set[str] = set()
    for candidate in candidates:
        encoding = candidate.strip()
        if not encoding:
            continue
        lowered = encoding.lower()
        if lowered in tried:
            continue
        tried.add(lowered)
        try:
            return response.content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    for fallback in ("gb18030", "gbk", "gb2312", "utf-8"):
        if fallback in tried:
            continue
        try:
            return response.content.decode(fallback, errors="replace")
        except LookupError:
            continue
    return response.text


def validate_once(
    proxy: ProxyEntry | None,
    target_url: str,
    timeout: float,
    ok_min_keywords: int,
    attempt_index: int,
) -> ValidationResult:
    profile = choose_profile()
    request_url = cache_busted_okooo_url(target_url, profile=profile)
    headers = mobile_headers(
        extra={
            "Referer": "https://m.okooo.com/",
        },
        profile=profile,
    )
    proxies = None
    proxy_value = "DIRECT"
    proxy_label = f"direct-{attempt_index + 1:03d}"
    if proxy is not None:
        proxies = {
            "http": proxy.normalized,
            "https": proxy.normalized,
        }
        proxy_value = proxy.normalized
        proxy_label = proxy.label

    start = time.perf_counter()
    try:
        response = requests.get(
            request_url,
            headers=headers,
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        body = decode_response_text(response)
        hits = keyword_hits(body, SUCCESS_KEYWORDS)
        blocked_hits = keyword_hits(body, BLOCK_KEYWORDS)
        ok, marker_hits = is_successful_page(
            status_code=response.status_code,
            body=body,
            keyword_matches=hits,
            blocked_matches=blocked_hits,
            ok_min_keywords=ok_min_keywords,
        )
        return ValidationResult(
            proxy=proxy_value,
            proxy_label=proxy_label,
            ok=ok,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            content_length=len(body),
            keyword_hits=hits,
            blocked_hits=blocked_hits,
            page_marker_hits=marker_hits,
            final_url=str(response.url),
            error="",
        )
    except requests.RequestException as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ValidationResult(
            proxy=proxy_value,
            proxy_label=proxy_label,
            ok=False,
            status_code=None,
            elapsed_ms=elapsed_ms,
            content_length=0,
            keyword_hits=[],
            blocked_hits=[],
            page_marker_hits=[],
            final_url=request_url,
            error=str(exc),
        )


def validate_proxy(
    proxy: ProxyEntry | None,
    target_url: str,
    timeout: float,
    retries: int,
    ok_min_keywords: int,
) -> ValidationResult:
    attempts = max(1, retries)
    last_result: ValidationResult | None = None
    for attempt_index in range(attempts):
        result = validate_once(
            proxy=proxy,
            target_url=target_url,
            timeout=timeout,
            ok_min_keywords=ok_min_keywords,
            attempt_index=attempt_index,
        )
        last_result = result
        if result.ok:
            return result
    assert last_result is not None
    return last_result


def validate_direct(
    target_url: str,
    timeout: float,
    attempts: int,
    ok_min_keywords: int,
    concurrency: int,
) -> list[ValidationResult]:
    total_attempts = max(1, attempts)
    results: list[ValidationResult] = []
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, total_attempts))) as executor:
        futures = [
            executor.submit(
                validate_once,
                None,
                target_url,
                timeout,
                ok_min_keywords,
                attempt_index,
            )
            for attempt_index in range(total_attempts)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def write_json(path: str, payload: dict) -> None:
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: str, results: list[ValidationResult]) -> None:
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "proxy_label",
        "proxy",
        "ok",
        "status_code",
        "elapsed_ms",
        "content_length",
        "keyword_hits",
        "blocked_hits",
        "page_marker_hits",
        "final_url",
        "error",
    ]
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            row = asdict(item)
            row["keyword_hits"] = "|".join(item.keyword_hits)
            row["blocked_hits"] = "|".join(item.blocked_hits)
            row["page_marker_hits"] = "|".join(item.page_marker_hits)
            writer.writerow(row)


def print_result_line(result: ValidationResult) -> None:
    status = "OK" if result.ok else "FAIL"
    code = result.status_code if result.status_code is not None else "-"
    hits = ",".join(result.keyword_hits) if result.keyword_hits else "-"
    blocked = ",".join(result.blocked_hits) if result.blocked_hits else "-"
    markers = ",".join(result.page_marker_hits) if result.page_marker_hits else "-"
    error = result.error if result.error else "-"
    print(
        f"[{status}] {result.proxy_label} code={code} "
        f"elapsed={result.elapsed_ms}ms len={result.content_length} "
        f"hits={hits} markers={markers} blocked={blocked} proxy={result.proxy} err={error}"
    )


def build_summary(target_url: str, results: list[ValidationResult]) -> dict:
    total = len(results)
    success = sum(1 for item in results if item.ok)
    failure = total - success
    avg_elapsed = int(sum(item.elapsed_ms for item in results) / total) if total else 0
    return {
        "target_url": target_url,
        "total": total,
        "success": success,
        "failure": failure,
        "success_rate": round((success / total) * 100, 2) if total else 0.0,
        "avg_elapsed_ms": avg_elapsed,
        "max_elapsed_ms": max((item.elapsed_ms for item in results), default=0),
        "min_elapsed_ms": min((item.elapsed_ms for item in results), default=0),
        "ok_proxies": [item.proxy for item in results if item.ok],
        "failed_proxies": [item.proxy for item in results if not item.ok],
    }


def main() -> None:
    args = parse_args()
    target_url = target_url_from_args(args)

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    if should_use_direct_mode(args):
        results = validate_direct(
            target_url=target_url,
            timeout=float(args.timeout),
            attempts=int(args.attempts),
            ok_min_keywords=int(args.ok_min_keywords),
            concurrency=int(args.concurrency),
        )
    else:
        proxies = load_proxies(args.proxy_file)
        results = []
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
            futures = {
                executor.submit(
                    validate_proxy,
                    proxy,
                    target_url,
                    float(args.timeout),
                    int(args.retries),
                    int(args.ok_min_keywords),
                ): proxy
                for proxy in proxies
            }
            for future in as_completed(futures):
                results.append(future.result())

    for result in results:
        if args.only_ok and not result.ok:
            continue
        print_result_line(result)

    results.sort(key=lambda item: (not item.ok, item.elapsed_ms, item.proxy))
    summary = build_summary(target_url, results)

    payload = {
        "started_at": started_at,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "results": [asdict(item) for item in results],
    }

    print()
    print(
        f"SUMMARY total={summary['total']} success={summary['success']} "
        f"failure={summary['failure']} success_rate={summary['success_rate']}% "
        f"avg_elapsed={summary['avg_elapsed_ms']}ms "
        f"min_elapsed={summary['min_elapsed_ms']}ms "
        f"max_elapsed={summary['max_elapsed_ms']}ms"
    )

    if args.json_out:
        write_json(args.json_out, payload)
        print(f"JSON 已保存: {Path(args.json_out).expanduser().resolve()}")
    if args.csv_out:
        write_csv(args.csv_out, results)
        print(f"CSV 已保存: {Path(args.csv_out).expanduser().resolve()}")


if __name__ == "__main__":
    main()
