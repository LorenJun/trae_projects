"""模块说明：提供领域层对外预测外壳，屏蔽内部复杂实现细节。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from enhanced_prediction_workflow import EnhancedPredictor, LEAGUE_CONFIG


class DomainPredictor:
    """领域层预测外壳，向接口层隐藏超大实现文件。"""

    def __init__(self, base_dir: Optional[str] = None):
        self._predictor = EnhancedPredictor(base_dir=base_dir)

    def predict_match(self, **kwargs) -> Dict[str, Any]:
        return self._predictor.predict_match(**kwargs)

    def generate_prediction_report(
        self,
        league_code: str,
        match_date: str,
        matches: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        return self._predictor.generate_prediction_report(league_code, match_date, matches=matches)


__all__ = ["DomainPredictor", "LEAGUE_CONFIG"]
