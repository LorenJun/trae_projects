"""模块说明：构建并检索基于归档、滚动记忆与赛果标签的混合检索 RAG 案例库。"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from glob import glob
from typing import Any, Dict, Iterable, List, Optional, Tuple

from runtime.memory_samples import load_prediction_memory_samples
from runtime.paths import get_default_paths
from storage import PredictionArchiveStore

RAG_MODE = "hybrid-structured-bm25-v2"
CASE_TYPES = ("prediction_case", "market_case", "upset_case")
WINNER_TEXT_TO_CODE = {"主胜": "home", "平局": "draw", "客胜": "away"}
WINNER_CODE_TO_TEXT = {"home": "主胜", "draw": "平局", "away": "客胜"}
EURO_COMPETITION_CODES = {"europa_league", "champions_league", "conference_league", "uefa_super_cup"}
DOMESTIC_LEAGUE_CODES = {"premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1"}
LEAGUE_CN_NAMES = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "serie_a": "意甲",
    "bundesliga": "德甲",
    "ligue_1": "法甲",
    "champions_league": "欧冠",
    "europa_league": "欧联",
    "conference_league": "欧协联",
}
SNAPSHOT_LEAGUE_ALIASES = {
    "欧联": "europa_league",
    "欧罗巴": "europa_league",
    "欧冠": "champions_league",
    "英超": "premier_league",
    "西甲": "la_liga",
    "意甲": "serie_a",
    "德甲": "bundesliga",
    "法甲": "ligue_1",
}


def rag_cases_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file("rag_cases.json")


def rag_index_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file("rag_index.json")


def rag_registry_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file("rag_registry.json")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _pick_market_line(snapshot: Dict[str, Any], market_key: str, phase: str, field: str) -> Optional[float]:
    block = snapshot.get(market_key) if isinstance(snapshot.get(market_key), dict) else {}
    stage = block.get(phase) if isinstance(block.get(phase), dict) else {}
    return _safe_float(stage.get(field))


def _parse_actual_total_goals(actual_score: str) -> Optional[int]:
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(actual_score or ""))
    if not match:
        return None
    return int(match.group(1)) + int(match.group(2))


def _predict_ou_direction(over_under: Dict[str, Any]) -> str:
    if not isinstance(over_under, dict):
        return ""
    over = _safe_float(over_under.get("over")) or 0.0
    under = _safe_float(over_under.get("under")) or 0.0
    if over <= 0 and under <= 0:
        return ""
    return "大球" if over > under else "小球"


def _tokenize_text(text: str) -> List[str]:
    normalized = str(text or "").replace("|", " ").replace("/", " ").replace("->", " ")
    raw_tokens = re.findall(r"[A-Za-z0-9_.:-]+|[\u4e00-\u9fff]+", normalized)
    tokens: List[str] = []
    for token in raw_tokens:
        cleaned = token.strip().lower()
        if not cleaned:
            continue
        tokens.append(cleaned)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) > 1:
                tokens.append(token)
            for size in (2, 3):
                if len(token) >= size:
                    for idx in range(len(token) - size + 1):
                        tokens.append(token[idx : idx + size])
    return tokens


def _serialize_market_snapshot(snapshot: Dict[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        return ""
    snippets: List[str] = []
    euro_initial = snapshot.get("欧赔", {}).get("initial", {}) if isinstance(snapshot.get("欧赔"), dict) else {}
    euro_final = snapshot.get("欧赔", {}).get("final", {}) if isinstance(snapshot.get("欧赔"), dict) else {}
    if euro_initial or euro_final:
        snippets.append(
            "欧赔 "
            f"{euro_initial.get('home','?')}/{euro_initial.get('draw','?')}/{euro_initial.get('away','?')}"
            " -> "
            f"{euro_final.get('home','?')}/{euro_final.get('draw','?')}/{euro_final.get('away','?')}"
        )
    asian_initial = snapshot.get("亚值", {}).get("initial", {}) if isinstance(snapshot.get("亚值"), dict) else {}
    asian_final = snapshot.get("亚值", {}).get("final", {}) if isinstance(snapshot.get("亚值"), dict) else {}
    if asian_initial or asian_final:
        snippets.append(
            "亚盘 "
            f"{asian_initial.get('handicap_text') or asian_initial.get('handicap') or '?'} "
            f"{asian_initial.get('home_water','?')}/{asian_initial.get('away_water','?')}"
            " -> "
            f"{asian_final.get('handicap_text') or asian_final.get('handicap') or '?'} "
            f"{asian_final.get('home_water','?')}/{asian_final.get('away_water','?')}"
        )
    ou_initial = snapshot.get("大小球", {}).get("initial", {}) if isinstance(snapshot.get("大小球"), dict) else {}
    ou_final = snapshot.get("大小球", {}).get("final", {}) if isinstance(snapshot.get("大小球"), dict) else {}
    if ou_initial or ou_final:
        snippets.append(
            "大小球 "
            f"{ou_initial.get('line','?')} {ou_initial.get('over','?')}/{ou_initial.get('under','?')}"
            " -> "
            f"{ou_final.get('line','?')} {ou_final.get('over','?')}/{ou_final.get('under','?')}"
        )
    kelly_final = snapshot.get("凯利", {}).get("final", {}) if isinstance(snapshot.get("凯利"), dict) else {}
    if kelly_final:
        snippets.append(
            "凯利 "
            f"{kelly_final.get('home','?')}/{kelly_final.get('draw','?')}/{kelly_final.get('away','?')}"
        )
    return " | ".join(snippets)


def _competition_bucket(league_code: str, competition_stage_name: str = "") -> str:
    normalized_league = str(league_code or "").strip()
    stage = str(competition_stage_name or "").strip()
    if normalized_league in EURO_COMPETITION_CODES:
        return "europe_cup"
    if any(keyword in stage for keyword in ("半决赛", "决赛", "淘汰赛", "附加赛")):
        return "knockout"
    if any(keyword in stage for keyword in ("杯", "杯赛")):
        return "cup"
    if normalized_league in DOMESTIC_LEAGUE_CODES:
        return "domestic_league"
    return "unknown"


def _derive_review_tags(case: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    prediction = str(case.get('prediction') or '').strip()
    actual_result = str(case.get('actual_result') or '').strip()
    confidence = _safe_float(case.get('confidence')) or 0.0
    predicted_scores = [str(score).strip() for score in (case.get('predicted_scores') or []) if str(score).strip()]
    ou_line = _safe_float(case.get('ou_line'))
    if prediction == '主胜' and actual_result != '主胜':
        tags.append('主胜高估')
        if actual_result == '客胜':
            tags.append('客胜冷门漏判')
        elif actual_result == '平局':
            tags.append('平局防守不足')
    if prediction == '客胜' and actual_result != '客胜':
        tags.append('客胜高估')
    if confidence >= 0.65 and prediction and actual_result and prediction != actual_result:
        tags.append('高信心误判')
    if prediction == '主胜' and any(score in {'1-0', '2-1', '2-0', '3-0'} for score in predicted_scores):
        tags.append('比分模板偏主胜')
    if prediction == '平局' and any(score in {'0-0', '1-1'} for score in predicted_scores):
        tags.append('比分模板偏平局')
    if ou_line is None:
        tags.append('大小球盘口线缺失')
    return tags


def _annotate_review_dimensions(documents: List[Dict[str, Any]], recent_days: int = 30) -> None:
    _ = recent_days
    league_tag_counter: Dict[str, Counter[str]] = defaultdict(Counter)
    league_tag_aliases = {
        '主胜高估': '主胜偏置',
        '客胜冷门漏判': '客胜冷门敏感度不足',
        '平局防守不足': '平局防守不足',
        '大小球盘口线缺失': '大小球盘口线缺失',
        '高信心误判': '高信心误判',
        '比分模板偏主胜': '比分模板偏主胜',
        '比分模板偏平局': '比分模板偏平局',
        '客胜高估': '客胜偏置',
    }
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        review_tags = _derive_review_tags(doc)
        doc['review_tags'] = review_tags
        league_name = str(doc.get('league_name') or doc.get('league_code') or '').strip()
        for tag in review_tags:
            normalized_tag = league_tag_aliases.get(tag, tag)
            if league_name and normalized_tag:
                league_tag_counter[league_name][normalized_tag] += 1
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        league_name = str(doc.get('league_name') or doc.get('league_code') or '').strip()
        league_review_tags = [f'{league_name}-{tag}' for tag, _count in league_tag_counter.get(league_name, Counter()).most_common(4)] if league_name else []
        doc['league_review_tags'] = league_review_tags
        review_tags = doc.get('review_tags') or []
        text = str(doc.get('text') or '').strip()
        review_line = f"错因标签:{'、'.join(review_tags)}" if review_tags else '错因标签:无'
        league_line = f"联赛复盘:{'、'.join(league_review_tags)}" if league_review_tags else '联赛复盘:无'
        doc['text'] = f"{text} | {review_line} | {league_line}" if text else f"{review_line} | {league_line}"


def _odds_history_records(base_dir: Optional[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    root = str(get_default_paths(base_dir).base_dir)
    for file_path in glob(f"{root}/*/analysis/odds/*_odds.json"):
        try:
            payload = json.loads(open(file_path, "r", encoding="utf-8").read())
        except Exception:
            continue
        league_code = str(payload.get("league_code") or "").strip()
        for match in payload.get("matches") or []:
            if isinstance(match, dict):
                row = dict(match)
                row["_source_file"] = file_path
                row["_league_code"] = league_code
                records.append(row)
    return records


def _snapshot_market_records(base_dir: Optional[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    root = str(get_default_paths(base_dir).runtime_dir.parent / "snapshots")
    for file_path in glob(f"{root}/**/*.json", recursive=True):
        try:
            payload = json.loads(open(file_path, "r", encoding="utf-8").read())
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        league_hint = str(payload.get("league") or "").strip()
        league_dir = file_path.split("/snapshots/")[-1].split("/", 1)[0] if "/snapshots/" in file_path else ""
        league_code = SNAPSHOT_LEAGUE_ALIASES.get(league_hint) or SNAPSHOT_LEAGUE_ALIASES.get(league_dir) or league_dir
        market_snapshot = {
            key: payload.get(key)
            for key in ("胜平负赔率", "欧赔", "亚值", "大小球", "凯利", "离散率")
            if isinstance(payload.get(key), dict)
        }
        if not market_snapshot:
            continue
        row = {
            "match_id": str(payload.get("match_id") or payload.get("schedule", {}).get("mid") or ""),
            "home_team": str(payload.get("home_team") or ""),
            "away_team": str(payload.get("away_team") or ""),
            "match_date": str(payload.get("match_date") or ""),
            "league_code": league_code,
            "league_name": league_hint or league_code,
            "competition_stage_name": "",
            "market_snapshot": market_snapshot,
        }
        records.append(row)
    return records


def _collect_risk_points(archived: Dict[str, Any]) -> List[str]:
    points: List[str] = []
    upset = archived.get("upset_potential") if isinstance(archived.get("upset_potential"), dict) else {}
    for item in upset.get("factors") or []:
        text = str(item or "").strip()
        if text and text not in points:
            points.append(text)
    intelligence = archived.get("match_intelligence") if isinstance(archived.get("match_intelligence"), dict) else {}
    for item in intelligence.get("signals") or []:
        text = str(item or "").strip()
        if text and text not in points:
            points.append(text)
    realtime = archived.get("realtime") if isinstance(archived.get("realtime"), dict) else {}
    context_applied = realtime.get("context_applied") if isinstance(realtime.get("context_applied"), dict) else {}
    for group_key in ("match_intelligence", "live_outcome_adjustment"):
        group = context_applied.get(group_key)
        if isinstance(group, dict):
            for item in group.get("signals") or []:
                text = str(item or "").strip()
                if text and text not in points:
                    points.append(text)
    return points[:5]


def _base_case_entity(
    archive_key: str,
    archived: Dict[str, Any],
    sample: Dict[str, Any],
) -> Dict[str, Any]:
    full_prediction = archived.get("full_prediction") if isinstance(archived.get("full_prediction"), dict) else {}
    market_snapshot = archived.get("market_snapshot") if isinstance(archived.get("market_snapshot"), dict) else {}
    if not market_snapshot and isinstance(full_prediction.get("market_snapshot"), dict):
        market_snapshot = full_prediction.get("market_snapshot") or {}

    match_id = str(
        archived.get("external_match_id")
        or archived.get("match_id")
        or archived.get("internal_match_id")
        or archive_key
    ).strip()
    actual_result = str(sample.get("actual_result") or archived.get("actual_result") or "").strip()
    actual_score = str(
        sample.get("actual_score")
        or archived.get("actual_score")
        or full_prediction.get("actual_score")
        or ""
    ).strip()
    over_under = archived.get("over_under") if isinstance(archived.get("over_under"), dict) else full_prediction.get("over_under")
    predicted_ou_direction = _predict_ou_direction(over_under if isinstance(over_under, dict) else {})
    ou_line = (
        _safe_float((over_under or {}).get("line")) if isinstance(over_under, dict) else None
    ) or _pick_market_line(market_snapshot, "大小球", "final", "line") or _pick_market_line(market_snapshot, "大小球", "initial", "line")
    predicted_scores = []
    top_scores = archived.get("top_scores")
    if isinstance(top_scores, list):
        for item in top_scores[:3]:
            if isinstance(item, (list, tuple)) and item:
                predicted_scores.append(str(item[0]))
    if not predicted_scores and isinstance(full_prediction.get("top_scores"), list):
        for item in full_prediction.get("top_scores", [])[:3]:
            if isinstance(item, (list, tuple)) and item:
                predicted_scores.append(str(item[0]))

    competition_stage_name = str(
        archived.get("competition_stage_name")
        or full_prediction.get("competition_stage_name")
        or archived.get("competition_stage")
        or full_prediction.get("competition_stage")
        or ""
    ).strip()
    league_code = str(archived.get("league") or full_prediction.get("league_code") or "").strip()
    return {
        "match_id": match_id,
        "archive_key": str(archive_key),
        "league_code": league_code,
        "league_name": str(archived.get("league_name") or full_prediction.get("league_name") or "").strip(),
        "competition_stage_name": competition_stage_name,
        "competition_bucket": _competition_bucket(league_code, competition_stage_name),
        "home_team": str(archived.get("home_team") or full_prediction.get("home_team") or "").strip(),
        "away_team": str(archived.get("away_team") or full_prediction.get("away_team") or "").strip(),
        "match_date": str(archived.get("match_date") or full_prediction.get("match_date") or "").strip(),
        "prediction": str(archived.get("prediction") or full_prediction.get("prediction") or "").strip(),
        "confidence": _safe_float(archived.get("confidence") or full_prediction.get("confidence")),
        "actual_score": actual_score,
        "actual_result": actual_result or WINNER_CODE_TO_TEXT.get(str(archived.get("actual_winner") or "").strip(), ""),
        "storage_mode": str(archived.get("storage_mode") or full_prediction.get("storage_mode") or "").strip(),
        "risk_points": _collect_risk_points(archived),
        "market_snapshot": market_snapshot,
        "market_summary": _serialize_market_snapshot(market_snapshot),
        "ou_line": ou_line,
        "predicted_ou_direction": predicted_ou_direction,
        "asian_line": _pick_market_line(market_snapshot, "亚值", "final", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "final", "handicap")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap"),
        "euro_home": _pick_market_line(market_snapshot, "欧赔", "final", "home")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "home"),
        "euro_draw": _pick_market_line(market_snapshot, "欧赔", "final", "draw")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "draw"),
        "euro_away": _pick_market_line(market_snapshot, "欧赔", "final", "away")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "away"),
        "actual_total_goals": _parse_actual_total_goals(actual_score),
        "predicted_scores": predicted_scores,
        "completed": bool(actual_result and actual_score),
        "archived_at": str(archived.get("archived_at") or full_prediction.get("timestamp") or ""),
    }


def _build_case_document(base: Dict[str, Any], case_type: str, text: str) -> Dict[str, Any]:
    terms = _tokenize_text(text)
    term_counts = Counter(terms)
    doc = dict(base)
    doc["case_type"] = case_type
    doc["case_id"] = f"{case_type}:{base.get('match_id')}:{base.get('archive_key')}"
    doc["text"] = text
    doc["terms"] = list(term_counts.keys())
    doc["term_counts"] = dict(term_counts)
    doc["doc_length"] = int(sum(term_counts.values()))
    return doc


def _case_text_prediction(base: Dict[str, Any]) -> str:
    parts = [
        str(base.get("league_name") or base.get("league_code") or ""),
        str(base.get("competition_stage_name") or ""),
        f"{base.get('home_team', '')} vs {base.get('away_team', '')}",
        f"预测:{base.get('prediction', '')}",
        f"信心:{base.get('confidence', '')}",
        f"候选比分:{'/'.join(base.get('predicted_scores') or [])}",
        f"大小球:{base.get('predicted_ou_direction', '')} {base.get('ou_line', '')}",
        f"赛果:{base.get('actual_result', '')} {base.get('actual_score', '')}".strip(),
        f"风险:{' '.join(base.get('risk_points') or [])}",
    ]
    return " | ".join([item for item in parts if str(item).strip()])


def _case_text_market(base: Dict[str, Any]) -> str:
    parts = [
        str(base.get("league_name") or base.get("league_code") or ""),
        str(base.get("competition_stage_name") or ""),
        f"{base.get('home_team', '')} vs {base.get('away_team', '')}",
        f"盘口:{base.get('market_summary', '')}",
        f"大小球:{base.get('predicted_ou_direction', '')} {base.get('ou_line', '')}",
        f"赛果:{base.get('actual_result', '')} {base.get('actual_score', '')}".strip(),
    ]
    return " | ".join([item for item in parts if str(item).strip()])


def _case_text_upset(base: Dict[str, Any]) -> str:
    parts = [
        str(base.get("league_name") or base.get("league_code") or ""),
        str(base.get("competition_stage_name") or ""),
        f"{base.get('home_team', '')} vs {base.get('away_team', '')}",
        f"预测:{base.get('prediction', '')}",
        f"赛果:{base.get('actual_result', '')} {base.get('actual_score', '')}".strip(),
        f"风险:{' '.join(base.get('risk_points') or [])}",
    ]
    return " | ".join([item for item in parts if str(item).strip()])


def _market_case_from_odds_history(match: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    league_code = str(match.get("_league_code") or match.get("league_code") or "").strip()
    if not league_code:
        return None
    market_snapshot = {
        key: match.get(key) for key in ("胜平负赔率", "欧赔", "亚值", "大小球", "凯利", "离散率") if isinstance(match.get(key), dict)
    }
    market_summary = _serialize_market_snapshot(market_snapshot)
    if not market_summary:
        return None
    base = {
        "match_id": str(match.get("match_id") or f"odds-history-{league_code}-{index}"),
        "archive_key": str(match.get("page_id") or f"odds-history-{league_code}-{index}"),
        "league_code": league_code,
        "league_name": str(match.get("league_name") or league_code),
        "competition_stage_name": str(match.get("competition_stage_name") or match.get("competition_stage") or ""),
        "competition_bucket": _competition_bucket(
            league_code,
            str(match.get("competition_stage_name") or match.get("competition_stage") or ""),
        ),
        "home_team": str(match.get("home_team") or ""),
        "away_team": str(match.get("away_team") or ""),
        "match_date": str(match.get("match_date") or ""),
        "prediction": "",
        "confidence": None,
        "actual_score": str(match.get("actual_score") or ""),
        "actual_result": str(match.get("actual_result") or ""),
        "storage_mode": "odds_history",
        "risk_points": [],
        "market_snapshot": market_snapshot,
        "market_summary": market_summary,
        "ou_line": _pick_market_line(market_snapshot, "大小球", "final", "line")
        or _pick_market_line(market_snapshot, "大小球", "initial", "line"),
        "predicted_ou_direction": "",
        "asian_line": _pick_market_line(market_snapshot, "亚值", "final", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "final", "handicap")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap"),
        "euro_home": _pick_market_line(market_snapshot, "欧赔", "final", "home")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "home"),
        "euro_draw": _pick_market_line(market_snapshot, "欧赔", "final", "draw")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "draw"),
        "euro_away": _pick_market_line(market_snapshot, "欧赔", "final", "away")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "away"),
        "actual_total_goals": _parse_actual_total_goals(str(match.get("actual_score") or "")),
        "predicted_scores": [],
        "completed": bool(match.get("actual_result") and match.get("actual_score")),
        "archived_at": "",
    }
    return _build_case_document(base, "market_case", _case_text_market(base))


def _market_case_from_snapshot(match: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    market_snapshot = match.get("market_snapshot") if isinstance(match.get("market_snapshot"), dict) else {}
    market_summary = _serialize_market_snapshot(market_snapshot)
    if not market_summary:
        return None
    league_code = str(match.get("league_code") or "").strip()
    base = {
        "match_id": str(match.get("match_id") or f"snapshot-market-{league_code}-{index}"),
        "archive_key": str(match.get("match_id") or f"snapshot-market-{league_code}-{index}"),
        "league_code": league_code,
        "league_name": str(match.get("league_name") or league_code),
        "competition_stage_name": str(match.get("competition_stage_name") or ""),
        "competition_bucket": _competition_bucket(league_code, str(match.get("competition_stage_name") or "")),
        "home_team": str(match.get("home_team") or ""),
        "away_team": str(match.get("away_team") or ""),
        "match_date": str(match.get("match_date") or ""),
        "prediction": "",
        "confidence": None,
        "actual_score": "",
        "actual_result": "",
        "storage_mode": "snapshot_market",
        "risk_points": [],
        "market_snapshot": market_snapshot,
        "market_summary": market_summary,
        "ou_line": _pick_market_line(market_snapshot, "大小球", "final", "line")
        or _pick_market_line(market_snapshot, "大小球", "initial", "line"),
        "predicted_ou_direction": "",
        "asian_line": _pick_market_line(market_snapshot, "亚值", "final", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap_value")
        or _pick_market_line(market_snapshot, "亚值", "final", "handicap")
        or _pick_market_line(market_snapshot, "亚值", "initial", "handicap"),
        "euro_home": _pick_market_line(market_snapshot, "欧赔", "final", "home")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "home"),
        "euro_draw": _pick_market_line(market_snapshot, "欧赔", "final", "draw")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "draw"),
        "euro_away": _pick_market_line(market_snapshot, "欧赔", "final", "away")
        or _pick_market_line(market_snapshot, "欧赔", "initial", "away"),
        "actual_total_goals": None,
        "predicted_scores": [],
        "completed": False,
        "archived_at": "",
    }
    return _build_case_document(base, "market_case", _case_text_market(base))


def _iter_case_documents(archive: Dict[str, Dict[str, Any]], sample_index: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []
    seen_ids = set()
    for archive_key, archived in archive.items():
        if not isinstance(archived, dict):
            continue
        candidate_match_id = str(
            archived.get("external_match_id")
            or archived.get("match_id")
            or archived.get("internal_match_id")
            or archive_key
        ).strip()
        if candidate_match_id and candidate_match_id in seen_ids:
            continue
        if candidate_match_id:
            seen_ids.add(candidate_match_id)
        sample = sample_index.get(candidate_match_id, {})
        base = _base_case_entity(str(archive_key), archived, sample)
        if not base.get("league_code"):
            continue
        documents.append(_build_case_document(base, "prediction_case", _case_text_prediction(base)))
        if base.get("market_summary"):
            documents.append(_build_case_document(base, "market_case", _case_text_market(base)))
        is_upset_like = bool(base.get("risk_points")) or (
            base.get("completed")
            and base.get("prediction")
            and base.get("actual_result")
            and str(base.get("prediction")) != str(base.get("actual_result"))
        )
        if is_upset_like:
            documents.append(_build_case_document(base, "upset_case", _case_text_upset(base)))
    return documents


def _infer_query_competition_context(
    documents: List[Dict[str, Any]],
    *,
    match_id: str,
    home_team: str,
    away_team: str,
) -> Dict[str, str]:
    priority = {"europe_cup": 4, "cup": 3, "knockout": 2, "domestic_league": 1, "unknown": 0, "": 0}
    candidates: List[Dict[str, str]] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        if match_id and str(doc.get("match_id") or "").strip() == match_id:
            candidates.append({
                "competition_bucket": str(doc.get("competition_bucket") or ""),
                "competition_stage_name": str(doc.get("competition_stage_name") or ""),
            })
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("home_team") or "").strip() == home_team and str(doc.get("away_team") or "").strip() == away_team:
            candidates.append({
                "competition_bucket": str(doc.get("competition_bucket") or ""),
                "competition_stage_name": str(doc.get("competition_stage_name") or ""),
            })
    if not candidates:
        return {"competition_bucket": "", "competition_stage_name": ""}
    candidates.sort(key=lambda item: priority.get(str(item.get("competition_bucket") or ""), 0), reverse=True)
    return candidates[0]


def _build_index_statistics(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    document_frequencies: Dict[str, int] = defaultdict(int)
    avgdl = 0.0
    case_type_counts: Dict[str, int] = defaultdict(int)
    for doc in documents:
        case_type_counts[str(doc.get("case_type") or "unknown")] += 1
        avgdl += float(doc.get("doc_length") or 0)
        for term in set(doc.get("terms") or []):
            document_frequencies[term] += 1
    total_docs = len(documents)
    if total_docs > 0:
        avgdl = avgdl / total_docs
    return {
        "document_count": total_docs,
        "avgdl": round(avgdl, 6),
        "document_frequencies": dict(document_frequencies),
        "case_type_counts": dict(case_type_counts),
    }


def build_hybrid_rag_index(base_dir: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    archive = PredictionArchiveStore(base_dir).load()
    memory_samples = load_prediction_memory_samples(base_dir=base_dir, limit=limit)
    memory_records_by_league = memory_samples.get("records_by_league") if isinstance(memory_samples, dict) else {}

    sample_index: Dict[str, Dict[str, Any]] = {}
    if isinstance(memory_records_by_league, dict):
        for records in memory_records_by_league.values():
            for record in records or []:
                if isinstance(record, dict) and record.get("match_id"):
                    sample_index[str(record.get("match_id"))] = record

    documents = _iter_case_documents(archive, sample_index)
    for index, record in enumerate(_odds_history_records(base_dir), start=1):
        doc = _market_case_from_odds_history(record, index=index)
        if doc:
            documents.append(doc)
    for index, record in enumerate(_snapshot_market_records(base_dir), start=1):
        doc = _market_case_from_snapshot(record, index=index)
        if doc:
            documents.append(doc)
    _annotate_review_dimensions(documents, recent_days=30)
    index_stats = _build_index_statistics(documents)
    cases_payload = {
        "updated_at": datetime.now().isoformat(),
        "rag_mode": RAG_MODE,
        "case_count": len(documents),
        "cases": documents,
    }
    index_payload = {
        "updated_at": cases_payload["updated_at"],
        "rag_mode": RAG_MODE,
        **index_stats,
    }
    registry_payload = {
        "updated_at": cases_payload["updated_at"],
        "rag_mode": RAG_MODE,
        "source_files": [
            "prediction_archive.json",
            "prediction_memory_odds_samples.json",
            "*/analysis/odds/*_odds.json",
            ".okooo-scraper/snapshots/**/*.json",
        ],
        "case_count": len(documents),
        "archive_record_count": len(archive),
        "memory_sample_count": len(sample_index),
        "case_type_counts": index_stats.get("case_type_counts", {}),
    }
    rag_cases_path(base_dir).write_text(json.dumps(cases_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rag_index_path(base_dir).write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rag_registry_path(base_dir).write_text(json.dumps(registry_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **cases_payload,
        "index": index_payload,
        "registry": registry_payload,
    }


def build_rag_cases(base_dir: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    return build_hybrid_rag_index(base_dir=base_dir, limit=limit)


def sync_rag_index(base_dir: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    return build_hybrid_rag_index(base_dir=base_dir, limit=limit)


def load_rag_cases(base_dir: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    path = rag_cases_path(base_dir)
    if not path.exists():
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit)
    if not isinstance(payload, dict) or "cases" not in payload:
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit)
    return payload


def load_rag_index(base_dir: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    path = rag_index_path(base_dir)
    if not path.exists():
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit).get("index", {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit).get("index", {})
    if not isinstance(payload, dict) or "document_frequencies" not in payload:
        return build_hybrid_rag_index(base_dir=base_dir, limit=limit).get("index", {})
    return payload


def _idf(term: str, *, total_docs: int, document_frequencies: Dict[str, int]) -> float:
    df = int(document_frequencies.get(term, 0))
    if total_docs <= 0:
        return 0.0
    return math.log(1 + (total_docs - df + 0.5) / (df + 0.5))


def _bm25_score(doc: Dict[str, Any], query_terms: List[str], index_payload: Dict[str, Any]) -> float:
    total_docs = int(index_payload.get("document_count") or 0)
    avgdl = float(index_payload.get("avgdl") or 1.0) or 1.0
    document_frequencies = index_payload.get("document_frequencies") if isinstance(index_payload.get("document_frequencies"), dict) else {}
    term_counts = doc.get("term_counts") if isinstance(doc.get("term_counts"), dict) else {}
    doc_length = float(doc.get("doc_length") or 0.0)
    if total_docs <= 0 or not term_counts:
        return 0.0
    score = 0.0
    k1 = 1.5
    b = 0.75
    for term in query_terms:
        tf = float(term_counts.get(term) or 0.0)
        if tf <= 0:
            continue
        numerator = tf * (k1 + 1.0)
        denominator = tf + k1 * (1.0 - b + b * (doc_length / avgdl))
        score += _idf(term, total_docs=total_docs, document_frequencies=document_frequencies) * (numerator / denominator)
    return score


def _market_distance(doc: Dict[str, Any], query: Dict[str, Any]) -> float:
    penalties = []
    for key in ("ou_line", "asian_line", "euro_home", "euro_draw", "euro_away"):
        left = _safe_float(doc.get(key))
        right = _safe_float(query.get(key))
        if left is None or right is None:
            continue
        penalties.append(abs(left - right))
    if not penalties:
        return 999.0
    return sum(penalties) / len(penalties)


def _market_similarity_bonus(doc: Dict[str, Any], query: Dict[str, Any]) -> float:
    distance = _market_distance(doc, query)
    if distance >= 999.0:
        return 0.0
    return max(0.0, 3.5 - min(3.5, distance))


def _structured_bonus(doc: Dict[str, Any], query: Dict[str, Any]) -> float:
    score = 0.0
    doc_league = str(doc.get("league_code") or "").strip()
    if doc_league and doc_league == str(query.get("league_code") or "").strip():
        score += 2.0
    doc_bucket = str(doc.get("competition_bucket") or "").strip()
    query_bucket = str(query.get("competition_bucket") or "").strip()
    if query_bucket and doc_bucket:
        if doc_bucket == query_bucket:
            score += 2.2
        else:
            score -= 1.4
    query_stage = str(query.get("competition_stage_name") or "").strip()
    if query_stage and query_stage == str(doc.get("competition_stage_name") or "").strip():
        score += 1.4
    if doc.get("completed"):
        score += 0.7
    if str(doc.get("home_team") or "").strip() == str(query.get("home_team") or "").strip():
        score += 0.4
    if str(doc.get("away_team") or "").strip() == str(query.get("away_team") or "").strip():
        score += 0.4
    query_risks = set(query.get("risk_tokens") or [])
    doc_risks = set(_tokenize_text(" ".join(doc.get("risk_points") or [])))
    if query_risks and doc_risks:
        score += min(1.2, 0.4 * len(query_risks & doc_risks))
    return score


def _build_query(
    *,
    league_code: str,
    home_team: str,
    away_team: str,
    market_snapshot: Optional[Dict[str, Any]],
    match_id: str = "",
    analysis_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    analysis_context = analysis_context if isinstance(analysis_context, dict) else {}
    competition_stage_name = str(
        analysis_context.get("competition_stage_name")
        or analysis_context.get("competition_stage")
        or ""
    ).strip()
    risk_text_parts = []
    for key in ("risk_summary", "risk_note", "match_script", "betting_note"):
        text = str(analysis_context.get(key) or "").strip()
        if text:
            risk_text_parts.append(text)
    query_text = " | ".join(
        item
        for item in [
            league_code,
            competition_stage_name,
            home_team,
            away_team,
            " ".join(risk_text_parts),
            _serialize_market_snapshot(market_snapshot or {}),
        ]
        if str(item).strip()
    )
    competition_bucket = _competition_bucket(str(league_code or ""), competition_stage_name)
    return {
        "match_id": str(match_id or "").strip(),
        "league_code": str(league_code or "").strip(),
        "home_team": str(home_team or "").strip(),
        "away_team": str(away_team or "").strip(),
        "competition_stage_name": competition_stage_name,
        "competition_bucket": competition_bucket,
        "risk_tokens": _tokenize_text(" ".join(risk_text_parts)),
        "ou_line": _pick_market_line(market_snapshot or {}, "大小球", "final", "line")
        or _pick_market_line(market_snapshot or {}, "大小球", "initial", "line"),
        "asian_line": _pick_market_line(market_snapshot or {}, "亚值", "final", "handicap_value")
        or _pick_market_line(market_snapshot or {}, "亚值", "initial", "handicap_value")
        or _pick_market_line(market_snapshot or {}, "亚值", "final", "handicap")
        or _pick_market_line(market_snapshot or {}, "亚值", "initial", "handicap"),
        "euro_home": _pick_market_line(market_snapshot or {}, "欧赔", "final", "home")
        or _pick_market_line(market_snapshot or {}, "欧赔", "initial", "home"),
        "euro_draw": _pick_market_line(market_snapshot or {}, "欧赔", "final", "draw")
        or _pick_market_line(market_snapshot or {}, "欧赔", "initial", "draw"),
        "euro_away": _pick_market_line(market_snapshot or {}, "欧赔", "final", "away")
        or _pick_market_line(market_snapshot or {}, "欧赔", "initial", "away"),
        "text": query_text,
    }


def _format_doc_result(doc: Dict[str, Any], score: float, bm25: float, market_bonus: float, structured_bonus: float) -> Dict[str, Any]:
    return {
        "match_id": doc.get("match_id"),
        "league_code": doc.get("league_code"),
        "league_name": doc.get("league_name"),
        "competition_stage_name": doc.get("competition_stage_name"),
        "match_date": doc.get("match_date"),
        "home_team": doc.get("home_team"),
        "away_team": doc.get("away_team"),
        "prediction": doc.get("prediction"),
        "confidence": doc.get("confidence"),
        "actual_score": doc.get("actual_score"),
        "actual_result": doc.get("actual_result"),
        "storage_mode": doc.get("storage_mode"),
        "risk_points": doc.get("risk_points"),
        "predicted_ou_direction": doc.get("predicted_ou_direction"),
        "ou_line": doc.get("ou_line"),
        "predicted_scores": doc.get("predicted_scores"),
        "case_type": doc.get("case_type"),
        "similarity_score": round(float(score), 4),
        "bm25_score": round(float(bm25), 4),
        "market_bonus": round(float(market_bonus), 4),
        "structured_bonus": round(float(structured_bonus), 4),
        "text": doc.get("text"),
    }


def _select_top_group(ranked_docs: List[Tuple[float, float, float, float, Dict[str, Any]]], case_type: str, top_k: int, min_score: float) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for total_score, bm25, market_bonus, structured_bonus, doc in ranked_docs:
        if str(doc.get("case_type") or "") != case_type:
            continue
        if total_score < min_score:
            continue
        selected.append(_format_doc_result(doc, total_score, bm25, market_bonus, structured_bonus))
        if len(selected) >= top_k:
            break
    return selected


def _build_summary(similar_cases: List[Dict[str, Any]], market_cases: List[Dict[str, Any]], upset_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed_cases = [item for item in similar_cases if item.get("actual_result") and item.get("actual_score")]
    home_win = sum(1 for item in completed_cases if item.get("actual_result") == "主胜")
    draw = sum(1 for item in completed_cases if item.get("actual_result") == "平局")
    away_win = sum(1 for item in completed_cases if item.get("actual_result") == "客胜")

    total_goals_samples = []
    for item in market_cases:
        total_goals = _parse_actual_total_goals(str(item.get("actual_score") or ""))
        if isinstance(total_goals, int):
            total_goals_samples.append(total_goals)

    return {
        "retrieved_count": len(similar_cases) + len(market_cases) + len(upset_cases),
        "similar_case_count": len(similar_cases),
        "market_case_count": len(market_cases),
        "upset_case_count": len(upset_cases),
        "completed_similar_case_count": len(completed_cases),
        "home_win_rate": round(home_win / len(completed_cases), 4) if completed_cases else None,
        "draw_rate": round(draw / len(completed_cases), 4) if completed_cases else None,
        "away_win_rate": round(away_win / len(completed_cases), 4) if completed_cases else None,
        "avg_market_total_goals": round(sum(total_goals_samples) / len(total_goals_samples), 4) if total_goals_samples else None,
    }


def retrieve_hybrid_context(
    base_dir: Optional[str],
    *,
    league_code: str,
    home_team: str,
    away_team: str,
    market_snapshot: Optional[Dict[str, Any]],
    match_id: str = "",
    analysis_context: Optional[Dict[str, Any]] = None,
    top_k: int = 5,
    min_score: float = 0.75,
) -> Dict[str, Any]:
    cases_payload = load_rag_cases(base_dir=base_dir, limit=200)
    index_payload = load_rag_index(base_dir=base_dir, limit=200)
    documents = cases_payload.get("cases") if isinstance(cases_payload, dict) else []
    if not isinstance(documents, list):
        documents = []
    query = _build_query(
        league_code=league_code,
        home_team=home_team,
        away_team=away_team,
        market_snapshot=market_snapshot,
        match_id=match_id,
        analysis_context=analysis_context,
    )
    inferred_context = _infer_query_competition_context(
        documents,
        match_id=str(query.get("match_id") or ""),
        home_team=str(query.get("home_team") or ""),
        away_team=str(query.get("away_team") or ""),
    )
    if inferred_context.get("competition_bucket"):
        query["competition_bucket"] = inferred_context["competition_bucket"]
    if not query.get("competition_stage_name") and inferred_context.get("competition_stage_name"):
        query["competition_stage_name"] = inferred_context["competition_stage_name"]
    query_terms = _tokenize_text(query.get("text") or "")

    ranked_docs: List[Tuple[float, float, float, float, Dict[str, Any]]] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        if query["match_id"] and str(doc.get("match_id") or "").strip() == query["match_id"]:
            continue
        bm25 = _bm25_score(doc, query_terms, index_payload)
        structured_bonus = _structured_bonus(doc, query)
        market_bonus = _market_similarity_bonus(doc, query)
        type_bias = {
            "prediction_case": 0.4,
            "market_case": 0.55,
            "upset_case": 0.3,
        }.get(str(doc.get("case_type") or ""), 0.0)
        total_score = bm25 + structured_bonus + market_bonus + type_bias
        ranked_docs.append((total_score, bm25, market_bonus, structured_bonus, doc))

    ranked_docs.sort(key=lambda item: item[0], reverse=True)
    similar_cases = _select_top_group(ranked_docs, "prediction_case", top_k=max(1, top_k), min_score=min_score)
    market_cases = _select_top_group(ranked_docs, "market_case", top_k=max(1, min(3, top_k)), min_score=min_score)
    upset_cases = _select_top_group(ranked_docs, "upset_case", top_k=max(1, min(3, top_k)), min_score=min_score)
    summary = _build_summary(similar_cases, market_cases, upset_cases)
    return {
        "mode": RAG_MODE,
        "query": query,
        "summary": summary,
        "similar_cases": similar_cases,
        "market_cases": market_cases,
        "upset_cases": upset_cases,
    }


def retrieve_structured_cases(
    base_dir: Optional[str],
    *,
    league_code: str,
    home_team: str,
    away_team: str,
    market_snapshot: Optional[Dict[str, Any]],
    match_id: str = "",
    top_k: int = 5,
) -> Dict[str, Any]:
    result = retrieve_hybrid_context(
        base_dir,
        league_code=league_code,
        home_team=home_team,
        away_team=away_team,
        market_snapshot=market_snapshot,
        match_id=match_id,
        analysis_context=None,
        top_k=top_k,
    )
    cases = list(result.get("similar_cases") or [])
    for item in result.get("market_cases") or []:
        if item.get("match_id") not in {case.get("match_id") for case in cases}:
            cases.append(item)
    return {
        "mode": result.get("mode"),
        "query": result.get("query"),
        "summary": result.get("summary"),
        "cases": cases[:top_k],
    }
