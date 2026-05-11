"""模块说明：负责预测前的实时快照刷新、输入准备与临场上下文补齐。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from collectors.okooo import build_okooo_driver_chain
from collectors.okooo import describe_unavailable_okooo_drivers
from collectors.okooo import extract_current_odds as extract_okooo_current_odds
from collectors.okooo import find_snapshot_for_match
from collectors.okooo import refresh_snapshot as refresh_okooo_snapshot
from collectors.okooo import snapshot_matches_request
from runtime.debug_events import emit_local_debug_event
from domain.features import auto_enrich_team_context_if_enabled
from domain.odds import auto_fetch_okooo_totals_if_needed


class LiveRefreshService:
    def __init__(self, base_dir: str, league_config: Dict[str, Dict[str, Any]], team_ewma_learning: Any):
        self.base_dir = base_dir
        self.league_config = league_config
        self.team_ewma_learning = team_ewma_learning

    @staticmethod
    def to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            s = str(value).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    @staticmethod
    def build_realtime(match_id: str, okooo_driver: str, okooo_headed: bool) -> Dict[str, Any]:
        return {
            'okooo': {
                'attempted': False,
                'refreshed': False,
                'snapshot_path': '',
                'match_id': str(match_id or ''),
                'driver': okooo_driver,
                'headed': bool(okooo_headed),
                'errors': [],
                'warnings': [],
            },
            'context_applied': {},
        }

    @staticmethod
    def _has_real_totals_line(current_odds: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(current_odds, dict):
            return False
        totals = current_odds.get('大小球')
        if not isinstance(totals, dict):
            return False
        final = totals.get('final') if isinstance(totals.get('final'), dict) else {}
        initial = totals.get('initial') if isinstance(totals.get('initial'), dict) else {}
        return bool(final.get('line') not in (None, '') or initial.get('line') not in (None, ''))

    def hydrate_existing_snapshot_odds(
        self,
        *,
        league_code: str,
        current_odds: Optional[Dict[str, Any]],
        realtime: Dict[str, Any],
        match_id: str,
        home_team: str,
        away_team: str,
        match_date: str,
    ) -> Optional[Dict[str, Any]]:
        if self._has_real_totals_line(current_odds):
            return current_odds
        mid = str(match_id or realtime.get('okooo', {}).get('match_id') or '').strip()
        try:
            found = find_snapshot_for_match(
                self.base_dir,
                league_code,
                match_id=mid,
                home_team=home_team,
                away_team=away_team,
                match_date=match_date,
            )
            if not found:
                # #region debug-point A:snapshot-hydrate-miss
                emit_local_debug_event({"sessionId":"snapshot-routing-chain","runId":"pre-fix","hypothesisId":"A","location":"domain/live.py:82","msg":"[DEBUG] hydrate existing snapshot missed","data":{"league_code":league_code,"match_id":mid,"current_odds_has_totals":self._has_real_totals_line(current_odds),"realtime_match_id":str((realtime or {}).get("okooo", {}).get("match_id") or "")}})
                # #endregion
                realtime['context_applied']['existing_snapshot_odds'] = {
                    'attempted': bool(mid or home_team or away_team),
                    'ok': False,
                    'reason': 'snapshot_not_found' if (mid or home_team or away_team) else 'missing_match_context',
                    'match_id': mid,
                }
                return current_odds
            path, payload = found
            merged = dict(current_odds or {})
            merged.update(extract_okooo_current_odds(payload))
            merged['snapshot_path'] = str(path or '')
            # #region debug-point B:snapshot-hydrate-hit
            emit_local_debug_event({"sessionId":"snapshot-routing-chain","runId":"pre-fix","hypothesisId":"B","location":"domain/live.py:91","msg":"[DEBUG] hydrate existing snapshot hit","data":{"league_code":league_code,"match_id":mid,"snapshot_path":path,"payload_match_id":str(payload.get("match_id") or ""),"payload_home_team":str(payload.get("home_team") or payload.get("team1") or ""),"payload_away_team":str(payload.get("away_team") or payload.get("team2") or ""),"totals_found":bool(isinstance(payload.get("大小球"), dict) and payload.get("大小球", {}).get("found")),"totals_final_line":((payload.get("大小球") or {}).get("final") or {}).get("line"),"merged_totals_final_line":((merged.get("大小球") or {}).get("final") or {}).get("line"),"merged_keys":sorted(list(merged.keys()))[:12]}})
            # #endregion
            realtime['context_applied']['existing_snapshot_odds'] = {
                'attempted': True,
                'ok': True,
                'snapshot_path': path,
                'match_id': str(payload.get('match_id') or mid),
            }
            if isinstance(realtime.get('okooo'), dict):
                realtime['okooo']['snapshot_path'] = str(path or '')
                realtime['okooo']['match_id'] = str(payload.get('match_id') or mid)
            return merged
        except Exception as exc:
            realtime['context_applied']['existing_snapshot_odds'] = {
                'attempted': True,
                'ok': False,
                'reason': 'snapshot_load_error',
                'error': str(exc),
            }
            return current_odds

    def fill_ewma_form(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        match_date: str,
        analysis_context: Dict[str, Any],
        realtime: Dict[str, Any],
    ) -> None:
        try:
            if 'home_form' not in analysis_context or 'away_form' not in analysis_context:
                home_ewma = self.team_ewma_learning.get_team_ewma_features(
                    league_code=league_code,
                    team_name=home_team,
                    match_date=match_date,
                )
                away_ewma = self.team_ewma_learning.get_team_ewma_features(
                    league_code=league_code,
                    team_name=away_team,
                    match_date=match_date,
                )
                if 'home_form' not in analysis_context and isinstance(home_ewma, dict) and home_ewma.get('available'):
                    analysis_context['home_form'] = int(home_ewma.get('form_scale', 3))
                if 'away_form' not in analysis_context and isinstance(away_ewma, dict) and away_ewma.get('available'):
                    analysis_context['away_form'] = int(away_ewma.get('form_scale', 3))
                realtime['context_applied']['ewma_form'] = {'home': home_ewma, 'away': away_ewma}
        except Exception as exc:
            realtime['context_applied']['ewma_form'] = {'attempted': True, 'error': str(exc)}

    def refresh_live_snapshot(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        match_date: str,
        current_odds: Optional[Dict[str, Any]],
        realtime: Dict[str, Any],
        force_refresh_odds: bool,
        okooo_driver: str,
        okooo_headed: bool,
        match_time: str,
        match_id: str,
    ) -> Optional[Dict[str, Any]]:
        if (
            not force_refresh_odds
            or os.environ.get('OKOOO_REFRESH_LIVE', '1') == '0'
            or not home_team
            or not away_team
        ):
            return current_odds

        realtime['okooo']['attempted'] = True
        mid = str(match_id or '')
        realtime['okooo']['warnings'].extend(
            describe_unavailable_okooo_drivers(okooo_driver)
        )
        drivers = build_okooo_driver_chain(okooo_driver)
        if not drivers:
            realtime['okooo']['errors'].append(
                {
                    'driver': okooo_driver,
                    'error': '没有可用的抓取 driver；已跳过实时刷新，请先检查 browser-use / local-chrome 环境',
                }
            )
            return current_odds

        for driver in drivers:
            try:
                refreshed = refresh_okooo_snapshot(
                    self.base_dir,
                    league_code,
                    home_team,
                    away_team,
                    match_date,
                    driver=driver,
                    match_id=mid,
                    headed=bool(okooo_headed),
                    match_time=match_time or '',
                )
                if not refreshed:
                    continue
                path, payload = refreshed
                realtime['okooo']['snapshot_path'] = path or ''
                realtime['okooo']['match_id'] = str(payload.get('match_id') or mid or '')
                realtime['okooo']['refreshed'] = True
                realtime['okooo']['driver'] = driver
                if not snapshot_matches_request(
                    payload,
                    home_team=home_team,
                    away_team=away_team,
                    match_date=match_date,
                ):
                    realtime['okooo']['errors'].append(
                        {
                            'driver': driver,
                            'error': 'snapshot_payload_mismatch',
                            'requested_match_id': mid,
                            'payload_match_id': str(payload.get('match_id') or ''),
                            'payload_home_team': str(payload.get('home_team') or payload.get('team1') or ''),
                            'payload_away_team': str(payload.get('away_team') or payload.get('team2') or ''),
                        }
                    )
                    continue
                # #region debug-point C:live-refresh-hit
                emit_local_debug_event({"sessionId":"snapshot-routing-chain","runId":"pre-fix","hypothesisId":"C","location":"domain/live.py:196","msg":"[DEBUG] live refresh snapshot hit","data":{"league_code":league_code,"home_team":home_team,"away_team":away_team,"requested_match_id":mid,"driver":driver,"snapshot_path":path,"payload_match_id":str(payload.get("match_id") or ""),"payload_home_team":str(payload.get("home_team") or payload.get("team1") or ""),"payload_away_team":str(payload.get("away_team") or payload.get("team2") or ""),"totals_found":bool(isinstance(payload.get("大小球"), dict) and payload.get("大小球", {}).get("found")),"totals_final_line":((payload.get("大小球") or {}).get("final") or {}).get("line")}})
                # #endregion
                merged = extract_okooo_current_odds(payload)
                merged['snapshot_path'] = str(path or '')
                return merged
            except Exception as exc:
                realtime['okooo']['errors'].append({'driver': driver, 'error': str(exc)})
        return current_odds

    def ensure_totals_if_needed(
        self,
        *,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        realtime: Dict[str, Any],
        force_refresh_odds: bool,
        okooo_driver: str,
        okooo_headed: bool,
        match_time: str,
        diag_key: str,
    ) -> Optional[Dict[str, Any]]:
        if not force_refresh_odds:
            realtime['context_applied'][diag_key] = {
                'attempted': False,
                'ok': False,
                'skipped': 'force_refresh_odds=False',
            }
            return current_odds
        try:
            current_odds, diag = auto_fetch_okooo_totals_if_needed(
                base_dir=self.base_dir,
                league_name=(self.league_config.get(league_code, {}) or {}).get('name') or '',
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                current_odds=current_odds,
                analysis_context=analysis_context,
                to_float=self.to_float,
                okooo_driver=okooo_driver,
                okooo_headed=bool(okooo_headed),
                match_time=match_time or '',
                match_id=realtime['okooo']['match_id'],
            )
            # #region debug-point D:totals-fetch-result
            emit_local_debug_event({"sessionId":"snapshot-routing-chain","runId":"pre-fix","hypothesisId":"D","location":"domain/live.py:230","msg":"[DEBUG] totals fetch result","data":{"league_code":league_code,"home_team":home_team,"away_team":away_team,"match_date":match_date,"match_id":str((realtime or {}).get("okooo", {}).get("match_id") or ""),"diag":diag,"current_odds_totals_final_line":((current_odds or {}).get("大小球") or {}).get("final", {}).get("line"),"current_odds_match_id":str((current_odds or {}).get("match_id") or "") if isinstance(current_odds, dict) else ""}})
            # #endregion
            realtime['context_applied'][diag_key] = diag
            return current_odds
        except Exception as exc:
            realtime['context_applied'][diag_key] = {'attempted': True, 'ok': False, 'error': str(exc)}
            return current_odds

    def prepare_prediction_inputs(
        self,
        *,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: Optional[str],
        current_odds: Optional[Dict[str, Any]],
        match_id: str,
        force_refresh_odds: bool,
        okooo_driver: str,
        okooo_headed: bool,
        match_time: str,
        analysis_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        effective_match_date = match_date or datetime.now().strftime('%Y-%m-%d')
        effective_analysis_context = dict(analysis_context or {})
        realtime = self.build_realtime(match_id, okooo_driver, okooo_headed)

        self.fill_ewma_form(
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            match_date=effective_match_date,
            analysis_context=effective_analysis_context,
            realtime=realtime,
        )
        current_odds = self.hydrate_existing_snapshot_odds(
            league_code=league_code,
            current_odds=current_odds,
            realtime=realtime,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            match_date=effective_match_date,
        )
        current_odds = self.refresh_live_snapshot(
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            match_date=effective_match_date,
            current_odds=current_odds,
            realtime=realtime,
            force_refresh_odds=force_refresh_odds,
            okooo_driver=okooo_driver,
            okooo_headed=okooo_headed,
            match_time=match_time,
            match_id=match_id,
        )
        current_odds = self.ensure_totals_if_needed(
            league_code=league_code,
            match_date=effective_match_date,
            home_team=home_team,
            away_team=away_team,
            current_odds=current_odds,
            analysis_context=effective_analysis_context,
            realtime=realtime,
            force_refresh_odds=force_refresh_odds,
            okooo_driver=okooo_driver,
            okooo_headed=okooo_headed,
            match_time=match_time,
            diag_key='okooo_totals_fetch',
        )
        auto_enrich_team_context_if_enabled(
            base_dir=self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            analysis_context=effective_analysis_context,
            realtime_context_applied=realtime['context_applied'],
        )
        cache_params = {
            'schema_v': 6,
            'home': home_team,
            'away': away_team,
            'league': league_code,
            'date': effective_match_date,
            'current_odds': current_odds,
            'match_id': realtime['okooo']['match_id'],
            'force_refresh_odds': force_refresh_odds,
            'okooo_driver': okooo_driver,
            'okooo_headed': bool(okooo_headed),
            'analysis_context': effective_analysis_context,
        }
        return {
            'match_date': effective_match_date,
            'analysis_context': effective_analysis_context,
            'current_odds': current_odds,
            'realtime': realtime,
            'cache_params': cache_params,
        }

    def refresh_report_match_odds(
        self,
        *,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        current_odds: Optional[Dict[str, Any]],
        prefer_existing: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if os.environ.get('OKOOO_REFRESH_LIVE', '1') == '0' or not home_team or not away_team:
            return current_odds
        if prefer_existing and isinstance(current_odds, dict):
            if any(
                current_odds.get(key)
                for key in ('胜平负赔率', '欧赔', '亚值', '大小球', '凯利', '离散率')
            ):
                return current_odds
        try:
            match_id = ''
            if isinstance(current_odds, dict):
                match_id = str(current_odds.get('match_id') or '')
            refreshed = refresh_okooo_snapshot(
                self.base_dir,
                league_code,
                home_team,
                away_team,
                match_date,
                driver='local-chrome',
                match_id=match_id,
            )
            if refreshed:
                _path, payload = refreshed
                return extract_okooo_current_odds(payload)
        except Exception:
            return current_odds
        return current_odds
