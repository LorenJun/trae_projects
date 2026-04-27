#!/usr/bin/env python3
"""
增强版预测比赛流程
整合多模型融合、智能缓存、动态权重调整的完整预测系统
"""

import os
import sys
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
import re
from glob import glob

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Realtime okooo snapshots (refreshed before each prediction)
from okooo_live_snapshot import refresh_snapshot as refresh_okooo_snapshot
from okooo_live_snapshot import extract_current_odds as extract_okooo_current_odds
import subprocess

# 导入机器学习模型
from ml_prediction_models import MultiModelFusion, PoissonModel, DixonColesModel
from result_manager import ResultManager

# 配置日志
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.join(SCRIPT_DIR, '.okooo-scraper', 'runtime')
os.makedirs(RUNTIME_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(RUNTIME_DIR, 'enhanced_prediction.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def _load_team_alias_map(base_dir: str) -> Dict[str, Any]:
    """Load team alias mapping file used across collectors (league -> canonical -> aliases)."""
    try:
        path = os.path.join(base_dir, "okooo_team_aliases.json")
        if not os.path.exists(path):
            return {}
        return json.loads(open(path, "r", encoding="utf-8").read())
    except Exception:
        return {}


def _aliases_for_team(alias_map: Dict[str, Any], league_code: str, team_name: str) -> List[str]:
    league = alias_map.get(league_code) if isinstance(alias_map, dict) else None
    if not isinstance(league, dict):
        return []
    aliases = league.get(team_name)
    return [str(x) for x in aliases] if isinstance(aliases, list) else []


def _derive_form_from_recent(points: Any, matches: Any) -> int:
    """Map recent points to a 1..5 form scale."""
    try:
        pts = int(points)
        m = max(1, int(matches))
    except Exception:
        return 3
    ppg = pts / m
    if ppg >= 2.2:
        return 5
    if ppg >= 1.6:
        return 4
    if ppg >= 1.0:
        return 3
    if ppg >= 0.5:
        return 2
    return 1


def _auto_enrich_team_context_if_enabled(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    analysis_context: Dict[str, Any],
    realtime_context_applied: Dict[str, Any],
) -> None:
    """Best-effort: fetch team state (formation/possession/last lineup/player form) via Sofascore.

    Enable via env:
      ENABLE_TEAM_CONTEXT=1
      TEAM_CONTEXT_LAST_N=5
    """
    # Default ON: we want team_context to be available for richer analysis.
    # Can be disabled via ENABLE_TEAM_CONTEXT=0.
    enabled = os.environ.get("ENABLE_TEAM_CONTEXT", "1").strip() in ("1", "true", "True")
    if not enabled:
        return
    if not isinstance(analysis_context, dict):
        return
    if "team_context" in analysis_context:
        # Caller already provided team context.
        return

    diag: Dict[str, Any] = {"attempted": True, "ok": False, "provider": "sofascore"}
    try:
        from sofascore_team_context import build_match_team_context

        alias_map = _load_team_alias_map(base_dir)
        home_aliases = _aliases_for_team(alias_map, league_code, home_team)
        away_aliases = _aliases_for_team(alias_map, league_code, away_team)
        last_n = int(os.environ.get("TEAM_CONTEXT_LAST_N", "5") or "5")

        tc = build_match_team_context(
            base_dir=base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            home_aliases=home_aliases,
            away_aliases=away_aliases,
            last_n=last_n,
        )
        analysis_context["team_context"] = tc
        diag["ok"] = bool(tc.get("ok"))

        # Derive form scale if not provided by caller.
        if "home_form" not in analysis_context and isinstance(tc.get("home"), dict):
            recent = tc["home"].get("recent") if isinstance(tc["home"].get("recent"), dict) else {}
            analysis_context["home_form"] = _derive_form_from_recent(recent.get("points", 0), recent.get("matches", 5))
        if "away_form" not in analysis_context and isinstance(tc.get("away"), dict):
            recent = tc["away"].get("recent") if isinstance(tc["away"].get("recent"), dict) else {}
            analysis_context["away_form"] = _derive_form_from_recent(recent.get("points", 0), recent.get("matches", 5))

        diag["home_form"] = analysis_context.get("home_form")
        diag["away_form"] = analysis_context.get("away_form")
    except Exception as e:
        diag["error"] = str(e)

    realtime_context_applied["team_context"] = diag


def _external_snapshot_root(base_dir: str) -> str:
    """Project-relative snapshot root (to avoid hardcoding user home paths).

    NOTE: base_dir here is `europe_leagues/` by default for EnhancedPredictor.
    """
    return os.path.join(base_dir, '.okooo-scraper', 'snapshots')


def _update_teams_md_with_enhanced_predictions(teams_path: str, match_date: str, predictions: List[Dict]) -> int:
    """Update teams_2025-26.md schedule rows in-place by appending prediction notes to the last column."""
    try:
        lines = open(teams_path, 'r', encoding='utf-8').read().splitlines(True)
    except Exception:
        return 0

    def norm(s: str) -> str:
        return (s or '').strip().replace(' ', '')

    def _format_upset_note(upset: Any) -> str:
        """Format upset info into a compact single-line note segment for teams_2025-26.md."""
        if isinstance(upset, str):
            return f"爆冷:{upset or '-'}".strip()
        if not isinstance(upset, dict):
            return "爆冷:-"

        level = (upset.get('level') or '').strip() or '-'
        idx = upset.get('index')
        idx_str = ''
        if isinstance(idx, (int, float)):
            idx_str = f"({int(round(float(idx)))})"

        parts = [f"爆冷:{level}{idx_str}"]

        mismatch = upset.get('handicap_strength_mismatch')
        if isinstance(mismatch, dict) and mismatch.get('mismatch_detected'):
            ml = (mismatch.get('mismatch_level') or '').strip() or '是'
            parts.append(f"错配:{ml}")

            suggestion = (mismatch.get('suggested_outcome') or '').strip()
            if suggestion:
                parts.append(f"建议:{suggestion}")

            factors = mismatch.get('warning_factors') or []
            if isinstance(factors, list) and factors:
                # Keep it short to avoid blowing up the schedule table cell.
                compact = ';'.join([str(x).strip() for x in factors[:2] if str(x).strip()])
                if compact:
                    parts.append(f"因子:{compact}")

        knowledge = upset.get('case_knowledge')
        if isinstance(knowledge, dict) and knowledge.get('available'):
            hint = (knowledge.get('hint') or '').strip()
            if hint:
                parts.append(f"案例:{hint}")

        return ' '.join(parts).strip()

    def _format_score_ou_note(pred: Dict[str, Any]) -> str:
        """Keep score/O-U summary short so schedule table cell won't explode."""
        top_scores = pred.get('top_scores') or []
        scores = []
        if isinstance(top_scores, list):
            for item in top_scores[:2]:
                if isinstance(item, (list, tuple)) and item:
                    scores.append(str(item[0]).strip())
        score_note = ''
        if scores:
            score_note = f"比分:{'/'.join(scores)}"

        ou = pred.get('over_under') or {}
        ou_note = ''
        if isinstance(ou, dict):
            line = ou.get('line')
            over_p = ou.get('over')
            under_p = ou.get('under')
            if isinstance(line, (int, float)) and isinstance(over_p, (int, float)) and isinstance(under_p, (int, float)):
                side = '大' if over_p >= under_p else '小'
                prob = max(over_p, under_p)
                ou_note = f"大小:{side}{line:g}({prob:.2f})"

        bits = [x for x in (score_note, ou_note) if x]
        return ' '.join(bits).strip()

    def strip_existing_prediction_fragments(note: str) -> str:
        """Remove any existing prediction fragments from note to avoid duplicate write-backs.

        Supports both formats:
        - New: `...；预测:主胜 信心:0.48 爆冷:低`
        - Legacy: `.../预测主胜✅` / `.../预测平局❌` / `预测 客胜`
        """
        if not note:
            return ''
        base = note
        if '预测:' in base:
            base = base.split('预测:')[0]
        base = re.sub(r'预测\s*(主胜|平局|客胜)\s*[✅❌]?', '', base)
        base = re.sub(r'\s+', ' ', base).strip()
        base = base.rstrip('；; /／').strip()
        return base

    pred_index = {}
    for p in predictions:
        h = norm(p.get('home_team'))
        a = norm(p.get('away_team'))
        if h and a:
            pred_index[(match_date, h, a)] = p

    changed = 0
    out_lines = []
    for line in lines:
        if not line.lstrip().startswith('|'):
            out_lines.append(line)
            continue
        raw = line.strip('\n')
        if raw.count('|') < 6:
            out_lines.append(line)
            continue
        cells = [c.strip() for c in raw.strip().strip('|').split('|')]
        if len(cells) != 6:
            out_lines.append(line)
            continue
        date, _time, home, _score, away, note = cells
        pred = pred_index.get((date, norm(home), norm(away)))
        if not pred:
            out_lines.append(line)
            continue

        # Don't rewrite finished matches (their notes may contain manual correctness marks like ✅/❌).
        if re.match(r'^\d+\s*-\s*\d+$', _score or ''):
            out_lines.append(line)
            continue

        upset = pred.get('upset_potential')
        upset_note = _format_upset_note(upset)
        score_ou_note = _format_score_ou_note(pred)
        conf = float(pred.get('confidence') or 0.0)
        diag = pred.get('applied_model_weights')
        dyn = ''
        if isinstance(diag, dict) and 'has_enough_samples' in diag:
            dyn = '动态调权:已生效' if diag.get('has_enough_samples') else '动态调权:样本不足'

        merged = strip_existing_prediction_fragments(note).rstrip('；; ').strip()
        pred_note = f"预测:{pred.get('prediction')} 信心:{conf:.2f} {score_ou_note} {upset_note}{(' ' + dyn) if dyn else ''}".strip()
        cells[5] = f"{merged}；{pred_note}" if merged else pred_note
        out_lines.append("| " + " | ".join(cells) + " |\n")
        changed += 1

    if changed:
        try:
            with open(teams_path, 'w', encoding='utf-8') as f:
                f.writelines(out_lines)
        except Exception:
            return 0
    return changed


def _external_snapshot_dirs(base_dir: str, league_code: str) -> List[str]:
    dirs = [
        os.path.join(_external_snapshot_root(base_dir), league_code),
        os.path.join(base_dir, 'okooo_snapshots'),
        os.path.join(base_dir, 'okooo_snapshots', league_code),
    ]
    seen = set()
    result = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            result.append(d)
    return result

# 定义联赛配置
LEAGUE_CONFIG = {
    'premier_league': {
        'name': '英超',
        'code': 'premier_league',
        'teams': ['切尔西', '曼联', '利物浦', '阿森纳', '曼城', '阿斯顿维拉', '诺丁汉森林', '布莱顿', '布伦特福德', '富勒姆', '伯恩茅斯', '水晶宫', '埃弗顿', '狼队', '西汉姆联', '热刺', '利兹联', '伯恩利', '桑德兰'],
        'avg_goals': 2.7
    },
    'serie_a': {
        'name': '意甲',
        'code': 'serie_a',
        'teams': ['国际米兰', 'AC米兰', '尤文图斯', '那不勒斯', '罗马', '亚特兰大', '拉齐奥', '佛罗伦萨', '博洛尼亚', '乌迪内斯'],
        'avg_goals': 2.5
    },
    'bundesliga': {
        'name': '德甲',
        'code': 'bundesliga',
        'teams': ['拜仁慕尼黑', '多特蒙德', '勒沃库森', '斯图加特', '柏林联合', '霍芬海姆', '法兰克福', '门兴格拉德巴赫', '沃尔夫斯堡', '美因茨'],
        'avg_goals': 2.9
    },
    'ligue_1': {
        'name': '法甲',
        'code': 'ligue_1',
        'teams': ['巴黎圣日耳曼', '马赛', '摩纳哥', '里尔', '里昂', '朗斯', '雷恩', '尼斯', '洛里昂', '斯特拉斯堡'],
        'avg_goals': 2.4
    },
    'la_liga': {
        'name': '西甲',
        'code': 'la_liga',
        'teams': ['巴塞罗那', '皇家马德里', '马德里竞技', '塞维利亚', '皇家社会', '比利亚雷亚尔', '贝蒂斯', '瓦伦西亚', '毕尔巴鄂竞技', '奥萨苏纳'],
        'avg_goals': 2.6
    }
}

class PredictionCache:
    """智能缓存系统 - 避免重复计算。

    默认关闭（不读不写、不创建目录），以保证预测链路尽量使用实时数据。
    如需开启（用于本地性能优化），设置环境变量：
      ENABLE_PREDICTION_CACHE=1
    """
    
    def __init__(self, cache_dir: str = '.prediction_cache', enabled: Optional[bool] = None):
        if enabled is None:
            enabled = os.getenv('ENABLE_PREDICTION_CACHE', '0') == '1'
        self.enabled = bool(enabled) and bool(cache_dir)
        self.cache_dir = cache_dir
        if self.enabled:
            os.makedirs(cache_dir, exist_ok=True)
    
    def _get_cache_key(self, func_name: str, params: Dict) -> str:
        """生成缓存键"""
        params_str = json.dumps(params, sort_keys=True, default=str)
        key_str = f"{func_name}_{params_str}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, func_name: str, params: Dict, ttl_hours: int = 24) -> Optional[Any]:
        """获取缓存数据"""
        if not self.enabled:
            return None
        cache_key = self._get_cache_key(func_name, params)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        if not os.path.exists(cache_file):
            return None
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查是否过期
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - cache_time > timedelta(hours=ttl_hours):
                os.remove(cache_file)
                return None
            
            return cache_data['data']
        except Exception as e:
            logger.warning(f"读取缓存失败: {e}")
            return None
    
    def set(self, func_name: str, params: Dict, data: Any):
        """设置缓存数据"""
        if not self.enabled:
            return
        cache_key = self._get_cache_key(func_name, params)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"写入缓存失败: {e}")

class DynamicWeightAdjuster:
    """动态权重调整器 - 根据历史准确率调整模型权重"""
    
    def __init__(self, history_file: str = ''):
        if not history_file:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            history_file = os.path.join(base_dir, '.okooo-scraper', 'runtime', 'accuracy_stats.json')
        self.history_file = history_file
        self.accuracy_history = self._load_history()
        # 调权保护机制：样本不足时避免“乱调”
        self.min_league_samples = 30          # 联赛样本量门槛
        self.min_model_samples = 20           # 子模型 n 门槛（低于则不参与调权）
        self.max_adjustment_ratio = 0.10      # 每次调权最大偏离默认权重比例（±10%）
        self.shrink_base = 0.8                # 收缩到默认权重：new = base*shrink + adj*(1-shrink)
    
    def _load_history(self) -> Dict:
        """加载历史准确率数据"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载历史数据失败: {e}")
        return {}
    
    @staticmethod
    def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return MultiModelFusion.MODEL_WEIGHTS.copy()
        return {k: v / total_weight for k, v in weights.items()}

    def get_adjusted_weights(self, league_code: str) -> Dict[str, float]:
        """获取调整后的权重（带样本量保护、收缩与n门槛）"""
        base_weights = self._normalize_weights(MultiModelFusion.MODEL_WEIGHTS.copy())
        
        # 新版统计文件结构: {'overall':..., 'by_league': {...}}
        by_league = self.accuracy_history.get('by_league', {})
        if league_code not in by_league:
            return base_weights

        league_acc = by_league[league_code]
        total_predictions = int(league_acc.get('total_predictions', 0) or 0)
        model_accuracy: Dict[str, float] = league_acc.get('model_accuracy', {}) or {}

        # 1) 联赛样本量门槛：不足则直接返回默认权重
        if total_predictions < self.min_league_samples:
            return base_weights

        # 2) 子模型 n 门槛：低样本模型不参与调权
        # 当前统计口径里每个模型的有效样本量不单独落盘，这里先用“联赛样本量”作为保守下界。
        # 若未来补充 model_total_* 字段，可替换为真实 n。
        eligible_models = {
            model_name for model_name in model_accuracy.keys()
            if model_name in base_weights and total_predictions >= self.min_model_samples
        }

        adjusted = base_weights.copy()
        for model_name, acc in model_accuracy.items():
            if model_name not in eligible_models:
                continue
            # 准确率高的模型获得更高权重，低的更低：0.5~2.0 倍
            adjustment_factor = 0.5 + (float(acc) * 1.5)
            adjusted[model_name] *= adjustment_factor

        adjusted = self._normalize_weights(adjusted)

        # 3) 限幅：限制相对默认权重的偏离幅度，避免短期波动拉飞
        capped = {}
        for model_name, base_w in base_weights.items():
            target = adjusted.get(model_name, base_w)
            lo = base_w * (1.0 - self.max_adjustment_ratio)
            hi = base_w * (1.0 + self.max_adjustment_ratio)
            capped[model_name] = min(max(target, lo), hi)
        capped = self._normalize_weights(capped)

        # 4) 收缩：向默认权重回归
        shrink = float(self.shrink_base)
        final = {
            model_name: base_weights[model_name] * shrink + capped[model_name] * (1.0 - shrink)
            for model_name in base_weights
        }
        return self._normalize_weights(final)

    def get_adjustment_diagnostics(self, league_code: str) -> Dict[str, Any]:
        """返回调权诊断信息，便于在预测输出中解释权重来源。"""
        base_weights = self._normalize_weights(MultiModelFusion.MODEL_WEIGHTS.copy())
        by_league = self.accuracy_history.get('by_league', {})
        league_acc = by_league.get(league_code, {}) if isinstance(by_league, dict) else {}
        total_predictions = int(league_acc.get('total_predictions', 0) or 0)
        model_accuracy = league_acc.get('model_accuracy', {}) or {}

        final = self.get_adjusted_weights(league_code)
        has_enough_samples = total_predictions >= self.min_league_samples

        return {
            'league_code': league_code,
            'league_total_predictions': total_predictions,
            'min_league_samples': self.min_league_samples,
            'min_model_samples': self.min_model_samples,
            'max_adjustment_ratio': self.max_adjustment_ratio,
            'shrink_base': self.shrink_base,
            'has_enough_samples': has_enough_samples,
            'model_accuracy_keys': sorted([k for k in model_accuracy.keys() if k in base_weights]),
            'base_weights': base_weights,
            'final_weights': final,
        }

class TeamDataManager:
    """球队数据管理器"""
    
    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.cache = PredictionCache()
    
    def get_player_data_path(self, league_code: str, team_name: str) -> str:
        """获取球员数据文件路径"""
        return os.path.join(self.base_dir, league_code, 'players', f"{team_name}.json")
    
    def load_player_data(self, league_code: str, team_name: str) -> Optional[Dict]:
        """加载球队球员数据"""
        cached = self.cache.get('load_player_data', {'league': league_code, 'team': team_name})
        if cached:
            return cached
        
        file_path = self.get_player_data_path(league_code, team_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.cache.set('load_player_data', {'league': league_code, 'team': team_name}, data)
                    return data
            except Exception as e:
                logger.warning(f"加载球员数据失败 {team_name}: {e}")
        return None
    
    def analyze_team_strength(self, league_code: str, team_name: str) -> Dict:
        """分析球队实力（增强版）"""
        cache_params = {'league': league_code, 'team': team_name}
        cached = self.cache.get('analyze_team_strength', cache_params)
        if cached:
            return cached
        
        team_data = self.load_player_data(league_code, team_name)
        
        if not team_data:
            result = {
                'team': team_name,
                'strength': 50.0,
                'attack': 1.0,
                'defense': 1.0,
                'injured_count': 0,
                'suspended_count': 0,
                'key_players_available': True,
                'available_value': 0,
                'total_value': 0
            }
        else:
            # 统计球员状态
            players = team_data.get('players', [])
            injured_players = [p for p in players if p.get('transfer_status') == 'injured']
            suspended_players = [p for p in players if p.get('transfer_status') == 'suspended']
            available_players = [p for p in players if p.get('transfer_status') == 'current']
            
            total_value = sum(p.get('market_value', 0) for p in players)
            available_value = sum(p.get('market_value', 0) for p in available_players)
            
            # 计算攻防能力
            attack_players = [p for p in available_players if p.get('position') in ['前锋', '中场']]
            defense_players = [p for p in available_players if p.get('position') in ['后卫', '门将']]
            
            attack = 1.0
            if attack_players:
                attack = sum(p.get('market_value', 0) for p in attack_players) / len(attack_players) / 50 + 0.5
                attack = max(0.5, min(1.5, attack))
            
            defense = 1.0
            if defense_players:
                defense = sum(p.get('market_value', 0) for p in defense_players) / len(defense_players) / 50 + 0.5
                defense = max(0.5, min(1.5, defense))
            
            # 基础实力值
            base_strength = 50.0
            if total_value > 0:
                value_ratio = available_value / total_value
                strength_adjustment = (value_ratio - 0.5) * 50
                base_strength += strength_adjustment
            
            # 根据市场价值调整
            avg_value = total_value / len(players) if players else 0
            value_strength = min(50, avg_value / 2)
            base_strength += value_strength
            
            strength = max(10, min(95, base_strength))
            
            # 检查核心球员
            key_positions = ['前锋', '中场', '后卫', '门将']
            key_players_available = True
            for pos in key_positions:
                pos_players = [p for p in available_players if p.get('position') == pos]
                if not pos_players:
                    key_players_available = False
                    break
            
            result = {
                'team': team_name,
                'strength': strength,
                'attack': attack,
                'defense': defense,
                'injured_count': len(injured_players),
                'suspended_count': len(suspended_players),
                'key_players_available': key_players_available,
                'available_value': available_value,
                'total_value': total_value
            }
        
        self.cache.set('analyze_team_strength', cache_params, result)
        return result

class HistoricalOddsReference:
    """历史赔率参考库。

    用于在拿到当前赔率快照时，回看历史上“开盘/终盘/凯利/离散率/亚值变化”相近的比赛，
    给预测结果一个可解释的参考样本。
    """

    FEATURE_FIELDS = [
        ('胜平负赔率', 'initial', 'home'),
        ('胜平负赔率', 'initial', 'draw'),
        ('胜平负赔率', 'initial', 'away'),
        ('胜平负赔率', 'final', 'home'),
        ('胜平负赔率', 'final', 'draw'),
        ('胜平负赔率', 'final', 'away'),
        ('欧赔', 'initial', 'home'),
        ('欧赔', 'initial', 'draw'),
        ('欧赔', 'initial', 'away'),
        ('欧赔', 'final', 'home'),
        ('欧赔', 'final', 'draw'),
        ('欧赔', 'final', 'away'),
        ('亚值', 'initial', 'home_water'),
        ('亚值', 'initial', 'handicap_value'),
        ('亚值', 'initial', 'away_water'),
        ('亚值', 'final', 'home_water'),
        ('亚值', 'final', 'handicap_value'),
        ('亚值', 'final', 'away_water'),
        ('凯利', 'initial', 'home'),
        ('凯利', 'initial', 'draw'),
        ('凯利', 'initial', 'away'),
        ('凯利', 'final', 'home'),
        ('凯利', 'final', 'draw'),
        ('凯利', 'final', 'away'),
        ('离散率', 'initial', 'home'),
        ('离散率', 'initial', 'draw'),
        ('离散率', 'initial', 'away'),
        ('离散率', 'final', 'home'),
        ('离散率', 'final', 'draw'),
        ('离散率', 'final', 'away'),
    ]

    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.cache = PredictionCache()
        self.records_by_league = self._load_odds_history()
        self.feature_stats = self._build_feature_stats()

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None

    def _load_odds_history(self) -> Dict[str, List[Dict]]:
        records_by_league: Dict[str, List[Dict]] = {}
        for league_code in LEAGUE_CONFIG:
            odds_dir = os.path.join(self.base_dir, league_code, 'analysis', 'odds')
            records_by_league[league_code] = []
            if not os.path.isdir(odds_dir):
                continue

            for file_path in sorted(glob(os.path.join(odds_dir, '*_odds.json'))):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                    for match in payload.get('matches', []):
                        records_by_league[league_code].append(match)
                except Exception as e:
                    logger.warning(f"加载历史赔率文件失败 {file_path}: {e}")
        return records_by_league

    def _build_feature_stats(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        """为每个联赛、每个特征建立均值/标准差，用于相似度标准化。"""
        stats: Dict[str, Dict[str, Dict[str, float]]] = {}
        for league_code, matches in self.records_by_league.items():
            values_by_key: Dict[str, List[float]] = {}
            for match in matches:
                vec = self._extract_feature_vector(match)
                for k, v in vec.items():
                    values_by_key.setdefault(k, []).append(v)

            league_stats: Dict[str, Dict[str, float]] = {}
            for k, vals in values_by_key.items():
                if not vals:
                    continue
                mean = sum(vals) / len(vals)
                var = sum((x - mean) ** 2 for x in vals) / max(1, len(vals) - 1)
                std = var ** 0.5
                if std == 0:
                    std = 1.0
                league_stats[k] = {'mean': mean, 'std': std}
            stats[league_code] = league_stats
        return stats

    def get_league_record_count(self, league_code: str) -> int:
        return len(self.records_by_league.get(league_code, []))

    def _extract_feature_vector(self, odds_snapshot: Dict) -> Dict[str, float]:
        vector: Dict[str, float] = {}
        for group, phase, key in self.FEATURE_FIELDS:
            value = (
                odds_snapshot.get(group, {})
                .get(phase, {})
                .get(key)
            )
            numeric = self._safe_float(value)
            if numeric is not None:
                vector[f'{group}.{phase}.{key}'] = numeric
        return vector

    def _build_result_summary(self, matches: List[Dict]) -> Dict[str, Any]:
        summary = {'主胜': 0, '平局': 0, '客胜': 0}
        for match in matches:
            actual = match.get('actual_result')
            if actual in summary:
                summary[actual] += 1

        total = len(matches)
        cold_count = 0
        for match in matches:
            win_odds = (
                match.get('胜平负赔率', {})
                .get('final', {})
            )
            actual = match.get('actual_result')
            if actual == '主胜':
                actual_odds = self._safe_float(win_odds.get('home'))
            elif actual == '平局':
                actual_odds = self._safe_float(win_odds.get('draw'))
            elif actual == '客胜':
                actual_odds = self._safe_float(win_odds.get('away'))
            else:
                actual_odds = None

            if actual_odds is not None and actual_odds >= 3.5:
                cold_count += 1

        return {
            'sample_size': total,
            'result_counts': summary,
            'result_rates': {
                key: (value / total if total else 0.0)
                for key, value in summary.items()
            },
            'cold_result_count': cold_count,
            'cold_result_rate': (cold_count / total if total else 0.0),
        }

    def find_similar_matches(
        self,
        league_code: str,
        current_odds: Dict,
        top_k: int = 5,
        exclude_match_id: Optional[str] = None
    ) -> Dict[str, Any]:
        cache_params = {
            'algo_v': 2,
            'league': league_code,
            'odds': current_odds,
            'top_k': top_k,
            'exclude': exclude_match_id
        }
        cached = self.cache.get('find_similar_odds_matches', cache_params)
        if cached:
            return cached

        current_vector = self._extract_feature_vector(current_odds)
        history = self.records_by_league.get(league_code, [])
        if not current_vector or not history:
            result = {
                'available': False,
                'league_history_count': len(history),
                'matched_feature_count': 0,
                'similar_matches': [],
                'summary': self._build_result_summary([]),
                'insights': []
            }
            self.cache.set('find_similar_odds_matches', cache_params, result)
            return result

        # 分组权重，避免高量纲字段（如离散率）主导距离
        group_weights = {
            '胜平负赔率': 1.0,
            '欧赔': 1.0,
            '亚值': 1.0,
            '凯利': 0.8,
            '离散率': 0.6
        }

        feature_stats = self.feature_stats.get(league_code, {})
        scored_matches = []
        for match in history:
            if exclude_match_id and match.get('match_id') == exclude_match_id:
                continue
            historical_vector = self._extract_feature_vector(match)
            common_keys = set(current_vector) & set(historical_vector)
            if len(common_keys) < 6:
                continue

            distance = 0.0
            weight_sum = 0.0
            for key in common_keys:
                stats = feature_stats.get(key)
                if stats:
                    cur = (current_vector[key] - stats['mean']) / stats['std']
                    hist = (historical_vector[key] - stats['mean']) / stats['std']
                    diff = abs(cur - hist)
                else:
                    # 兜底：没有统计时退化为绝对差
                    diff = abs(current_vector[key] - historical_vector[key])

                group = key.split('.', 1)[0]
                w = group_weights.get(group, 1.0)
                distance += diff * w
                weight_sum += w

            avg_distance = distance / max(1.0, weight_sum)
            similarity = max(0.0, 1.0 / (1.0 + avg_distance))
            scored_matches.append({
                'match_id': match.get('match_id'),
                'match_date': match.get('match_date'),
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'actual_score': match.get('actual_score'),
                'actual_result': match.get('actual_result'),
                'page_id': match.get('page_id'),
                'similarity': similarity,
                'distance': avg_distance,
                'matched_feature_count': len(common_keys),
                '胜平负赔率': match.get('胜平负赔率', {}),
                '欧赔': match.get('欧赔', {}),
                '亚值': match.get('亚值', {}),
                '凯利': match.get('凯利', {}),
                '离散率': match.get('离散率', {}),
            })

        scored_matches.sort(key=lambda item: (-item['similarity'], item['distance']))
        top_matches = scored_matches[:top_k]
        summary = self._build_result_summary(top_matches)

        insights = []
        if summary['sample_size'] >= 3:
            cold_rate = summary['cold_result_rate']
            if cold_rate >= 0.4:
                insights.append('相似赔率样本中高赔赛果占比较高，需防范热门方向失真')
            dominant_result, dominant_rate = max(
                summary['result_rates'].items(),
                key=lambda item: item[1]
            )
            if dominant_rate >= 0.6:
                insights.append(f"相似赔率样本主要落在{dominant_result}({dominant_rate:.0%})")

        result = {
            'available': bool(top_matches),
            'league_history_count': len(history),
            'matched_feature_count': max(
                (item['matched_feature_count'] for item in top_matches),
                default=0
            ),
            'similar_matches': top_matches,
            'summary': summary,
            'insights': insights,
        }
        self.cache.set('find_similar_odds_matches', cache_params, result)
        return result

class UpsetAnalyzer:
    """爆冷分析器"""
    
    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.upset_cases = self._load_upset_cases()
        self.cache = PredictionCache()

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _case_get(case: Dict, *keys: str) -> Any:
        for k in keys:
            if k in case:
                return case.get(k)
        return None

    @staticmethod
    def _case_int(case: Dict, *keys: str, default: int = 0) -> int:
        value = UpsetAnalyzer._case_get(case, *keys)
        try:
            if value in (None, ''):
                return default
            return int(float(value))
        except Exception:
            return default

    @staticmethod
    def _extract_case_tags(case: Dict) -> set:
        """Extract coarse tags from a knowledge-base case for similarity matching."""
        text_fields = []
        for key in ('爆冷类型', '盘口异常', '赔率变化', '凯利指数', '伤病影响', '战术变化', '心理因素', '爆冷原因分析'):
            val = UpsetAnalyzer._case_get(case, key)
            if val:
                text_fields.append(str(val))
        text = ' '.join(text_fields)

        tags = set()
        if '降盘' in text or '降' in (UpsetAnalyzer._case_get(case, '盘口异常') or ''):
            tags.add('handicap_down')
        if '升盘' in text or '升' in (UpsetAnalyzer._case_get(case, '盘口异常') or ''):
            tags.add('handicap_up')
        if '平手' in text:
            tags.add('handicap_level')
        if '偏高' in text:
            tags.add('kelly_or_water_high')
        if '伤' in text or '缺阵' in text:
            tags.add('injury_or_absence')
        if '轮换' in text or '欧战' in text or '杯' in text:
            tags.add('rotation_or_cup')
        if '战意' in text or '保级' in text or '争冠' in text:
            tags.add('motivation')
        if '主场' in text:
            tags.add('home_boost')
        if '平局' in text:
            tags.add('draw_risk')
        if '赔率' in text and '上升' in text:
            tags.add('odds_up')
        if '赔率' in text and '下降' in text:
            tags.add('odds_down')
        return tags

    @staticmethod
    def _extract_current_tags(
        strength_diff: float,
        asian_handicap: Optional[Dict],
        european_odds: Optional[Dict],
        mismatch_analysis: Optional[Dict],
    ) -> set:
        """Extract coarse tags from current match context for similarity matching."""
        tags = set()
        if abs(float(strength_diff or 0.0)) >= 20:
            tags.add('big_gap')
        if mismatch_analysis and mismatch_analysis.get('mismatch_detected'):
            tags.add('handicap_strength_mismatch')
            tags.add(f"mismatch_{mismatch_analysis.get('mismatch_level')}")

        # Draw-odds low heuristic
        if isinstance(european_odds, dict):
            final_odds = european_odds.get('final', {}) or {}
            draw_odds = UpsetAnalyzer._to_float(final_odds.get('draw'), 0.0)
            if draw_odds and draw_odds < 3.2:
                tags.add('draw_risk')

        # Handicap movement heuristic (if initial/final both present)
        if isinstance(asian_handicap, dict):
            fin = asian_handicap.get('final', {}) or {}
            ini = asian_handicap.get('initial', {}) or {}
            fin_v = str(fin.get('handicap_value') or '')
            ini_v = str(ini.get('handicap_value') or '')
            if fin_v and ini_v and fin_v != ini_v:
                tags.add('handicap_move')
        return tags

    def _find_similar_cases(
        self,
        league_name: str,
        strength_diff: float,
        asian_handicap: Optional[Dict],
        european_odds: Optional[Dict],
        mismatch_analysis: Optional[Dict],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Similarity retrieval over the upset case library (as a lightweight knowledge base)."""
        cases = self._league_cases(league_name)
        if not cases:
            return []

        cur_tags = self._extract_current_tags(strength_diff, asian_handicap, european_odds, mismatch_analysis)
        cur_gap = abs(float(strength_diff or 0.0))

        scored = []
        for case in cases:
            # Skip unverified cases to reduce noise.
            level = str(self._case_get(case, '爆冷等级') or '')
            actual = str(self._case_get(case, '实际结果') or '')
            if level == '待验证' or actual == '待验证':
                continue

            # Approximate "gap" using ranking/points differences from the case as a proxy for strength gap.
            rank_gap = abs(self._case_int(case, '排名差', default=0))
            pts_gap = abs(self._case_int(case, '积分差', default=0))
            case_gap = rank_gap * 2.0 + pts_gap * 0.8

            # Gap similarity: 1/(1+delta) in a normalized space.
            gap_delta = abs(cur_gap - case_gap)
            gap_sim = 1.0 / (1.0 + (gap_delta / 10.0))

            case_tags = self._extract_case_tags(case)
            union = cur_tags | case_tags
            tag_sim = (len(cur_tags & case_tags) / len(union)) if union else 0.0

            score = 0.6 * tag_sim + 0.4 * gap_sim
            scored.append((score, tag_sim, gap_sim, case))

        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        top = []
        for score, tag_sim, gap_sim, case in scored[:top_k]:
            top.append({
                'case_id': self._case_get(case, '案例ID') or '',
                'match_date': self._case_get(case, '比赛日期') or '',
                'home_team': self._case_get(case, '主队') or '',
                'away_team': self._case_get(case, '客队') or '',
                'upset_level': self._case_get(case, '爆冷等级') or '',
                'upset_type': self._case_get(case, '爆冷类型') or '',
                'reason': self._case_get(case, '爆冷原因分析') or '',
                'suggestion': self._case_get(case, '改进建议') or '',
                'score': round(float(score), 3),
            })
        return top
    
    def _league_cases(self, league_name: str) -> List[Dict]:
        return [
            case for case in self.upset_cases
            if self._case_get(case, 'league', '联赛') == league_name
        ]
    
    def _load_upset_cases(self) -> List[Dict]:
        """加载爆冷案例库"""
        file_path = os.path.join(self.base_dir, '爆冷案例库.json')
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载爆冷案例库失败: {e}")
        return []
    
    def analyze_handicap_vs_strength(
        self,
        home_team: str,
        away_team: str,
        strength_diff: float,
        asian_handicap: Optional[Dict] = None,
        european_odds: Optional[Dict] = None
    ) -> Dict:
        """分析强队实力指数与让步数据是否匹配 - 新增爆冷预警核心逻辑
        
        当强队实力明显占优但盘口/赔率让步不足时，触发爆冷预警
        
        Args:
            home_team: 主队名称
            away_team: 客队名称
            strength_diff: 实力差距 (正值表示主队强，负值表示客队强)
            asian_handicap: 亚盘数据 {'initial': {...}, 'final': {...}}
            european_odds: 欧赔数据 {'initial': {...}, 'final': {...}}
            
        Returns:
            {
                'mismatch_detected': bool,  # 是否检测到不匹配
                'mismatch_level': str,      # '高'/'中'/'低'
                'strong_team': str,         # 强队名称
                'weak_team': str,           # 弱队名称
                'strength_advantage': float, # 实力优势值
                'handicap_advantage': float, # 盘口优势值
                'gap': float,               # 差距值
                'warning_factors': List[str], # 预警因素
                'suggested_outcome': str    # 建议投注方向
            }
        """
        result = {
            'mismatch_detected': False,
            'mismatch_level': '低',
            'strong_team': '',
            'weak_team': '',
            'strength_advantage': 0.0,
            'handicap_advantage': 0.0,
            'gap': 0.0,
            'warning_factors': [],
            'suggested_outcome': ''
        }
        
        if not asian_handicap and not european_odds:
            return result
            
        # 确定哪方是强队
        if strength_diff > 0:
            result['strong_team'] = home_team
            result['weak_team'] = away_team
            result['strength_advantage'] = strength_diff
        else:
            result['strong_team'] = away_team
            result['weak_team'] = home_team
            result['strength_advantage'] = abs(strength_diff)
            
        # 实力优势必须足够大才进行分析
        if result['strength_advantage'] < 10:
            return result
            
        warning_factors = []
        gap = 0.0
        
        # 1. 亚盘分析 - 强队让球是否足够
        if asian_handicap:
            final_handicap = asian_handicap.get('final', {})
            handicap_value = self._to_float(final_handicap.get('handicap_value'), 0.0)
            home_water = self._to_float(final_handicap.get('home_water'), 0.0)
            away_water = self._to_float(final_handicap.get('away_water'), 0.0)
            
            # 转换盘口为数值
            handicap_num = 0.0
            if handicap_value:
                # 处理类似 "0.5", "1", "1/1.5" 等格式
                if '/' in str(handicap_value):
                    parts = str(handicap_value).split('/')
                    handicap_num = (float(parts[0]) + float(parts[1])) / 2
                else:
                    handicap_num = float(handicap_value)
                    
            # 判断强队是主队还是客队
            is_strong_home = result['strong_team'] == home_team
            
            # 强队让球不足的情况
            if is_strong_home:
                # 主队是强队，应该让球
                if handicap_num < 0.5 and result['strength_advantage'] >= 20:
                    gap = 20 - handicap_num * 10
                    warning_factors.append(f"{home_team}实力强{result['strength_advantage']:.0f}分但仅让{handicap_value}球，盘口过浅")
                elif handicap_num < 0.25 and result['strength_advantage'] >= 15:
                    gap = 15 - handicap_num * 10
                    warning_factors.append(f"{home_team}实力占优但盘口让球不足")
            else:
                # 客队是强队，应该受让或让球
                if handicap_num > -0.5 and result['strength_advantage'] >= 20:
                    gap = 20 + handicap_num * 10
                    warning_factors.append(f"{away_team}实力强{result['strength_advantage']:.0f}分但盘口{handicap_value}球，未获足够支持")
                    
            # 水位异常 - 强队水位过高
            if is_strong_home and home_water > 1.0:
                gap += 5
                warning_factors.append(f"{home_team}水位偏高({home_water})，庄家赔付压力大")
            elif not is_strong_home and away_water > 1.0:
                gap += 5
                warning_factors.append(f"{away_team}水位偏高({away_water})，庄家赔付压力大")
                
        # 2. 欧赔分析 - 强队赔率是否过高
        if european_odds:
            final_odds = european_odds.get('final', {})
            home_odds = self._to_float(final_odds.get('home'), 0.0)
            draw_odds = self._to_float(final_odds.get('draw'), 0.0)
            away_odds = self._to_float(final_odds.get('away'), 0.0)
            
            is_strong_home = result['strong_team'] == home_team
            
            # 根据实力差距计算理论赔率
            if result['strength_advantage'] >= 25:
                expected_strong_odds = 1.3
            elif result['strength_advantage'] >= 20:
                expected_strong_odds = 1.5
            elif result['strength_advantage'] >= 15:
                expected_strong_odds = 1.7
            elif result['strength_advantage'] >= 10:
                expected_strong_odds = 1.9
            else:
                expected_strong_odds = 2.1
                
            actual_strong_odds = home_odds if is_strong_home else away_odds
            
            if actual_strong_odds > 0 and actual_strong_odds > expected_strong_odds * 1.15:
                odds_gap = (actual_strong_odds - expected_strong_odds) / expected_strong_odds * 100
                gap += odds_gap
                warning_factors.append(f"{result['strong_team']}赔率{actual_strong_odds}高于理论值{expected_strong_odds:.2f}，机构不看好")
                
            # 平局赔率偏低 - 防范冷门信号
            if draw_odds > 0 and draw_odds < 3.2 and result['strength_advantage'] >= 15:
                gap += 8
                warning_factors.append(f"平局赔率{draw_odds}偏低，机构防范冷门")
                
        # 3. 综合判断
        result['gap'] = gap
        result['warning_factors'] = warning_factors
        
        if gap >= 30:
            result['mismatch_level'] = '高'
            result['mismatch_detected'] = True
        elif gap >= 15:
            result['mismatch_level'] = '中'
            result['mismatch_detected'] = True
        elif gap >= 5:
            result['mismatch_level'] = '低'
            
        # 建议投注方向
        if result['mismatch_detected']:
            if result['mismatch_level'] == '高':
                result['suggested_outcome'] = f"防范冷门 - {result['weak_team']}不败或平局"
            elif result['mismatch_level'] == '中':
                result['suggested_outcome'] = f"谨慎 - {result['weak_team']}+1球或小球"
            else:
                result['suggested_outcome'] = f"观望 - {result['strong_team']}小胜或平局"
        else:
            result['suggested_outcome'] = f"正常 - {result['strong_team']}胜"
            
        return result

    def assess_upset_potential(
        self,
        home_team: str,
        away_team: str,
        league_code: str,
        strength_diff: float,
        home_strength: Dict,
        away_strength: Dict,
        predicted_outcome: Optional[str] = None,
        confidence: Optional[float] = None,
        historical_odds_reference: Optional[Dict] = None,
        asian_handicap: Optional[Dict] = None,
        european_odds: Optional[Dict] = None
    ) -> Dict:
        """评估爆冷可能性（增强版）"""
        cache_params = {
            'home': home_team, 'away': away_team, 'league': league_code,
            'diff': strength_diff,
            'pred': predicted_outcome,
            'conf': None if confidence is None else round(float(confidence), 3),
            'odds_ref': historical_odds_reference,
            'asian_handicap': asian_handicap,
            'european_odds': european_odds,
        }
        cached = self.cache.get('assess_upset_potential', cache_params)
        if cached:
            return cached
        
        upset_index = 0.0
        factors = []
        mismatch_analysis = None
        
        # 1. 实力差距因素
        if abs(strength_diff) > 20:
            upset_index += 30
            factors.append(f"实力差距大({strength_diff:+.1f})")
        elif abs(strength_diff) > 10:
            upset_index += 15
        
        # 2. 历史爆冷案例（精确匹配 + 模式学习）
        league_name = LEAGUE_CONFIG[league_code]['name']
        league_cases = self._league_cases(league_name)
        similar_cases = []
        for case in league_cases:
            c_home = self._case_get(case, 'home_team', '主队')
            c_away = self._case_get(case, 'away_team', '客队')
            if not c_home or not c_away:
                continue
            if (
                (c_home == home_team and c_away == away_team) or
                (c_home == away_team and c_away == home_team)
            ):
                similar_cases.append(case)
        
        if similar_cases:
            upset_index += len(similar_cases) * 15
            factors.append(f"历史爆冷案例({len(similar_cases)}个)")

        # 基于案例库做“模式学习”：同一联赛中，历史上与当前预测方向相同但被反打的比例越高，爆冷指数越高。
        if predicted_outcome:
            opposite_cases = []
            for case in league_cases:
                pred = self._case_get(case, 'predicted_outcome', '预测结果')
                actual = self._case_get(case, 'actual_outcome', '实际结果')
                if pred == predicted_outcome and actual and actual != predicted_outcome:
                    opposite_cases.append(case)

            if opposite_cases:
                boost = min(20.0, len(opposite_cases) * 3.0)
                upset_index += boost
                factors.append(f"历史同向反打({len(opposite_cases)}次)")

                super_cold = 0
                for case in opposite_cases:
                    odds = self._to_float(self._case_get(case, 'upset_odds', '实际爆冷赔率'), 0.0)
                    if odds >= 5.0:
                        super_cold += 1
                if super_cold:
                    upset_index += min(10.0, super_cold * 5.0)
                    factors.append(f"历史超级冷门({super_cold}次)")

            # 经验规律：强热门且高信心时，若信息面不足（战意/轮换/临场）很容易“过热被穿”
            if confidence is not None and confidence >= 0.70 and abs(strength_diff) >= 15:
                upset_index += 5.0
                factors.append("强热门需防过热")
        
        # 3. 伤病因素
        if home_strength.get('injured_count', 0) >= 3:
            upset_index += 20
            factors.append(f"{home_team}伤病严重({home_strength['injured_count']}人)")
        elif home_strength.get('injured_count', 0) >= 2:
            upset_index += 10
        
        if away_strength.get('injured_count', 0) >= 3:
            upset_index += 20
            factors.append(f"{away_team}伤病严重({away_strength['injured_count']}人)")
        elif away_strength.get('injured_count', 0) >= 2:
            upset_index += 10
        
        # 4. 核心球员缺席
        if not home_strength.get('key_players_available', True):
            upset_index += 25
            factors.append(f"{home_team}核心球员缺席")
        
        if not away_strength.get('key_players_available', True):
            upset_index += 25
            factors.append(f"{away_team}核心球员缺席")

        # 5. 历史相似赔率参考
        if historical_odds_reference and historical_odds_reference.get('available'):
            summary = historical_odds_reference.get('summary', {})
            sample_size = summary.get('sample_size', 0)
            cold_rate = summary.get('cold_result_rate', 0.0)
            result_rates = summary.get('result_rates', {})

            if sample_size >= 3:
                upset_index += min(15.0, cold_rate * 25.0)
                if cold_rate >= 0.4:
                    factors.append(f"相似赔率冷门占比高({cold_rate:.0%})")

                if predicted_outcome:
                    reverse_rate = 1.0 - result_rates.get(predicted_outcome, 0.0)
                    if reverse_rate >= 0.6:
                        upset_index += min(10.0, reverse_rate * 10.0)
                        factors.append(f"相似赔率反向结果偏多({reverse_rate:.0%})")
        
        # 6. 【新增】强队实力指数与让步数据不匹配分析
        if asian_handicap or european_odds:
            mismatch_analysis = self.analyze_handicap_vs_strength(
                home_team=home_team,
                away_team=away_team,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                european_odds=european_odds
            )
            
            if mismatch_analysis.get('mismatch_detected'):
                gap = mismatch_analysis.get('gap', 0)
                level = mismatch_analysis.get('mismatch_level', '低')
                
                # 根据不匹配程度增加爆冷指数
                if level == '高':
                    upset_index += min(35, gap)
                elif level == '中':
                    upset_index += min(20, gap)
                else:
                    upset_index += min(10, gap)
                    
                # 添加不匹配因素到列表
                warning_factors = mismatch_analysis.get('warning_factors', [])
                for factor in warning_factors:
                    if factor not in factors:
                        factors.append(f"[实力-盘口不匹配] {factor}")

        # 7. 【新增】爆冷案例库知识检索（相似案例 Top-K），用于解释与复盘
        knowledge = {'available': False, 'top_cases': [], 'hint': ''}
        try:
            league_name = LEAGUE_CONFIG[league_code]['name']
            top_cases = self._find_similar_cases(
                league_name=league_name,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                european_odds=european_odds,
                mismatch_analysis=mismatch_analysis,
                top_k=3,
            )
            if top_cases:
                knowledge['available'] = True
                knowledge['top_cases'] = top_cases
                best = top_cases[0]
                # One-line hint for schedule write-back.
                hint_bits = []
                if best.get('upset_level'):
                    hint_bits.append(str(best['upset_level']))
                if best.get('upset_type') and best['upset_type'] != '无':
                    hint_bits.append(str(best['upset_type']))
                knowledge['hint'] = f"{best.get('home_team','')}vs{best.get('away_team','')}({','.join(hint_bits)})".strip('()')

                # Conservative boost: only when similarity is reasonably high and the case is a real upset.
                if float(best.get('score') or 0.0) >= 0.55 and best.get('upset_level') not in ('微弱爆冷', ''):
                    upset_index += min(8.0, float(best.get('score') or 0.0) * 8.0)
                    factors.append(f"[案例库] 相似案例:{knowledge['hint']} s={best.get('score')}")
        except Exception:
            # Keep prediction robust; knowledge is best-effort.
            knowledge = {'available': False, 'top_cases': [], 'hint': ''}
        
        # 确定爆冷等级
        upset_index = min(100, upset_index)
        
        if upset_index >= 70:
            upset_level = '高'
            warning_level = '🔴'
        elif upset_index >= 40:
            upset_level = '中'
            warning_level = '🟡'
        else:
            upset_level = '低'
            warning_level = '🟢'
        
        result = {
            'index': upset_index,
            'level': upset_level,
            'warning_level': warning_level,
            'similar_cases_count': len(similar_cases),
            'factors': factors,
            'historical_odds_reference': historical_odds_reference,
            'handicap_strength_mismatch': mismatch_analysis,
            'case_knowledge': knowledge,
        }
        
        self.cache.set('assess_upset_potential', cache_params, result)
        return result

class EnhancedPredictor:
    """增强版预测器 - 整合所有功能"""
    
    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.team_manager = TeamDataManager(base_dir)
        self.odds_reference = HistoricalOddsReference(base_dir)
        self.upset_analyzer = UpsetAnalyzer(base_dir)
        self.weight_adjuster = DynamicWeightAdjuster()
        self.cache = PredictionCache()
        self.result_manager = ResultManager()
        
        # 初始化多模型融合
        self.model_fusion = MultiModelFusion()
        self.poisson_model = PoissonModel()

    def _apply_dynamic_weights(self, league_code: str) -> Dict[str, Any]:
        """把动态权重应用到融合器（若可用），并返回诊断信息。"""
        try:
            diag = self.weight_adjuster.get_adjustment_diagnostics(league_code)
            weights = diag.get('final_weights')
            if weights and hasattr(self.model_fusion, 'set_model_weights'):
                self.model_fusion.set_model_weights(weights)
            return diag
        except Exception as e:
            logger.warning(f"动态调权应用失败: {e}")
        return {'league_code': league_code, 'error': str(e)}

    def compare_with_historical_odds(self, league_code: str, current_odds: Dict, top_k: int = 5) -> Dict:
        """对比当前赔率与历史相似盘路。"""
        return self.odds_reference.find_similar_matches(
            league_code=league_code,
            current_odds=current_odds,
            top_k=top_k
        )

    def _calibrate_lambdas_from_market(
        self,
        league_code: str,
        base_home_lambda: float,
        base_away_lambda: float,
        european_odds: Optional[Dict[str, Any]],
    ) -> tuple[float, float, Dict[str, Any]]:
        """Use 1X2 (欧赔终盘) implied probabilities to calibrate expected goals (lambdas).

        Why:
        - Our base lambdas come mostly from squad market-value proxies and are noisy.
        - Score + O/U hit rate is very sensitive to lambdas.
        - 1X2 market already encodes updated team news & latent strength; calibrating toward it
          tends to improve stability.
        """
        diag: Dict[str, Any] = {"applied": False}
        if not isinstance(european_odds, dict):
            return base_home_lambda, base_away_lambda, diag
        final = european_odds.get("final")
        if not isinstance(final, dict):
            return base_home_lambda, base_away_lambda, diag

        oh = self._to_float(final.get("home"))
        od = self._to_float(final.get("draw"))
        oa = self._to_float(final.get("away"))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            return base_home_lambda, base_away_lambda, diag

        # implied probs (remove margin via normalization)
        ph = 1.0 / oh
        pd = 1.0 / od
        pa = 1.0 / oa
        s = ph + pd + pa
        ph, pd, pa = ph / s, pd / s, pa / s

        rho_map = {
            "premier_league": -0.08,
            "la_liga": -0.10,
            "serie_a": -0.12,
            "bundesliga": -0.06,
            "ligue_1": -0.10,
        }
        dc = DixonColesModel(rho=rho_map.get(league_code, -0.10))

        league_avg = float(LEAGUE_CONFIG.get(league_code, {}).get("avg_goals") or 2.6)
        base_total = max(0.8, float(base_home_lambda) + float(base_away_lambda))

        best = None
        best_cost = 1e9

        # Grid-search small region: keep cost low (runs per match)
        total_min = max(1.2, league_avg - 0.8)
        total_max = min(3.8, league_avg + 0.8)
        # Favor totals near base_total/league_avg (soft constraint)
        for ti in range(int(total_min * 20), int(total_max * 20) + 1):  # step 0.05
            total = ti / 20.0
            for si in range(20, 81, 2):  # share 0.20..0.80 step 0.02
                share = si / 100.0
                hl = max(0.15, total * share)
                al = max(0.15, total * (1 - share))

                probs = dc.predict_with_dixon_coles(hl, al)
                cost_prob = (probs["home_win"] - ph) ** 2 + (probs["draw"] - pd) ** 2 + (probs["away_win"] - pa) ** 2

                # soft penalty: don't drift too far from base lambdas
                cost_base = 0.08 * ((hl - base_home_lambda) ** 2 + (al - base_away_lambda) ** 2)
                # soft penalty: keep totals close to league/base total
                cost_total = 0.04 * ((total - league_avg) ** 2 + (total - base_total) ** 2)
                cost = cost_prob + cost_base + cost_total

                if cost < best_cost:
                    best_cost = cost
                    best = (hl, al, probs)

        if not best:
            return base_home_lambda, base_away_lambda, diag

        hl, al, probs = best
        diag = {
            "applied": True,
            "source": "euro_final_1x2",
            "odds_final": {"home": oh, "draw": od, "away": oa},
            "implied_probs": {"home": round(ph, 4), "draw": round(pd, 4), "away": round(pa, 4)},
            "model_probs": {"home": round(float(probs["home_win"]), 4), "draw": round(float(probs["draw"]), 4), "away": round(float(probs["away_win"]), 4)},
            "lambda_base": {"home": round(float(base_home_lambda), 3), "away": round(float(base_away_lambda), 3), "total": round(float(base_total), 3)},
            "lambda_calibrated": {"home": round(float(hl), 3), "away": round(float(al), 3), "total": round(float(hl + al), 3)},
            "cost": round(float(best_cost), 6),
        }
        return float(hl), float(al), diag

    def _apply_live_outcome_adjustment(
        self,
        league_code: str,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, float], Dict[str, Any]]:
        """Apply live, rule-based corrections for draw traps / strong-handicap-not-cover patterns.

        This is intentionally a *small* adjustment layer (post-fusion) to:
        - raise draw probability when market+handicap signals suggest a cold draw
        - reduce favorite win probability when deep handicap shows retreat / water drift
        """
        diag: Dict[str, Any] = {"applied": False, "signals": [], "delta": {}}
        if not isinstance(final_prob, dict):
            return final_prob, diag
        if not isinstance(current_odds, dict):
            return final_prob, diag

        def _pick(d: Dict[str, Any], *keys: str) -> Any:
            cur: Any = d
            for k in keys:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(k)
            return cur

        def _parse_euro_final(eu: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
            if not isinstance(eu, dict):
                return None, None, None
            # normalized schema: {'final': {'home':..,'draw':..,'away':..}}
            fin = eu.get("final")
            if isinstance(fin, dict):
                return self._to_float(fin.get("home")), self._to_float(fin.get("draw")), self._to_float(fin.get("away"))
            # fallback: 500-like snapshots may contain Chinese keys
            fin = eu.get("最新指数")
            if isinstance(fin, dict):
                return self._to_float(fin.get("主")), self._to_float(fin.get("平")), self._to_float(fin.get("客"))
            return None, None, None

        def _parse_asian(asian: Any) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
            # return (hcp_init, hcp_final, hw_init, aw_init, hw_final, aw_final)
            if not isinstance(asian, dict):
                return None, None, None, None, None, None
            ini = asian.get("initial")
            fin = asian.get("final")
            if isinstance(ini, dict) and isinstance(fin, dict):
                hcp_i = self._to_float(ini.get("handicap") if "handicap" in ini else ini.get("盘口值"))
                hcp_f = self._to_float(fin.get("handicap") if "handicap" in fin else fin.get("盘口值"))
                hw_i = self._to_float(ini.get("home_water") if "home_water" in ini else ini.get("主水"))
                aw_i = self._to_float(ini.get("away_water") if "away_water" in ini else ini.get("客水"))
                hw_f = self._to_float(fin.get("home_water") if "home_water" in fin else fin.get("主水"))
                aw_f = self._to_float(fin.get("away_water") if "away_water" in fin else fin.get("客水"))
                return hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f
            return None, None, None, None, None, None

        p_h = float(final_prob.get("home_win") or 0.0)
        p_d = float(final_prob.get("draw") or 0.0)
        p_a = float(final_prob.get("away_win") or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

        euro = current_odds.get("欧赔")
        asian = current_odds.get("亚值")
        kelly = current_odds.get("凯利")

        oh, od, oa = _parse_euro_final(euro)
        hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f = _parse_asian(asian)
        kd = None
        if isinstance(kelly, dict):
            kd = self._to_float(_pick(kelly, "final", "draw"))

        # Determine favorite (exclude draw)
        fav_side = None
        fav_odds = None
        if isinstance(oh, float) and isinstance(oa, float):
            if oh <= oa:
                fav_side, fav_odds = "home", oh
            else:
                fav_side, fav_odds = "away", oa
        _ = od  # keep for future extensions (draw odds based filters)

        _ = league_code  # reserved for future league-specific thresholds
        deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 0.75
        very_deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 1.0

        # Handicap giver side: negative -> home gives, positive -> away gives
        giver = None
        if isinstance(hcp_f, float):
            if hcp_f < -0.06:
                giver = "home"
            elif hcp_f > 0.06:
                giver = "away"

        retreat = False
        if isinstance(hcp_i, float) and isinstance(hcp_f, float) and abs(hcp_f) + 0.12 < abs(hcp_i):
            retreat = True

        water_drift = False
        if fav_side == "home" and isinstance(hw_i, float) and isinstance(hw_f, float) and (hw_f - hw_i) >= 0.04:
            water_drift = True
        if fav_side == "away" and isinstance(aw_i, float) and isinstance(aw_f, float) and (aw_f - aw_i) >= 0.04:
            water_drift = True

        # Cold draw signal score
        draw_boost = 0.0
        if isinstance(fav_odds, float) and fav_odds <= 1.60:
            draw_boost += 0.04
            diag["signals"].append("低赔强侧(<=1.60)")
        if deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag["signals"].append("深让>=0.75")
        if very_deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag["signals"].append("强让>=1.0")
        if retreat and giver == fav_side and very_deep_handicap:
            draw_boost += 0.03
            diag["signals"].append("强让退盘")
        if water_drift and giver == fav_side and very_deep_handicap:
            draw_boost += 0.02
            diag["signals"].append("强侧水位走高")
        if isinstance(kd, float) and kd <= 0.95:
            draw_boost += 0.01
            diag["signals"].append("平局凯利偏低")

        # Historical similar odds: if draw-rate is high, nudge draw a bit.
        try:
            if isinstance(historical_odds_reference, dict):
                summary = historical_odds_reference.get("summary") or {}
                rates = summary.get("result_rates") or {}
                dr = rates.get("平局")
                if isinstance(dr, (int, float)) and dr >= 0.33:
                    draw_boost += 0.02
                    diag["signals"].append("相似盘路平局率偏高")
        except Exception:
            pass

        draw_boost = min(0.10, max(0.0, draw_boost))
        if draw_boost <= 0:
            return {"home_win": p_h, "draw": p_d, "away_win": p_a}, diag

        # Apply by taking from favorite win, prefer not to zero it out.
        if fav_side == "home":
            take = min(draw_boost, max(0.0, p_h - 0.05))
            p_h -= take
            p_d += take
        elif fav_side == "away":
            take = min(draw_boost, max(0.0, p_a - 0.05))
            p_a -= take
            p_d += take
        else:
            # no favorite info, take from max win side
            if p_h >= p_a:
                take = min(draw_boost, max(0.0, p_h - 0.05))
                p_h -= take
                p_d += take
            else:
                take = min(draw_boost, max(0.0, p_a - 0.05))
                p_a -= take
                p_d += take

        # Normalize again
        s2 = p_h + p_d + p_a
        if s2 > 0:
            p_h, p_d, p_a = p_h / s2, p_d / s2, p_a / s2

        diag["applied"] = True
        diag["delta"] = {
            "home_win": round(p_h - float(final_prob.get("home_win") or 0.0), 6),
            "draw": round(p_d - float(final_prob.get("draw") or 0.0), 6),
            "away_win": round(p_a - float(final_prob.get("away_win") or 0.0), 6),
        }
        diag["fav"] = {"side": fav_side, "odds": fav_odds}
        diag["asian"] = {"handicap_initial": hcp_i, "handicap_final": hcp_f, "giver": giver, "retreat": retreat, "water_drift": water_drift}
        return {"home_win": p_h, "draw": p_d, "away_win": p_a}, diag

    @staticmethod
    def _extract_current_odds_snapshot(match_record: Dict) -> Dict:
        """从赔率落盘记录中提取预测所需的赔率快照字段。"""
        return {
            'match_id': match_record.get('match_id'),
            '胜平负赔率': match_record.get('胜平负赔率', {}),
            '欧赔': match_record.get('欧赔', {}),
            '亚值': match_record.get('亚值', {}),
            '大小球': match_record.get('大小球', {}) or {},
            '凯利': match_record.get('凯利', {}),
            '离散率': match_record.get('离散率', {}),
        }

    @staticmethod
    def _extract_current_odds_live_snapshot(snapshot: Dict) -> Dict:
        """从 okooo_save_snapshot.py 生成的实时 JSON 提取预测所需字段。"""
        europe = snapshot.get('欧赔', {}) or {}
        asian = snapshot.get('亚值', {}) or {}
        kelly = snapshot.get('凯利', {}) or {}
        totals = snapshot.get('大小球', {}) or {}
        return {
            'match_id': snapshot.get('match_id'),
            '胜平负赔率': {
                'initial': europe.get('initial', {}),
                'final': europe.get('final', {}),
            },
            '欧赔': {
                'initial': europe.get('initial', {}),
                'final': europe.get('final', {}),
            },
            '亚值': {
                'initial': asian.get('initial', {}),
                'final': asian.get('final', {}),
            },
            '大小球': {
                'initial': totals.get('initial', {}) if isinstance(totals, dict) else {},
                'final': totals.get('final', {}) if isinstance(totals, dict) else {},
            },
            '凯利': {
                'initial': kelly.get('initial', {}),
                'final': kelly.get('final', {}),
            },
            '离散率': snapshot.get('离散率', {}) or {},
        }

    def _resolve_over_under_line(self, current_odds: Optional[Dict[str, Any]], analysis_context: Dict[str, Any]) -> tuple[float, str]:
        """Pick a real O/U line if available; otherwise fallback to 2.5.

        Priority:
        1) analysis_context['ou_line'] (external source override)
        2) current_odds['大小球']['final']['line'] / ['盘口'] (from snapshots if enriched)
        3) default 2.5
        """
        line = None
        src = "default_2.5"

        # 1) explicit override
        try:
            if isinstance(analysis_context, dict) and 'ou_line' in analysis_context:
                v = self._to_float(analysis_context.get('ou_line'))
                if isinstance(v, float) and 0.5 <= v <= 6.5:
                    return float(v), "analysis_context"
        except Exception:
            pass

        # 2) snapshot-provided totals
        try:
            if isinstance(current_odds, dict):
                ou = current_odds.get('大小球')
                if isinstance(ou, dict):
                    fin = ou.get('final')
                    if isinstance(fin, dict):
                        v = fin.get('line')
                        if v is None:
                            v = fin.get('盘口')
                        vv = self._to_float(v)
                        if isinstance(vv, float) and 0.5 <= vv <= 6.5:
                            return float(vv), "snapshot_final"
                    ini = ou.get('initial')
                    if isinstance(ini, dict):
                        v = ini.get('line')
                        if v is None:
                            v = ini.get('盘口')
                        vv = self._to_float(v)
                        if isinstance(vv, float) and 0.5 <= vv <= 6.5:
                            return float(vv), "snapshot_initial"
        except Exception:
            pass

        # 3) default
        return 2.5, src

    def _auto_fetch_okooo_totals_if_needed(
        self,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        okooo_driver: str = "browser-use",
        okooo_headed: bool = False,
        match_time: str = "",
        match_id: str = "",
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Best-effort: fetch latest O/U line + water from okooo via remen flow.

        This runs `okooo_save_snapshot.py` (browser-based) and reads the emitted JSON.
        It is guarded by env var `OKOOO_AUTO_TOTALS` (default on).
        """
        diag: Dict[str, Any] = {"attempted": False, "ok": False}
        enabled = os.environ.get("OKOOO_AUTO_TOTALS", "1").strip() not in ("0", "false", "False")
        if not enabled:
            diag["skipped"] = "OKOOO_AUTO_TOTALS=0"
            return current_odds, diag

        # If already has totals line, no need.
        try:
            line, src = self._resolve_over_under_line(current_odds=current_odds, analysis_context=analysis_context)
            if src in ("snapshot_final", "snapshot_initial", "analysis_context"):
                diag["skipped"] = f"ou_line already resolved from {src}"
                return current_odds, diag
        except Exception:
            pass

        league_name = (LEAGUE_CONFIG.get(league_code, {}) or {}).get("name") or ""
        if not league_name:
            diag["skipped"] = "unknown league"
            return current_odds, diag

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "okooo_save_snapshot.py")
        out_dir = _external_snapshot_root(self.base_dir)  # europe_leagues/.okooo-scraper/snapshots
        # Prefer the caller-provided driver but fallback to the other one (best-effort).
        drivers = [okooo_driver]
        if okooo_driver == "local-chrome":
            drivers.append("browser-use")
        elif okooo_driver == "browser-use":
            drivers.append("local-chrome")

        diag["attempted"] = True
        try:
            last_err = None
            for drv in drivers:
                cmd = [
                    sys.executable,
                    script,
                    "--driver",
                    str(drv),
                    "--league",
                    str(league_name),
                    "--team1",
                    str(home_team),
                    "--team2",
                    str(away_team),
                    "--date",
                    str(match_date),
                    "--out-dir",
                    str(out_dir),
                    "--overwrite",
                ]
                if match_time:
                    cmd.extend(["--time", str(match_time)])
                if match_id:
                    cmd.extend(["--match-id", str(match_id)])
                if bool(okooo_headed) and drv == "browser-use":
                    cmd.append("--headed")

                diag["driver_tried"] = drv
                diag["cmd"] = " ".join([str(x) for x in cmd])
                p = subprocess.run(cmd, cwd=os.path.dirname(script), capture_output=True, text=True, timeout=240)
                diag["returncode"] = p.returncode
                if p.stdout:
                    diag["stdout_tail"] = p.stdout.strip().splitlines()[-1][-200:]
                if p.stderr:
                    diag["stderr_tail"] = p.stderr.strip().splitlines()[-1][-200:]
                if p.returncode != 0:
                    last_err = f"rc={p.returncode}"
                    continue

                out_path = (p.stdout or "").strip().splitlines()[-1].strip()
                if not out_path or not os.path.exists(out_path):
                    last_err = "snapshot path missing"
                    continue

                data = json.loads(open(out_path, "r", encoding="utf-8").read())
                totals = data.get("大小球") if isinstance(data, dict) else None
                if not isinstance(totals, dict) or not totals.get("found"):
                    last_err = "totals not found in snapshot"
                    continue

                merged = dict(current_odds or {})
                merged["大小球"] = {"initial": totals.get("initial") or {}, "final": totals.get("final") or {}}
                diag["ok"] = True
                diag["source_snapshot"] = out_path
                return merged, diag

            diag["error"] = last_err or "unknown"
            return current_odds, diag
        except Exception as e:
            diag["error"] = str(e)
            return current_odds, diag

    def _get_matches_from_odds_history(self, league_code: str, match_date: str) -> List[Dict]:
        """从联赛目录的赔率落盘文件中获取某日真实赛程+赔率快照。"""
        matches = []
        for record in self.odds_reference.records_by_league.get(league_code, []):
            if record.get('match_date') != match_date:
                continue
            matches.append({
                'home_team': record.get('home_team'),
                'away_team': record.get('away_team'),
                'current_odds': self._extract_current_odds_snapshot(record),
            })
        return [m for m in matches if m.get('home_team') and m.get('away_team')]

    def _get_matches_from_odds_snapshots(self, league_code: str, match_date: str) -> List[Dict]:
        """从联赛目录 analysis/odds_snapshots 获取某日真实赛程+赔率快照。

        支持：
        - `*_odds_snapshot.json`（结构化快照）
        - `*_odds_snapshot.csv`（批量快照，项目内目前更常见）
        """
        snapshot_dir = os.path.join(self.base_dir, league_code, 'analysis', 'odds_snapshots')
        if not os.path.isdir(snapshot_dir):
            return []

        matches = []
        for file_path in sorted(glob(os.path.join(snapshot_dir, '*_odds_snapshot.json'))):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
            except Exception:
                continue
            for record in payload.get('matches', []):
                if record.get('match_date') != match_date:
                    continue
                matches.append({
                    'home_team': record.get('home_team'),
                    'away_team': record.get('away_team'),
                    'current_odds': self._extract_current_odds_snapshot(record),
                })
        matches = [m for m in matches if m.get('home_team') and m.get('away_team')]
        if matches:
            return matches

        # CSV snapshots (preferred in this repo)
        csv_matches = []
        for file_path in sorted(glob(os.path.join(snapshot_dir, '*_odds_snapshot.csv'))):
            try:
                import csv as _csv
                with open(file_path, 'r', encoding='utf-8') as f:
                    reader = _csv.DictReader(f)
                    for row in reader:
                        if row.get('match_date') != match_date:
                            continue
                        home = (row.get('home_team') or '').strip()
                        away = (row.get('away_team') or '').strip()
                        if not home or not away:
                            continue
                        csv_matches.append({
                            'home_team': home,
                            'away_team': away,
                            'current_odds': self._extract_current_odds_from_csv_row(row),
                        })
            except Exception:
                continue
        csv_matches = [m for m in csv_matches if m.get('home_team') and m.get('away_team')]
        if csv_matches:
            logger.info(f"使用 CSV 赔率快照进行预测: {league_code} {match_date}, matches={len(csv_matches)}")
            return csv_matches

        # Fallback: external live snapshots generated by okooo_save_snapshot.py
        live_matches = []
        for external_dir in _external_snapshot_dirs(self.base_dir, league_code):
            if not os.path.isdir(external_dir):
                continue
            for file_path in sorted(glob(os.path.join(external_dir, '*.json'))):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                except Exception:
                    continue
                if payload.get('match_date') != match_date:
                    continue
                home = payload.get('home_team')
                away = payload.get('away_team')
                if not home or not away:
                    continue
                live_matches.append({
                    'home_team': home,
                    'away_team': away,
                    'current_odds': self._extract_current_odds_live_snapshot(payload),
                })
        if live_matches:
            logger.info(f"使用外部实时快照进行预测: {league_code} {match_date}, matches={len(live_matches)}")
            return live_matches
        return []

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            s = str(value).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    def _extract_current_odds_from_csv_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """把 odds_snapshot.csv 的扁平字段转换成 current_odds 所需的嵌套结构。"""
        f = self._to_float
        europe_initial = {
            'home': f(row.get('欧赔_初始_主')),
            'draw': f(row.get('欧赔_初始_平')),
            'away': f(row.get('欧赔_初始_客')),
        }
        europe_final = {
            'home': f(row.get('欧赔_即时_主')),
            'draw': f(row.get('欧赔_即时_平')),
            'away': f(row.get('欧赔_即时_客')),
        }
        spf_initial = {
            'home': f(row.get('胜平负_初始_主')),
            'draw': f(row.get('胜平负_初始_平')),
            'away': f(row.get('胜平负_初始_客')),
        }
        spf_final = {
            'home': f(row.get('胜平负_即时_主')),
            'draw': f(row.get('胜平负_即时_平')),
            'away': f(row.get('胜平负_即时_客')),
        }
        asian_initial = {
            'home_water': f(row.get('亚值_初始_主水')),
            'handicap': f(row.get('亚值_初始_盘口值')),
            'away_water': f(row.get('亚值_初始_客水')),
        }
        asian_final = {
            'home_water': f(row.get('亚值_即时_主水')),
            'handicap': f(row.get('亚值_即时_盘口值')),
            'away_water': f(row.get('亚值_即时_客水')),
        }
        kelly_initial = {
            'home': f(row.get('凯利_初始_主')),
            'draw': f(row.get('凯利_初始_平')),
            'away': f(row.get('凯利_初始_客')),
        }
        kelly_final = {
            'home': f(row.get('凯利_即时_主')),
            'draw': f(row.get('凯利_即时_平')),
            'away': f(row.get('凯利_即时_客')),
        }
        disc_initial = {
            'home': f(row.get('离散率_初始_主')),
            'draw': f(row.get('离散率_初始_平')),
            'away': f(row.get('离散率_初始_客')),
        }
        disc_final = {
            'home': f(row.get('离散率_即时_主')),
            'draw': f(row.get('离散率_即时_平')),
            'away': f(row.get('离散率_即时_客')),
        }

        # Optional: over/under market (if the snapshot csv has been enriched)
        # Expected column names (any subset is ok):
        # - 大小球_初始_盘口 / 大小球_即时_盘口
        # - 大小球_初始_大 / 大小球_初始_小 / 大小球_即时_大 / 大小球_即时_小
        ou_initial = {
            'line': f(row.get('大小球_初始_盘口')),
            'over': f(row.get('大小球_初始_大')),
            'under': f(row.get('大小球_初始_小')),
        }
        ou_final = {
            'line': f(row.get('大小球_即时_盘口')),
            'over': f(row.get('大小球_即时_大')),
            'under': f(row.get('大小球_即时_小')),
        }
        if all(v is None for v in ou_initial.values()):
            ou_initial = {}
        if all(v is None for v in ou_final.values()):
            ou_final = {}
        ou_block = {'initial': ou_initial, 'final': ou_final} if (ou_initial or ou_final) else {}
        return {
            'match_id': row.get('match_id') or row.get('page_id'),
            '胜平负赔率': {'initial': spf_initial, 'final': spf_final},
            '欧赔': {'initial': europe_initial, 'final': europe_final},
            '亚值': {'initial': asian_initial, 'final': asian_final},
            '凯利': {'initial': kelly_initial, 'final': kelly_final},
            '离散率': {'initial': disc_initial, 'final': disc_final},
            '大小球': ou_block,
            '_source': 'odds_snapshot.csv',
        }
    
    def predict_match(
        self,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str = None,
        current_odds: Optional[Dict] = None,
        match_id: str = "",
        force_refresh_odds: bool = True,
        okooo_driver: str = "browser-use",
        okooo_headed: bool = False,
        match_time: str = "",
        league_hint: Optional[str] = None,
        analysis_context: Optional[Dict] = None,
    ) -> Dict:
        """预测单场比赛（增强版）。

        默认尝试实时刷新澳客赔率快照并注入 current_odds，以便赔率相似盘路与爆冷评估使用最新数据。
        其他多维度信息（战术/战意/首发/临场变化等）可由调用方通过 analysis_context 传入。
        """
        if not match_date:
            match_date = datetime.now().strftime("%Y-%m-%d")

        analysis_context = analysis_context or {}
        realtime = {
            "okooo": {
                "attempted": False,
                "refreshed": False,
                "snapshot_path": "",
                "match_id": str(match_id or ""),
                "driver": okooo_driver,
                "headed": bool(okooo_headed),
                "errors": [],
            },
            "context_applied": {},
        }

        # 0. 优先刷新实时赔率快照（默认开启，可用 env OKOOO_REFRESH_LIVE=0 关闭）
        if (
            force_refresh_odds
            and os.environ.get("OKOOO_REFRESH_LIVE", "1") != "0"
            and home_team
            and away_team
        ):
            realtime["okooo"]["attempted"] = True
            # Stable path: rely on okooo_save_snapshot.py to find MatchID from schedule rows.
            # We then fallback between drivers (local-chrome <-> browser-use) if needed.
            mid = str(match_id or "")
            drivers = [okooo_driver]
            if okooo_driver == "local-chrome":
                drivers.append("browser-use")
            elif okooo_driver == "browser-use":
                drivers.append("local-chrome")

            for drv in drivers:
                try:
                    refreshed = refresh_okooo_snapshot(
                        self.base_dir,
                        league_code,
                        home_team,
                        away_team,
                        match_date,
                        driver=drv,
                        match_id=mid,
                        headed=bool(okooo_headed),
                        match_time=match_time or "",
                    )
                    if refreshed:
                        path, payload = refreshed
                        realtime["okooo"]["snapshot_path"] = path or ""
                        realtime["okooo"]["match_id"] = str(payload.get("match_id") or mid or "")
                        realtime["okooo"]["refreshed"] = True
                        realtime["okooo"]["driver"] = drv
                        current_odds = extract_okooo_current_odds(payload)
                        break
                except Exception as e:
                    realtime["okooo"]["errors"].append({"driver": drv, "error": str(e)})
                    continue

        # 0.5 若缺少大小球盘口线/水位，补抓一次（best-effort，可用 env OKOOO_AUTO_TOTALS=0 关闭）
        try:
            current_odds, ou_fetch_diag = self._auto_fetch_okooo_totals_if_needed(
                league_code=league_code,
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                current_odds=current_odds,
                analysis_context=analysis_context,
                okooo_driver=okooo_driver,
                okooo_headed=bool(okooo_headed),
                match_time=match_time or "",
                match_id=realtime["okooo"]["match_id"],
            )
            realtime["context_applied"]["okooo_totals_fetch"] = ou_fetch_diag
        except Exception as e:
            realtime["context_applied"]["okooo_totals_fetch"] = {"attempted": True, "ok": False, "error": str(e)}

        # 0.6 可选：自动补齐球队战术/控球/上一场首发/球员近期评分（best-effort，默认关闭）
        _auto_enrich_team_context_if_enabled(
            base_dir=self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            analysis_context=analysis_context,
            realtime_context_applied=realtime["context_applied"],
        )

        cache_params = {
            'schema_v': 3,
            'home': home_team, 'away': away_team,
            'league': league_code, 'date': match_date,
            'current_odds': current_odds,
            'match_id': realtime["okooo"]["match_id"],
            'force_refresh_odds': force_refresh_odds,
            'okooo_driver': okooo_driver,
            'okooo_headed': bool(okooo_headed),
            'analysis_context': analysis_context,
        }
        cached = self.cache.get('predict_match', cache_params)
        if cached:
            logger.info(f"使用缓存预测: {home_team} vs {away_team}")
            return cached
        
        
        logger.info(f"开始预测: {home_team} vs {away_team} ({league_code})")

        # 动态调权（基于最近准确率统计）；失败则使用默认权重
        applied_weights = self._apply_dynamic_weights(league_code)
        
        # 1. 获取球队实力分析
        home_strength = self.team_manager.analyze_team_strength(league_code, home_team)
        away_strength = self.team_manager.analyze_team_strength(league_code, away_team)
        
        # 2. 计算实力差距
        strength_diff = home_strength['strength'] - away_strength['strength']
        
        # Prepare market odds handles early (also used by lambda calibration).
        asian_handicap = None
        european_odds = None
        if isinstance(current_odds, dict):
            asian_handicap = current_odds.get('亚值')
            european_odds = current_odds.get('欧赔')

        # 3. 使用多模型融合预测
        league_avg_goals = LEAGUE_CONFIG[league_code]['avg_goals']
        
        # 准备模型输入参数（支持调用方覆盖）
        home_form = int(analysis_context.get("home_form", 3))
        away_form = int(analysis_context.get("away_form", 3))
        home_motivation = float(analysis_context.get("home_motivation", 75))
        away_motivation = float(analysis_context.get("away_motivation", 75))
        realtime["context_applied"].update(
            {
                "home_form": home_form,
                "away_form": away_form,
                "home_motivation": home_motivation,
                "away_motivation": away_motivation,
            }
        )
        
        # 计算预期进球
        base_home_lambda = home_strength['attack'] * away_strength['defense'] * league_avg_goals * 1.12
        base_away_lambda = away_strength['attack'] * home_strength['defense'] * league_avg_goals

        home_lambda = base_home_lambda
        away_lambda = base_away_lambda

        # 如果有欧赔终盘，尝试用市场隐含概率校准 λ（提升比分/大小球稳定性）
        try:
            home_lambda, away_lambda, cal_diag = self._calibrate_lambdas_from_market(
                league_code=league_code,
                base_home_lambda=base_home_lambda,
                base_away_lambda=base_away_lambda,
                european_odds=european_odds,
            )
            realtime["context_applied"]["lambda_calibration"] = cal_diag
        except Exception as e:
            realtime["context_applied"]["lambda_calibration"] = {"applied": False, "error": str(e)}
        
        # 简化的xG计算
        home_xg = home_lambda * 0.8
        away_xg = away_lambda * 0.8
        
        # 多模型预测
        h2h_home_wins = int(analysis_context.get("h2h_home_wins", 0))
        h2h_away_wins = int(analysis_context.get("h2h_away_wins", 0))
        h2h_draws = int(analysis_context.get("h2h_draws", 0))
        realtime["context_applied"].update(
            {"h2h_home_wins": h2h_home_wins, "h2h_away_wins": h2h_away_wins, "h2h_draws": h2h_draws}
        )

        fusion_result = self.model_fusion.predict(
            home_team=home_team,
            away_team=away_team,
            home_strength=home_strength['strength'],
            away_strength=away_strength['strength'],
            home_form=home_form,
            away_form=away_form,
            home_injuries=home_strength['injured_count'],
            away_injuries=away_strength['injured_count'],
            h2h_home_wins=h2h_home_wins,
            h2h_away_wins=h2h_away_wins,
            h2h_draws=h2h_draws,
            home_motivation=home_motivation,
            away_motivation=away_motivation,
            home_xg=home_xg,
            away_xg=away_xg,
            home_attack=home_strength['attack'],
            home_defense=home_strength['defense'],
            away_attack=away_strength['attack'],
            away_defense=away_strength['defense']
        )
        
        # 5. 确定最终预测结果（可叠加临场规则修正）
        final_prob = fusion_result['final']
        probs = [
            ('主胜', final_prob['home_win']),
            ('平局', final_prob['draw']),
            ('客胜', final_prob['away_win'])
        ]
        probs.sort(key=lambda x: x[1], reverse=True)

        historical_odds_reference = None
        if isinstance(current_odds, dict) and current_odds:
            exclude_match_id = current_odds.get('match_id')
            historical_odds_reference = self.odds_reference.find_similar_matches(
                league_code=league_code,
                current_odds=current_odds,
                top_k=5,
                exclude_match_id=exclude_match_id
            )
        else:
            historical_odds_reference = {
                'available': False,
                'league_history_count': self.odds_reference.get_league_record_count(league_code),
                'matched_feature_count': 0,
                'similar_matches': [],
                'summary': {
                    'sample_size': 0,
                    'result_counts': {'主胜': 0, '平局': 0, '客胜': 0},
                    'result_rates': {'主胜': 0.0, '平局': 0.0, '客胜': 0.0},
                    'cold_result_count': 0,
                    'cold_result_rate': 0.0,
                },
                'insights': ['当前未传入赔率快照，历史赔率参考已就绪但未参与匹配']
            }

        # Live correction for draw-traps / strong-handicap-not-cover patterns
        adjusted_prob, live_adj_diag = self._apply_live_outcome_adjustment(
            league_code=league_code,
            final_prob=final_prob,
            current_odds=current_odds,
            historical_odds_reference=historical_odds_reference,
        )
        if isinstance(live_adj_diag, dict) and live_adj_diag.get("applied"):
            final_prob = adjusted_prob
            probs = [
                ('主胜', final_prob['home_win']),
                ('平局', final_prob['draw']),
                ('客胜', final_prob['away_win'])
            ]
            probs.sort(key=lambda x: x[1], reverse=True)
        realtime["context_applied"]["live_outcome_adjustment"] = live_adj_diag

        main_prediction = probs[0][0]
        confidence = probs[0][1]

        # 6. 爆冷分析（把模型输出也作为输入，便于案例库做“同向反打”学习）
        upset_potential = self.upset_analyzer.assess_upset_potential(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            strength_diff=strength_diff,
            home_strength=home_strength,
            away_strength=away_strength,
            predicted_outcome=main_prediction,
            confidence=confidence,
            historical_odds_reference=historical_odds_reference,
            asian_handicap=asian_handicap,
            european_odds=european_odds,
        )
        
        # 7. 预测比分（Dixon-Coles 修正：更贴近低比分/平局分布）
        rho_map = {
            'premier_league': -0.08,
            'la_liga': -0.10,
            'serie_a': -0.12,
            'bundesliga': -0.06,
            'ligue_1': -0.10,
        }
        dc_model = DixonColesModel(rho=rho_map.get(league_code, -0.10))
        score_result = dc_model.predict_with_dixon_coles(home_lambda, away_lambda)
        top_scores = sorted(score_result['score_probs'].items(), key=lambda x: x[1], reverse=True)[:3]
        
        # 8. 大小球预测
        # If current_odds still lacks totals, attempt a last-moment fetch (best-effort).
        if os.environ.get("OKOOO_AUTO_TOTALS", "1") != "0":
            try:
                current_odds, ou_fetch_diag2 = self._auto_fetch_okooo_totals_if_needed(
                    league_code=league_code,
                    match_date=match_date,
                    home_team=home_team,
                    away_team=away_team,
                    current_odds=current_odds,
                    analysis_context=analysis_context,
                    okooo_driver=okooo_driver,
                    okooo_headed=bool(okooo_headed),
                    match_time=match_time or "",
                    match_id=realtime["okooo"]["match_id"],
                )
                # Only overwrite if we actually attempted here (avoid masking earlier diag).
                realtime["context_applied"]["okooo_totals_fetch_last_moment"] = ou_fetch_diag2
            except Exception as e:
                realtime["context_applied"]["okooo_totals_fetch_last_moment"] = {"attempted": True, "ok": False, "error": str(e)}

        ou_line, ou_line_source = self._resolve_over_under_line(current_odds=current_odds, analysis_context=analysis_context)
        over_under = self.poisson_model.predict_over_under(home_lambda, away_lambda, line=ou_line)
        if isinstance(over_under, dict):
            over_under["line_source"] = ou_line_source
            # Attach market O/U (line + water) if we have it, for traceability in final conclusion.
            try:
                market_ou = None
                if isinstance(current_odds, dict):
                    blk = current_odds.get("大小球")
                    if isinstance(blk, dict):
                        fin = blk.get("final") if isinstance(blk.get("final"), dict) else {}
                        ini = blk.get("initial") if isinstance(blk.get("initial"), dict) else {}
                        market_ou = {"final": fin or {}, "initial": ini or {}}
                if market_ou:
                    over_under["market"] = market_ou
                    realtime["context_applied"]["ou_market"] = market_ou
            except Exception:
                pass
        
        # 构建完整结果
        result = {
            'home_team': home_team,
            'away_team': away_team,
            'league_code': league_code,
            'league_name': LEAGUE_CONFIG[league_code]['name'],
            'match_date': match_date,
            'prediction': main_prediction,
            'confidence': confidence,
            'all_probabilities': dict(probs),
            'top_scores': top_scores,
            'expected_goals': {
                'home': home_lambda,
                'away': away_lambda,
                'total': home_lambda + away_lambda
            },
            'over_under': over_under,
            'strength_diff': strength_diff,
            'home_strength': home_strength,
            'away_strength': away_strength,
            'upset_potential': upset_potential,
            'historical_odds_reference': historical_odds_reference,
            'model_predictions': fusion_result['all_models'],
            'final_probabilities': final_prob,
            'applied_model_weights': applied_weights,
            'realtime': realtime,
            'analysis_context': analysis_context,
            'timestamp': datetime.now().isoformat()
        }
        
        self.cache.set('predict_match', cache_params, result)
        logger.info(f"预测完成: {home_team} vs {away_team} -> {main_prediction} ({confidence:.1%})")
        
        return result
    
    def generate_prediction_report(self, league_code: str, match_date: str, 
                                  matches: List[Dict] = None) -> Optional[str]:
        """生成预测并写回 teams_2025-26.md（不再生成独立 predictions.md 文件）"""
        if not matches:
            # 优先从联赛目录中的“即时赔率快照”读取未来赛程（确保有 current_odds 才能做相似盘路）
            matches = self._get_matches_from_odds_snapshots(league_code, match_date)
            if not matches:
                # 其次从历史赔率落盘文件读取（主要覆盖已回填的历史日期）
                matches = self._get_matches_from_odds_history(league_code, match_date)
            if not matches:
                # 如果没有提供比赛，使用模拟数据
                matches = self._get_sample_matches(league_code)
        else:
            # 如果传入 matches 但未带 current_odds，则尝试从快照目录补齐
            need_fill = any(not m.get('current_odds') for m in matches if isinstance(m, dict))
            if need_fill:
                snapshot_index = {}
                for m in self._get_matches_from_odds_snapshots(league_code, match_date):
                    snapshot_index[(m.get('home_team'), m.get('away_team'))] = m.get('current_odds')
                for m in matches:
                    if not isinstance(m, dict):
                        continue
                    if m.get('current_odds'):
                        continue
                    key = (m.get('home_team') or m.get('主队'), m.get('away_team') or m.get('客队'))
                    if key in snapshot_index:
                        m['current_odds'] = snapshot_index[key]
        
        if not matches:
            logger.warning(f"没有比赛数据: {league_code} {match_date}")
            return None
        
        # 预测所有比赛
        predictions = []
        for match in matches:
            home = match.get('home_team', match.get('主队'))
            away = match.get('away_team', match.get('客队'))
            current_odds = match.get('current_odds')

            # Always refresh latest odds before prediction unless disabled.
            # Set OKOOO_REFRESH_LIVE=0 to skip refreshing.
            if os.environ.get("OKOOO_REFRESH_LIVE", "1") != "0" and home and away:
                try:
                    mid = None
                    if isinstance(current_odds, dict):
                        mid = current_odds.get("match_id")
                    refreshed = refresh_okooo_snapshot(
                        self.base_dir,
                        league_code,
                        home,
                        away,
                        match_date,
                        driver="local-chrome",
                        match_id=str(mid) if mid else "",
                    )
                    if refreshed:
                        _path, payload = refreshed
                        current_odds = extract_okooo_current_odds(payload)
                except Exception as e:
                    logger.warning(f"刷新实时快照失败: {home} vs {away} {match_date}: {e}")

            pred = self.predict_match(
                home,
                away,
                league_code,
                match_date,
                current_odds=current_odds,
                # We already attempted a live refresh above in this loop; avoid double refreshing.
                force_refresh_odds=False,
            )
            predictions.append(pred)
        
        # Write back to league teams_2025-26.md schedule table notes.
        teams_path = os.path.join(self.base_dir, league_code, 'teams_2025-26.md')
        if os.path.exists(teams_path):
            _update_teams_md_with_enhanced_predictions(teams_path, match_date, predictions)
            logger.info(f"已更新 teams 文件: {teams_path}")
        else:
            logger.warning(f"未找到 teams 文件: {teams_path}")
        
        # 保存预测到历史数据库
        for pred in predictions:
            self.result_manager.save_prediction_from_enhanced(pred, league_code)
        
        logger.info(f"预测已保存到历史数据库")
        
        # 更新准确率统计
        self.result_manager.update_accuracy_stats()
        
        return teams_path if os.path.exists(teams_path) else None
    
    def _get_sample_matches(self, league_code: str) -> List[Dict]:
        """获取模拟比赛数据"""
        teams = LEAGUE_CONFIG[league_code]['teams']
        matches = []
        for i in range(0, min(len(teams), 10), 2):
            if i + 1 < len(teams):
                matches.append({
                    'home_team': teams[i],
                    'away_team': teams[i + 1]
                })
        return matches
    
    def _format_report(self, league_code: str, match_date: str, predictions: List[Dict]) -> str:
        """格式化预测报告"""
        league_name = LEAGUE_CONFIG[league_code]['name']
        
        report = f"# 🔮 {league_name} 联赛预测分析报告\n"
        report += f"\n**预测日期**: {match_date}\n"
        report += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report += f"**预测场次**: {len(predictions)} 场\n"
        
        # 比赛预测列表
        report += "\n" + "="*80 + "\n"
        report += "## 📊 比赛预测详情\n"
        
        for pred in predictions:
            home = pred['home_team']
            away = pred['away_team']
            
            report += f"\n### {home} vs {away}\n"
            report += f"\n**预测结果**: {pred['prediction']} (信心: {pred['confidence']:.1%})\n"
            
            # 概率分布
            report += "\n**概率分布**:\n"
            # 固定输出顺序，避免字典遍历导致的“主/平/客”顺序漂移
            for outcome in ('主胜', '平局', '客胜'):
                prob = pred['all_probabilities'].get(outcome, 0.0)
                bar = "█" * int(prob * 50)
                report += f"  {outcome}: {prob:6.1%} {bar}\n"
            
            # 比分预测
            report += "\n**最可能比分**:\n"
            for score, prob in pred['top_scores']:
                report += f"  {score}: {prob:.1%}\n"
            
            # 大小球
            ou = pred['over_under']
            ou_line = ou.get('line')
            line_label = f"{ou_line:g}" if isinstance(ou_line, (int, float)) else "?"
            report += f"\n**大小球分析** ({line_label}球):\n"
            report += f"  大球: {ou['over']:.1%} | 小球: {ou['under']:.1%}\n"
            report += f"  预期总进球: {ou['total_lambda']:.2f}\n"
            
            # 实力分析
            hs = pred['home_strength']
            aws = pred['away_strength']
            report += f"\n**实力对比**:\n"
            report += f"  {home}: 实力={hs['strength']:.1f} 进攻={hs['attack']:.2f} 防守={hs['defense']:.2f} "
            if hs['injured_count'] > 0:
                report += f"伤病={hs['injured_count']}人"
            report += "\n"
            report += f"  {away}: 实力={aws['strength']:.1f} 进攻={aws['attack']:.2f} 防守={aws['defense']:.2f} "
            if aws['injured_count'] > 0:
                report += f"伤病={aws['injured_count']}人"
            report += "\n"
            
            # 爆冷分析
            upset = pred['upset_potential']
            report += f"\n**爆冷分析**: {upset['warning_level']} {upset['level']} (指数: {upset['index']:.0f})\n"
            if upset['factors']:
                report += f"  风险因素: {', '.join(upset['factors'])}\n"

            odds_ref = pred.get('historical_odds_reference', {})
            if odds_ref.get('available'):
                summary = odds_ref.get('summary', {})
                report += "\n**历史赔率参考**:\n"
                report += (
                    f"  相似样本: {summary.get('sample_size', 0)} 场 | "
                    f"主胜: {summary.get('result_rates', {}).get('主胜', 0.0):.1%} | "
                    f"平局: {summary.get('result_rates', {}).get('平局', 0.0):.1%} | "
                    f"客胜: {summary.get('result_rates', {}).get('客胜', 0.0):.1%}\n"
                )
                report += f"  高赔赛果占比: {summary.get('cold_result_rate', 0.0):.1%}\n"
                if odds_ref.get('insights'):
                    report += f"  参考结论: {'；'.join(odds_ref['insights'])}\n"
                for similar in odds_ref.get('similar_matches', [])[:3]:
                    report += (
                        f"  - {similar['match_date']} {similar['home_team']} vs {similar['away_team']} "
                        f"=> {similar['actual_result']} ({similar['actual_score']}) "
                        f"[相似度 {similar['similarity']:.2f}]\n"
                    )
            
            report += "\n" + "-"*60 + "\n"
        
        # 统计摘要
        report += "\n" + "="*80 + "\n"
        report += "## 📈 预测统计摘要\n"
        
        high_conf = [p for p in predictions if p['confidence'] >= 0.7]
        med_conf = [p for p in predictions if 0.5 <= p['confidence'] < 0.7]
        low_conf = [p for p in predictions if p['confidence'] < 0.5]
        
        report += f"\n- **高信心预测** (≥70%): {len(high_conf)} 场\n"
        report += f"- **中等信心预测** (50%-70%): {len(med_conf)} 场\n"
        report += f"- **低信心预测** (<50%): {len(low_conf)} 场\n"
        
        # 爆冷警告
        upset_warnings = [p for p in predictions if p['upset_potential']['level'] == '高']
        if upset_warnings:
            report += f"\n## ⚠️ 爆冷警告\n"
            for p in upset_warnings:
                report += f"\n- {p['home_team']} vs {p['away_team']}\n"
                report += f"  风险因素: {', '.join(p['upset_potential']['factors'])}\n"
        
        report += "\n" + "="*80 + "\n"
        report += "## 💡 使用说明\n\n"
        report += "1. 本报告基于多模型融合预测，仅供参考\n"
        report += "2. 高信心预测（≥70%）可靠性较高\n"
        report += "3. 爆冷警告需特别关注\n"
        report += "4. 预测结果会根据实际比赛结果持续优化\n"
        
        return report

def main():
    """主函数 - 演示增强版预测流程"""
    print("="*80)
    print("🔮 增强版足球预测系统")
    print("="*80)
    
    # 初始化预测器
    predictor = EnhancedPredictor()
    
    # 预测今天和未来几天的比赛
    base_date = datetime.now()
    
    for league_code in LEAGUE_CONFIG.keys():
        print(f"\n📊 处理 {LEAGUE_CONFIG[league_code]['name']}...")
        
        for day_offset in range(3):
            match_date = (base_date + timedelta(days=day_offset)).strftime('%Y-%m-%d')
            
            print(f"  📅 生成 {match_date} 的预测...")
            report_file = predictor.generate_prediction_report(league_code, match_date)
            
            if report_file:
                print(f"  ✅ 预测报告已生成: {os.path.basename(report_file)}")
            else:
                print(f"  ⚠️  未能生成预测报告")
    
    print("\n" + "="*80)
    print("🎉 预测流程执行完成！")
    print("="*80)

if __name__ == "__main__":
    main()
