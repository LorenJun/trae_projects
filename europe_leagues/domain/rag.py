"""模块说明：提供基于现有归档、记忆样本与赛果标签的混合检索 RAG 服务。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from runtime.rag_store import (
    build_hybrid_rag_index,
    load_rag_index,
    retrieve_hybrid_context,
    retrieve_structured_cases,
)


class HybridRAGService:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir

    def refresh(self, limit: int = 200) -> Dict[str, Any]:
        return build_hybrid_rag_index(self.base_dir, limit=limit)

    def diagnose(self, limit: int = 200) -> Dict[str, Any]:
        index = load_rag_index(self.base_dir, limit=limit)
        return {
            "available": True,
            "mode": index.get("rag_mode"),
            "document_count": index.get("document_count"),
            "avgdl": index.get("avgdl"),
            "case_type_counts": index.get("case_type_counts", {}),
        }

    @staticmethod
    def _same_logical_match(
        item: Dict[str, Any],
        *,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        match_id: str,
    ) -> bool:
        if not isinstance(item, dict):
            return False
        item_match_id = str(item.get("match_id") or "").strip()
        if match_id and item_match_id and item_match_id == match_id:
            return True
        if (
            str(item.get("league_code") or "").strip() == str(league_code or "").strip()
            and str(item.get("match_date") or "").strip() == str(match_date or "").strip()
            and str(item.get("home_team") or "").strip() == str(home_team or "").strip()
            and str(item.get("away_team") or "").strip() == str(away_team or "").strip()
        ):
            return True
        return False

    @classmethod
    def _filter_current_match_cases(
        cls,
        cases: List[Dict[str, Any]],
        *,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        match_id: str,
    ) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for item in cases or []:
            if cls._same_logical_match(
                item,
                league_code=league_code,
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                match_id=match_id,
            ):
                continue
            filtered.append(item)
        return filtered

    @staticmethod
    def _merge_market_cases(
        existing: List[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = list(existing or [])
        seen = {
            (
                str(item.get("match_id") or ""),
                str(item.get("match_date") or ""),
                str(item.get("home_team") or ""),
                str(item.get("away_team") or ""),
            )
            for item in merged
            if isinstance(item, dict)
        }
        odds_ref = historical_odds_reference if isinstance(historical_odds_reference, dict) else {}
        for similar in odds_ref.get("similar_matches") or []:
            if not isinstance(similar, dict):
                continue
            key = (
                str(similar.get("match_id") or ""),
                str(similar.get("match_date") or ""),
                str(similar.get("home_team") or ""),
                str(similar.get("away_team") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "match_id": similar.get("match_id"),
                    "league_code": None,
                    "league_name": None,
                    "competition_stage_name": None,
                    "match_date": similar.get("match_date"),
                    "home_team": similar.get("home_team"),
                    "away_team": similar.get("away_team"),
                    "prediction": None,
                    "confidence": None,
                    "actual_score": similar.get("actual_score"),
                    "actual_result": similar.get("actual_result"),
                    "storage_mode": similar.get("source", "historical_odds_reference"),
                    "risk_points": [],
                    "predicted_ou_direction": None,
                    "ou_line": None,
                    "case_type": "market_case",
                    "similarity_score": round(float(similar.get("similarity") or 0.0), 4),
                    "bm25_score": 0.0,
                    "market_bonus": round(float(similar.get("similarity") or 0.0), 4),
                    "structured_bonus": 0.0,
                    "text": f"{similar.get('home_team')} vs {similar.get('away_team')} | 赛果:{similar.get('actual_result')} {similar.get('actual_score')}",
                }
            )
        merged.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
        return merged[:5]

    def retrieve_match_memory(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        match_date: str = "",
        market_snapshot: Optional[Dict[str, Any]],
        match_id: str = "",
        analysis_context: Optional[Dict[str, Any]] = None,
        historical_odds_reference: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        result = retrieve_hybrid_context(
            self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            market_snapshot=market_snapshot,
            match_id=match_id,
            analysis_context=analysis_context,
            top_k=top_k,
        )
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        similar_cases = result.get("similar_cases") if isinstance(result.get("similar_cases"), list) else []
        market_cases = result.get("market_cases") if isinstance(result.get("market_cases"), list) else []
        market_cases = self._merge_market_cases(market_cases, historical_odds_reference)
        upset_cases = result.get("upset_cases") if isinstance(result.get("upset_cases"), list) else []
        similar_cases = self._filter_current_match_cases(
            similar_cases,
            league_code=league_code,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            match_id=match_id,
        )
        market_cases = self._filter_current_match_cases(
            market_cases,
            league_code=league_code,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            match_id=match_id,
        )
        upset_cases = self._filter_current_match_cases(
            upset_cases,
            league_code=league_code,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            match_id=match_id,
        )
        summary = {
            **summary,
            "retrieved_count": len(similar_cases) + len(market_cases) + len(upset_cases),
            "market_case_count": len(market_cases),
        }
        return {
            "available": True,
            "mode": result.get("mode"),
            "summary": summary,
            "similar_cases": similar_cases,
            "market_cases": market_cases,
            "upset_cases": upset_cases,
        }

    def query_cases(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        market_snapshot: Optional[Dict[str, Any]],
        match_id: str = "",
        top_k: int = 5,
    ) -> Dict[str, Any]:
        return retrieve_structured_cases(
            self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            market_snapshot=market_snapshot,
            match_id=match_id,
            top_k=top_k,
        )


LightweightRAGService = HybridRAGService
