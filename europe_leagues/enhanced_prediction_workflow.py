#!/usr/bin/env python3
"""模块说明：作为增强预测主编排器，协调实时刷新、核心推理、后处理、写回与归档流程。

增强版预测比赛流程
整合多模型融合、智能缓存、动态权重调整的完整预测系统"""

import os
import sys
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

from collectors.odds_snapshots import OddsSnapshotRepository
from domain.intelligence import MatchIntelligenceEngine
from domain.inference import InferencePipelineService
from domain.live import LiveRefreshService
from domain.persistence import PredictionPersistenceService
from domain.postprocess import PredictionPostprocessService
from domain.reporting import PredictionReportService
from domain.team_strength import TeamStrengthService
from domain.upset import UpsetAnalyzer
from domain.writeback import TeamsWritebackGateway
from runtime.paths import get_default_paths

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# 导入机器学习模型
from domain.features import (
    LeagueOverUnderLearning,
    TeamEWMALearning,
)
from domain.odds import (
    HistoricalOddsReference,
)
from models import MultiModelFusion, PoissonModel
from result_manager import ResultManager
from agent_runtime_registry import get_runtime_profile
from runtime.cache import PredictionCache

# 配置日志
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = str(get_default_paths(SCRIPT_DIR).ensure_runtime_dir())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(RUNTIME_DIR, 'enhanced_prediction.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

class TeamDataManager(TeamStrengthService):
    """兼容别名，后续逐步移除。"""

    pass


class EnhancedPredictor:
    """增强版预测器 - 整合所有功能"""

    def __init__(self, base_dir: str = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.paths = get_default_paths(self.base_dir)
        self.writeback = TeamsWritebackGateway(self.base_dir)
        self.team_manager = TeamStrengthService(self.base_dir)
        self.league_ou_learning = LeagueOverUnderLearning(self.base_dir)
        self.team_ewma_learning = TeamEWMALearning(self.base_dir)
        self.odds_reference = HistoricalOddsReference(list(LEAGUE_CONFIG.keys()), self.base_dir)
        self.snapshot_repository = OddsSnapshotRepository(self.base_dir, self.odds_reference)
        self.reporting_service = PredictionReportService(LEAGUE_CONFIG)
        self.upset_analyzer = UpsetAnalyzer(self.base_dir, LEAGUE_CONFIG)
        self.match_intelligence_engine = MatchIntelligenceEngine(self.base_dir, self.writeback, self.team_ewma_learning)
        self.weight_adjuster = DynamicWeightAdjuster()
        self.cache = PredictionCache()
        self.result_manager = ResultManager(base_dir=self.base_dir)
        self.postprocess_service = PredictionPostprocessService(LEAGUE_CONFIG)
        self.persistence_service = PredictionPersistenceService(self.base_dir, self.cache, self.result_manager)
        # 初始化多模型融合
        self.model_fusion = MultiModelFusion()
        self.poisson_model = PoissonModel()
        self.live_refresh_service = LiveRefreshService(self.base_dir, LEAGUE_CONFIG, self.team_ewma_learning)
        self.inference_service = InferencePipelineService(
            league_config=LEAGUE_CONFIG,
            team_manager=self.team_manager,
            match_intelligence_engine=self.match_intelligence_engine,
            odds_reference=self.odds_reference,
            upset_analyzer=self.upset_analyzer,
            model_fusion=self.model_fusion,
            poisson_model=self.poisson_model,
            weight_adjuster=self.weight_adjuster,
            league_ou_learning=self.league_ou_learning,
            postprocess_service=self.postprocess_service,
        )
        self.runtime_profile = get_runtime_profile(
            ["data_collector", "match_analyzer", "odds_analyzer"]
        )

    def _memory_file_path(self) -> str:
        return self.persistence_service.memory_file_path()

    def _format_memory_prediction_entry(self, result: Dict[str, Any]) -> str:
        return self.persistence_service.format_memory_prediction_entry(result)

    def _update_prediction_memory(self, result: Dict[str, Any]) -> None:
        self.persistence_service.update_prediction_memory(result)

    @staticmethod
    def _normalize_probs(p: Dict[str, float]) -> Dict[str, float]:
        return PredictionPostprocessService.normalize_probs(p)

    def _compute_total_goals_distribution(
        self,
        score_probs: Dict[str, float],
        max_bucket: int = 7,
    ) -> Dict[str, Any]:
        return self.postprocess_service.compute_total_goals_distribution(score_probs, max_bucket=max_bucket)

    def _extract_decimal_odds_1x2(self, current_odds: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        return self.postprocess_service.extract_decimal_odds_1x2(current_odds)

    @staticmethod
    def _parse_handicap_value(value: Any) -> Optional[float]:
        """Parse asian handicap text/value into a numeric line from home-team perspective.

        Convention:
        - Negative: home gives handicap
        - Positive: away gives handicap
        """
        if value is None:
            return None
        if isinstance(value, (int, float)):
            v = float(value)
            return v if abs(v) > 1e-9 else 0.0

        s = str(value).strip()
        if not s:
            return None

        # Numeric forms like -0.5 / 0.25 / 0.5/1
        try:
            if "/" in s and not any(ch in s for ch in "球平受让"):
                parts = [float(x) for x in s.split("/") if x]
                if parts:
                    return sum(parts) / len(parts)
            return float(s)
        except Exception:
            pass

        mapping = {
            "平手": 0.0,
            "平手/半球": -0.25,
            "平/半": -0.25,
            "半球": -0.5,
            "半球/一球": -0.75,
            "半/一": -0.75,
            "一球": -1.0,
            "一球/球半": -1.25,
            "一/球半": -1.25,
            "球半": -1.5,
            "球半/两球": -1.75,
            "两球": -2.0,
            "两球/两球半": -2.25,
            "两球半": -2.5,
            "受让平手": 0.0,
            "受让平手/半球": 0.25,
            "受让平/半": 0.25,
            "受让半球": 0.5,
            "受让半球/一球": 0.75,
            "受让半/一": 0.75,
            "受让一球": 1.0,
            "受让一球/球半": 1.25,
            "受让一/球半": 1.25,
            "受让球半": 1.5,
            "受让球半/两球": 1.75,
            "受让两球": 2.0,
            "受让两球/两球半": 2.25,
            "受让两球半": 2.5,
        }
        s = s.replace(" ", "")
        if s in mapping:
            return mapping[s]
        return None

    def _detect_market_odds_anomaly(
        self,
        league_code: str,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        base_home_lambda: Optional[float] = None,
        base_away_lambda: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self.inference_service.detect_market_odds_anomaly(
            league_code=league_code,
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            base_home_lambda=base_home_lambda,
            base_away_lambda=base_away_lambda,
        )

    @staticmethod
    def _kelly_fraction(p: float, odds: float) -> Optional[float]:
        return PredictionPostprocessService.kelly_fraction(p, odds)

    def _build_kelly_staking(
        self,
        final_probabilities: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str] = None,
        cap_half: float = 0.05,
        cap_quarter: float = 0.03,
    ) -> Dict[str, Any]:
        return self.postprocess_service.build_kelly_staking(
            final_probabilities=final_probabilities,
            current_odds=current_odds,
            predicted_outcome=predicted_outcome,
            cap_half=cap_half,
            cap_quarter=cap_quarter,
        )

    def _apply_dynamic_weights(self, league_code: str) -> Dict[str, Any]:
        return self.inference_service.apply_dynamic_weights(league_code)

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
        asian_handicap: Optional[Dict[str, Any]] = None,
    ) -> tuple[float, float, Dict[str, Any]]:
        return self.inference_service.calibrate_lambdas_from_market(
            league_code=league_code,
            base_home_lambda=base_home_lambda,
            base_away_lambda=base_away_lambda,
            european_odds=european_odds,
            asian_handicap=asian_handicap,
        )

    def _apply_league_ou_learning(
        self,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        home_lambda: float,
        away_lambda: float,
        strength_diff: float,
    ) -> tuple[float, float, Dict[str, Any]]:
        return self.inference_service.apply_league_ou_learning(
            league_code=league_code,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            strength_diff=strength_diff,
        )

    def _apply_live_outcome_adjustment(
        self,
        league_code: str,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, float], Dict[str, Any]]:
        return self.inference_service.apply_live_outcome_adjustment(
            league_code=league_code,
            final_prob=final_prob,
            current_odds=current_odds,
            historical_odds_reference=historical_odds_reference,
        )


    @staticmethod
    def _extract_current_odds_snapshot(match_record: Dict) -> Dict:
        return OddsSnapshotRepository.extract_current_odds_snapshot(match_record)

    @staticmethod
    def _extract_current_odds_live_snapshot(snapshot: Dict) -> Dict:
        return OddsSnapshotRepository.extract_current_odds_live_snapshot(snapshot)


    def _get_matches_from_odds_history(self, league_code: str, match_date: str) -> List[Dict]:
        return self.snapshot_repository.get_matches_from_odds_history(league_code, match_date)

    def _get_matches_from_odds_snapshots(self, league_code: str, match_date: str) -> List[Dict]:
        return self.snapshot_repository.get_matches_from_odds_snapshots(league_code, match_date)

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        return LiveRefreshService.to_float(value)

    def _extract_current_odds_from_csv_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return self.snapshot_repository.extract_current_odds_from_csv_row(row)
    
    def predict_match(
        self,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str = None,
        current_odds: Optional[Dict] = None,
        match_id: str = "",
        force_refresh_odds: bool = True,
        okooo_driver: str = "local-chrome",
        okooo_headed: bool = False,
        match_time: str = "",
        league_hint: Optional[str] = None,
        analysis_context: Optional[Dict] = None,
    ) -> Dict:
        """预测单场比赛（增强版）。

        默认尝试实时刷新澳客赔率快照并注入 current_odds，以便赔率相似盘路与爆冷评估使用最新数据。
        其他多维度信息（战术/战意/首发/临场变化等）可由调用方通过 analysis_context 传入。
        """
        _ = league_hint
        prep = self.live_refresh_service.prepare_prediction_inputs(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            match_date=match_date,
            current_odds=current_odds,
            match_id=match_id,
            force_refresh_odds=force_refresh_odds,
            okooo_driver=okooo_driver,
            okooo_headed=okooo_headed,
            match_time=match_time,
            analysis_context=analysis_context,
        )
        match_date = prep["match_date"]
        analysis_context = prep["analysis_context"]
        current_odds = prep["current_odds"]
        realtime = prep["realtime"]
        cache_params = prep["cache_params"]
        cached = self.cache.get('predict_match', cache_params)
        if cached:
            logger.info(f"使用缓存预测: {home_team} vs {away_team}")
            return self.persistence_service.prepare_cached_prediction(cached, self.runtime_profile)
        
        
        logger.info(f"开始预测: {home_team} vs {away_team} ({league_code})")
        current_odds = self.live_refresh_service.ensure_totals_if_needed(
            league_code=league_code,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            current_odds=current_odds,
            analysis_context=analysis_context,
            realtime=realtime,
            force_refresh_odds=force_refresh_odds,
            okooo_driver=okooo_driver,
            okooo_headed=okooo_headed,
            match_time=match_time,
            diag_key="okooo_totals_fetch_last_moment",
        )
        core = self.inference_service.run(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            match_date=match_date,
            current_odds=current_odds,
            analysis_context=analysis_context,
            realtime=realtime,
        )
        applied_weights = core["applied_weights"]
        home_strength = core["home_strength"]
        away_strength = core["away_strength"]
        match_intelligence = core["match_intelligence"]
        strength_diff = core["strength_diff"]
        final_prob = core["final_probabilities"]
        probs = core["ranked_probabilities"]
        main_prediction = core["main_prediction"]
        confidence = core["confidence"]
        historical_odds_reference = core["historical_odds_reference"]
        upset_potential = core["upset_potential"]
        top_scores = core["top_scores"]
        total_goals = core["total_goals"]
        home_lambda = core["home_lambda"]
        away_lambda = core["away_lambda"]
        over_under = core["over_under"]
        fusion_result = core["fusion_result"]

        # 5.5 凯利仓位建议（基于模型概率 + 欧赔/竞彩赔率；输出半凯利封顶5% + 1/4凯利封顶3%）
        staking = {}
        try:
            kelly = self._build_kelly_staking(
                final_probabilities=final_prob,
                current_odds=current_odds,
                predicted_outcome=main_prediction,
                cap_half=0.05,
                cap_quarter=0.03,
            )
            recommended = kelly.get("recommended", {}) if isinstance(kelly, dict) else {}
            staking = {"kelly": kelly, "recommended": recommended}
            realtime["context_applied"]["staking_kelly"] = {"available": bool(kelly.get("available")), "recommended": recommended}
        except Exception as e:
            staking = {"kelly": {"available": False, "error": str(e)}, "recommended": {}}
            realtime["context_applied"]["staking_kelly"] = {"available": False, "error": str(e)}
        
        # 构建完整结果
        result = self.postprocess_service.build_prediction_result(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            match_date=match_date,
            match_time=match_time,
            ranked_probabilities=probs,
            confidence=confidence,
            top_scores=top_scores,
            total_goals=total_goals,
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            over_under=over_under,
            staking=staking,
            strength_diff=strength_diff,
            home_strength=home_strength,
            away_strength=away_strength,
            upset_potential=upset_potential,
            match_intelligence=match_intelligence,
            historical_odds_reference=historical_odds_reference,
            fusion_result=fusion_result,
            final_probabilities=final_prob,
            applied_model_weights=applied_weights,
            realtime=realtime,
            analysis_context=analysis_context,
            runtime_profile=self.runtime_profile,
            current_odds=current_odds,
        )

        self.persistence_service.persist_prediction('predict_match', cache_params, result, league_code)
        logger.info(f"预测完成: {home_team} vs {away_team} -> {main_prediction} ({confidence:.1%})")
        
        return result
    
    def generate_prediction_report(self, league_code: str, match_date: str, 
                                  matches: List[Dict] = None) -> Optional[str]:
        """生成预测并写回 teams_2025-26.md（不再生成独立 predictions.md 文件）"""
        if not matches:
            matches = self.snapshot_repository.get_matches_from_odds_snapshots(league_code, match_date)
            if not matches:
                matches = self.snapshot_repository.get_matches_from_odds_history(league_code, match_date)
            if not matches:
                matches = self.reporting_service.get_sample_matches(league_code)
        else:
            matches = self.snapshot_repository.fill_missing_current_odds(league_code, match_date, matches)
        
        if not matches:
            logger.warning(f"没有比赛数据: {league_code} {match_date}")
            return None
        
        # 预测所有比赛
        predictions = []
        for match in matches:
            home = match.get('home_team', match.get('主队'))
            away = match.get('away_team', match.get('客队'))
            current_odds = match.get('current_odds')
            current_odds = self.live_refresh_service.refresh_report_match_odds(
                league_code=league_code,
                match_date=match_date,
                home_team=home,
                away_team=away,
                current_odds=current_odds,
            )

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
        teams_path = self.writeback.teams_file_path(league_code)
        if os.path.exists(teams_path):
            self.writeback.write_predictions(league_code, match_date, predictions)
            logger.info(f"已更新 teams 文件: {teams_path}")
        else:
            logger.warning(f"未找到 teams 文件: {teams_path}")
        
        self.persistence_service.persist_prediction_batch(predictions, league_code)
        logger.info(f"预测已保存到历史数据库")
        
        return teams_path if os.path.exists(teams_path) else None
    
    def _get_sample_matches(self, league_code: str) -> List[Dict]:
        return self.reporting_service.get_sample_matches(league_code)
    
    def _format_report(self, league_code: str, match_date: str, predictions: List[Dict]) -> str:
        return self.reporting_service.format_report(league_code, match_date, predictions)

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
