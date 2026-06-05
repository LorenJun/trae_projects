#!/usr/bin/env python3
"""Shared mobile-access profile for `https://m.okooo.com/` requests."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from time import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


OKOOO_MOBILE_BASE = "https://m.okooo.com/"


@dataclass(frozen=True)
class OkoooMobileProfile:
    profile_id: str
    device_pool_id: str
    device_name: str
    user_agent: str
    viewport: dict[str, int]
    device_scale_factor: int
    accept_language: str
    cache_control: str
    pragma: str
    cache_key: str
    ts_key: str
    nonce_key: str
    nonce_prefix: str


def _build_profiles() -> tuple[OkoooMobileProfile, ...]:
    # Keep the pool fully in iPhone Safari shape because `m.okooo.com`
    # is much more permissive to this traffic pattern than mixed mobile UAs.
    # For bursty workloads like 14-match runs, rotate not only UA versions but
    # also a few realistic device pools so each access looks less homogeneous.
    device_pools = [
        {
            "pool_id": "iphone_se",
            "device_name": "iPhone SE",
            "viewport": {"width": 375, "height": 667},
            "device_scale_factor": 2,
        },
        {
            "pool_id": "iphone_12",
            "device_name": "iPhone 12",
            "viewport": {"width": 390, "height": 844},
            "device_scale_factor": 3,
        },
        {
            "pool_id": "iphone_13_mini",
            "device_name": "iPhone 13 mini",
            "viewport": {"width": 375, "height": 812},
            "device_scale_factor": 3,
        },
        {
            "pool_id": "iphone_14_plus",
            "device_name": "iPhone 14 Plus",
            "viewport": {"width": 428, "height": 926},
            "device_scale_factor": 3,
        },
        {
            "pool_id": "iphone_15_pro",
            "device_name": "iPhone 15 Pro",
            "viewport": {"width": 393, "height": 852},
            "device_scale_factor": 3,
        },
    ]
    version_specs = [f"{major}_{minor}" for major in range(18, 8, -1) for minor in range(10)]
    language_cycle = [
        "zh-CN,zh;q=0.9,en;q=0.8",
        "zh-CN,zh;q=0.95,en-US;q=0.8,en;q=0.7",
        "zh-CN,zh;q=0.9,en;q=0.75",
        "zh-Hans-CN,zh;q=0.9,en;q=0.8",
    ]
    cache_cycle = [
        "no-cache",
        "max-age=0, no-cache",
        "no-store, max-age=0",
        "no-cache, no-store, max-age=0",
    ]
    param_cycle = [
        ("_agent_mobile", "_ts", "_nonce", "m"),
        ("_mreq", "_t", "_r", "mr"),
        ("_touch", "_mt", "_id", "touch"),
        ("_ios", "_stamp", "_req", "ios"),
        ("_android", "_tick", "_sid", "and"),
    ]
    profiles: list[OkoooMobileProfile] = []
    for device_index, device in enumerate(device_pools):
        for version_index, version in enumerate(version_specs):
            idx = device_index * len(version_specs) + version_index
            cache_key, ts_key, nonce_key, nonce_prefix = param_cycle[idx % len(param_cycle)]
            profile_id = f"{device['pool_id']}_{version}"
            ua = (
                "Mozilla/5.0 (iPhone; CPU iPhone OS "
                f"{version} like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                f"Version/{version.replace('_', '.')} Mobile/15E148 Safari/604.1"
            )
            profiles.append(
                OkoooMobileProfile(
                    profile_id=profile_id,
                    device_pool_id=str(device["pool_id"]),
                    device_name=str(device["device_name"]),
                    user_agent=ua,
                    viewport=dict(device["viewport"]),
                    device_scale_factor=int(device["device_scale_factor"]),
                    accept_language=language_cycle[idx % len(language_cycle)],
                    cache_control=cache_cycle[idx % len(cache_cycle)],
                    pragma="no-cache",
                    cache_key=cache_key,
                    ts_key=ts_key,
                    nonce_key=nonce_key,
                    nonce_prefix=f"{nonce_prefix}{idx + 1:03d}",
                )
            )
    return tuple(profiles)


OKOOO_MOBILE_PROFILES = _build_profiles()


def is_okooo_mobile_url(url: str) -> bool:
    return (url or "").startswith(OKOOO_MOBILE_BASE)


def random_mobile_profile() -> OkoooMobileProfile:
    return random.choice(OKOOO_MOBILE_PROFILES)


def available_mobile_profiles() -> tuple[OkoooMobileProfile, ...]:
    return OKOOO_MOBILE_PROFILES


def _resolve_profile(profile: OkoooMobileProfile | None = None) -> OkoooMobileProfile:
    return profile or random_mobile_profile()


def mobile_headers(
    extra: dict[str, str] | None = None,
    profile: OkoooMobileProfile | None = None,
) -> dict[str, str]:
    resolved = _resolve_profile(profile)
    headers = {
        "User-Agent": resolved.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": resolved.accept_language,
        "Cache-Control": resolved.cache_control,
        "Pragma": resolved.pragma,
        # `m.okooo.com` is much more likely to serve the real odds page when
        # requests look like in-site mobile navigation instead of a cold direct hit.
        "Referer": OKOOO_MOBILE_BASE,
        "X-Okooo-Mobile-Profile": resolved.profile_id,
        "X-Okooo-Mobile-Device-Pool": resolved.device_pool_id,
    }
    if extra:
        headers.update(extra)
    return headers


def cache_busted_okooo_url(
    url: str,
    profile: OkoooMobileProfile | None = None,
) -> str:
    """Append a lightweight timestamp to avoid stale caches for mobile pages."""
    if not is_okooo_mobile_url(url):
        return url
    resolved = _resolve_profile(profile)
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[resolved.cache_key] = resolved.profile_id
    query[resolved.ts_key] = str(int(time() * 1000))
    query[resolved.nonce_key] = f"{resolved.nonce_prefix}_{uuid.uuid4().hex[:10]}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def mobile_context_options(profile: OkoooMobileProfile | None = None) -> dict[str, object]:
    resolved = _resolve_profile(profile)
    return {
        "viewport": resolved.viewport,
        "user_agent": resolved.user_agent,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "is_mobile": True,
        "has_touch": True,
        "device_scale_factor": resolved.device_scale_factor,
    }
