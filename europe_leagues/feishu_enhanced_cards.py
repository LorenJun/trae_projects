#!/usr/bin/env python3
"""
飞书精美卡片消息发送器 - 增强版
支持更多视觉效果和交互元素
"""

import json
import requests
import os
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass


@dataclass
class MatchPrediction:
    """比赛预测数据类"""
    match_id: str
    home_team: str
    away_team: str
    prediction: str
    confidence: float
    home_prob: float
    draw_prob: float
    away_prob: float
    home_xg: float
    away_xg: float
    total_xg: float
    top_scores: List[Tuple[str, float]]
    ou_line: float
    under_prob: float
    over_prob: float
    upset_level: str
    upset_index: float
    upset_factors: List[str]
    euro_odds: Dict[str, float]
    asian_handicap: str
    asian_water_home: float
    asian_water_away: float


class FeishuEnhancedCardBuilder:
    """飞书增强版卡片构建器"""
    
    # 颜色配置 - 更丰富的配色
    COLORS = {
        'primary': '#3370FF',
        'success': '#00B42A',
        'warning': '#FF7D00',
        'danger': '#F53F3F',
        'info': '#165DFF',
        'purple': '#722ED1',
        'cyan': '#14C9C9',
        'gold': '#F7BA1E',
        'pink': '#F5319D',
        'lime': '#9FDB1D',
        'gray': '#86909C',
        'light_gray': '#F2F3F5',
        'dark': '#1D2129',
    }
    
    # 爆冷等级配置
    UPSET_CONFIG = {
        '极低': {'color': '#00B42A', 'icon': '🟢', 'risk': '安全'},
        '低': {'color': '#7AD91B', 'icon': '🟢', 'risk': '较低'},
        '中': {'color': '#FF7D00', 'icon': '🟠', 'risk': '中等'},
        '高': {'color': '#F53F3F', 'icon': '🔴', 'risk': '较高'},
        '极高': {'color': '#F5319D', 'icon': '🔴', 'risk': '极高'},
    }
    
    # 预测结果图标
    PREDICTION_ICONS = {
        '主胜': '🏠',
        '平局': '🤝',
        '客胜': '✈️',
    }
    
    @classmethod
    def build_enhanced_prediction_card(cls,
                                        match_date: str,
                                        league_name: str,
                                        matches: List[Dict[str, Any]]) -> Dict:
        """构建增强版预测卡片"""
        elements = []
        
        # 1. 头部统计概览
        elements.append(cls._build_header_stats(matches))
        elements.append(cls._build_divider())
        
        # 2. 每场比赛的详细卡片
        for idx, match in enumerate(matches, 1):
            match_elements = cls._build_enhanced_match_card(match, idx, len(matches))
            elements.extend(match_elements)
        
        # 3. 底部信息
        elements.append(cls._build_divider())
        elements.append(cls._build_footer())
        
        return {
            "config": {
                "wide_screen_mode": True,
                "enable_forward": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🏆 {league_name} 智能预测报告"
                },
                "subtitle": {
                    "tag": "plain_text",
                    "content": f"{match_date} · {len(matches)}场比赛 · AI分析"
                },
                "template": "blue",
                "icon": {
                    "tag": "standard_icon",
                    "token": "soccer",
                    "color": "blue"
                }
            },
            "elements": elements
        }
    
    @classmethod
    def _build_header_stats(cls, matches: List[Dict]) -> Dict:
        """构建头部统计概览"""
        total_matches = len(matches)
        home_wins = sum(1 for m in matches if m.get('prediction') == '主胜')
        draws = sum(1 for m in matches if m.get('prediction') == '平局')
        away_wins = sum(1 for m in matches if m.get('prediction') == '客胜')
        
        avg_confidence = sum(m.get('confidence', 0) for m in matches) / total_matches * 100 if total_matches > 0 else 0
        
        return {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📊 预测分布**\n🏠主胜 {home_wins}  |  🤝平局 {draws}  |  ✈️客胜 {away_wins}"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**🎯 平均信心**\n<span style='color:{cls.COLORS['primary']};font-size:16px'>{avg_confidence:.1f}%</span>"
                    }
                }
            ],
            "padding": "12px 0",
            "background_style": "grey"
        }
    
    @classmethod
    def _build_enhanced_match_card(cls, match: Dict, idx: int, total: int) -> List[Dict]:
        """构建单场比赛的增强版卡片"""
        elements = []
        
        # 解析数据
        home_team = match.get('home_team', '')
        away_team = match.get('away_team', '')
        prediction = match.get('prediction', '')
        confidence = match.get('confidence', 0) * 100
        
        probs = match.get('all_probabilities', {})
        home_prob = probs.get('主胜', 0) * 100
        draw_prob = probs.get('平局', 0) * 100
        away_prob = probs.get('客胜', 0) * 100
        
        expected_goals = match.get('expected_goals', {})
        home_xg = expected_goals.get('home', 0)
        away_xg = expected_goals.get('away', 0)
        
        top_scores = match.get('top_scores', [])
        
        over_under = match.get('over_under', {})
        ou_line = over_under.get('line', 2.5)
        under_prob = over_under.get('under', 0) * 100
        over_prob = over_under.get('over', 0) * 100
        
        upset = match.get('upset_potential', {})
        upset_level = upset.get('level', '低')
        upset_index = upset.get('index', 0)
        upset_factors = upset.get('factors', [])
        
        market = match.get('market_snapshot', {})
        euro = market.get('欧赔', {}).get('final', {})
        asian = market.get('亚值', {}).get('final', {})
        
        # 获取配置
        pred_icon = cls.PREDICTION_ICONS.get(prediction, '⚽')
        upset_config = cls.UPSET_CONFIG.get(upset_level, cls.UPSET_CONFIG['低'])
        
        # 预测结果颜色
        pred_colors = {
            '主胜': cls.COLORS['success'],
            '平局': cls.COLORS['warning'],
            '客胜': cls.COLORS['danger']
        }
        pred_color = pred_colors.get(prediction, cls.COLORS['primary'])
        
        # === 比赛标题区 ===
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"### {pred_icon} 比赛 #{idx}: {home_team} vs {away_team}"
            },
            "padding": "16px 0 8px 0"
        })
        
        # === 核心预测结果区 - 使用 note 卡片样式 ===
        note_elements = [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**预测结果**\n<span style='color:{pred_color};font-size:24px;font-weight:bold'>{prediction}</span>"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**信心指数**\n<span style='color:{cls.COLORS['primary']};font-size:20px'>{confidence:.1f}%</span>\n{'⭐' * int(confidence/25)}"
                        }
                    }
                ]
            }
        ]
        
        elements.append({
            "tag": "note",
            "elements": note_elements,
            "padding": "12px"
        })
        
        # === 胜率分布 - 可视化柱状图 ===
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**📈 胜率分布**"
            },
            "padding": "12px 0 8px 0"
        })
        
        # 胜率柱状图
        elements.append(cls._build_probability_chart("🏠 主胜", home_prob, cls.COLORS['success']))
        elements.append(cls._build_probability_chart("🤝 平局", draw_prob, cls.COLORS['warning']))
        elements.append(cls._build_probability_chart("✈️ 客胜", away_prob, cls.COLORS['danger']))
        
        # === 关键数据区 - 双列布局 ===
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**⚽ 预期进球 (xG)**\n{home_team}: {home_xg:.2f}\n{away_team}: {away_xg:.2f}"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**🎯 最可能比分**\n" + "\n".join([f"{s[0]} ({s[1]*100:.0f}%)" for s in top_scores[:3]])
                    }
                }
            ],
            "padding": "12px 0"
        })
        
        # === 大小球 & 爆冷指数 ===
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📊 大小球 {ou_line}**\n🔽 小 {under_prob:.0f}%  |  🔼 大 {over_prob:.0f}%"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**⚠️ 爆冷风险**\n{upset_config['icon']} {upset_level} ({upset_index:.0f})\n风险等级: {upset_config['risk']}"
                    }
                }
            ],
            "padding": "8px 0"
        })
        
        # === 盘口信息 - 折叠面板 ===
        euro_str = f"{euro.get('home', 0):.2f} / {euro.get('draw', 0):.2f} / {euro.get('away', 0):.2f}" if euro else "暂无"
        asian_str = f"{asian.get('handicap_text', '-')} {asian.get('home_water', 0):.2f}/{asian.get('away_water', 0):.2f}" if asian else "暂无"
        
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**💰 盘口参考**\n🌍 欧赔: {euro_str}\n🎌 亚盘: {asian_str}"
            },
            "padding": "8px 0"
        })
        
        # === 风险提示 ===
        if upset_factors:
            risk_items = "\n".join([f"• {f}" for f in upset_factors[:3]])
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**🚨 风险提示**\n{risk_items}"
                },
                "padding": "8px 0",
                "background_style": "warning"
            })
        
        # 分隔线（如果不是最后一场）
        if idx < total:
            elements.append(cls._build_divider())
        
        return elements
    
    @classmethod
    def _build_probability_chart(cls, label: str, value: float, color: str) -> Dict:
        """构建概率柱状图"""
        # 使用 emoji 创建可视化柱状图
        filled_blocks = int(value / 5)
        bar = "█" * filled_blocks + "░" * (20 - filled_blocks)
        
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{label} `{bar}` **{value:.1f}%**"
            },
            "padding": "2px 0"
        }
    
    @classmethod
    def _build_divider(cls) -> Dict:
        """构建分隔线"""
        return {
            "tag": "hr",
            "padding": "16px 0"
        }
    
    @classmethod
    def _build_footer(cls) -> Dict:
        """构建底部信息"""
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"💡 **说明**: 预测基于10模型融合(Poisson/ELO/xG/贝叶斯等)，仅供参考\n🤖 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            },
            "padding": "12px 0 0 0"
        }
    
    # ============ 其他实用卡片模板 ============
    
    @classmethod
    def build_match_result_card(cls,
                                 match_date: str,
                                 league_name: str,
                                 match: Dict,
                                 actual_result: str,
                                 actual_score: str,
                                 prediction_correct: bool) -> Dict:
        """构建比赛结果卡片"""
        
        home_team = match.get('home_team', '')
        away_team = match.get('away_team', '')
        prediction = match.get('prediction', '')
        
        result_icon = "✅" if prediction_correct else "❌"
        result_color = cls.COLORS['success'] if prediction_correct else cls.COLORS['danger']
        result_text = "预测正确" if prediction_correct else "预测错误"
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"### {home_team} {actual_score} {away_team}"
                },
                "padding": "12px 0"
            },
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**预测**\n{prediction}"
                        }
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**实际结果**\n{actual_result}"
                        }
                    }
                ],
                "padding": "8px 0"
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**{result_icon} {result_text}**"
                },
                "padding": "8px 0",
                "background_style": "success" if prediction_correct else "danger"
            }
        ]
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"🏆 赛果更新 - {league_name}"},
                "subtitle": {"tag": "plain_text", "content": match_date},
                "template": "green" if prediction_correct else "red"
            },
            "elements": elements
        }
    
    @classmethod
    def build_daily_schedule_card(cls,
                                   match_date: str,
                                   league_name: str,
                                   matches: List[Dict]) -> Dict:
        """构建每日赛程卡片"""
        
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**📅 {match_date} 共 {len(matches)} 场比赛**"
                },
                "padding": "12px 0"
            },
            cls._build_divider()
        ]
        
        for idx, match in enumerate(matches, 1):
            home = match.get('home_team', '')
            away = match.get('away_team', '')
            match_time = match.get('match_time', '待定')
            
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**#{idx}** {match_time} | {home} vs {away}"
                },
                "padding": "8px 0"
            })
        
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 {league_name} 今日赛程"},
                "template": "blue"
            },
            "elements": elements
        }


class FeishuEnhancedSender:
    """飞书增强版卡片发送器"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.environ.get('FEISHU_WEBHOOK_URL')
        if not self.webhook_url:
            raise ValueError("请提供webhook_url或设置FEISHU_WEBHOOK_URL环境变量")
    
    def send_card(self, card_content: Dict) -> bool:
        """发送卡片消息"""
        payload = {
            "msg_type": "interactive",
            "card": card_content
        }
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            
            if result.get('code') == 0:
                print(f"✅ 卡片发送成功")
                return True
            else:
                print(f"❌ 发送失败: {result.get('msg', '未知错误')}")
                return False
                
        except Exception as e:
            print(f"❌ 发送异常: {e}")
            return False
    
    def send_prediction_report(self,
                                match_date: str,
                                league_name: str,
                                matches: List[Dict]) -> bool:
        """发送预测报告"""
        card = FeishuEnhancedCardBuilder.build_enhanced_prediction_card(
            match_date, league_name, matches
        )
        return self.send_card(card)
    
    def send_match_result(self,
                          match_date: str,
                          league_name: str,
                          match: Dict,
                          actual_result: str,
                          actual_score: str,
                          prediction_correct: bool) -> bool:
        """发送比赛结果"""
        card = FeishuEnhancedCardBuilder.build_match_result_card(
            match_date, league_name, match, actual_result, actual_score, prediction_correct
        )
        return self.send_card(card)
    
    def send_daily_schedule(self,
                            match_date: str,
                            league_name: str,
                            matches: List[Dict]) -> bool:
        """发送每日赛程"""
        card = FeishuEnhancedCardBuilder.build_daily_schedule_card(
            match_date, league_name, matches
        )
        return self.send_card(card)


# ============ 便捷函数 ============

def send_enhanced_prediction(matches_data: List[Dict],
                              match_date: str = "2026-05-13",
                              league_name: str = "西甲",
                              webhook_url: Optional[str] = None) -> bool:
    """便捷函数：发送增强版预测报告"""
    sender = FeishuEnhancedSender(webhook_url)
    return sender.send_prediction_report(match_date, league_name, matches_data)


# ============ 测试 ============

if __name__ == "__main__":
    # 测试数据
    test_matches = [
        {
            "match_id": "1302903",
            "home_team": "塞尔塔",
            "away_team": "莱万特",
            "prediction": "主胜",
            "confidence": 0.3825,
            "all_probabilities": {"主胜": 0.3825, "平局": 0.3371, "客胜": 0.2804},
            "expected_goals": {"home": 1.50, "away": 0.86, "total": 2.36},
            "top_scores": [["1-0", 0.148], ["2-0", 0.111], ["2-1", 0.091]],
            "over_under": {"line": 2.75, "under": 0.6063, "over": 0.3937},
            "upset_potential": {"level": "低", "index": 26.0, "factors": ["历史同向反打(6次)", "相似赔率反向结果偏多(80%)"]},
            "market_snapshot": {
                "欧赔": {"final": {"home": 1.80, "draw": 3.71, "away": 4.11}},
                "亚值": {"final": {"handicap_text": "半球", "home_water": 2.01, "away_water": 2.09}}
            }
        },
        {
            "match_id": "1302902",
            "home_team": "皇家贝蒂斯",
            "away_team": "埃尔切",
            "prediction": "主胜",
            "confidence": 0.3899,
            "all_probabilities": {"主胜": 0.3899, "平局": 0.3504, "客胜": 0.2597},
            "expected_goals": {"home": 1.72, "away": 0.85, "total": 2.56},
            "top_scores": [["1-0", 0.139], ["2-0", 0.119], ["2-1", 0.096]],
            "over_under": {"line": 3.0, "under": 0.6866, "over": 0.3134},
            "upset_potential": {"level": "低", "index": 26.0, "factors": ["历史同向反打(6次)", "相似赔率反向结果偏多(80%)"]},
            "market_snapshot": {
                "欧赔": {"final": {"home": 1.59, "draw": 4.18, "away": 5.12}},
                "亚值": {"final": {"handicap_text": "一球", "home_water": 2.02, "away_water": 1.90}}
            }
        },
        {
            "match_id": "1302907",
            "home_team": "奥萨苏纳",
            "away_team": "马德里竞技",
            "prediction": "主胜",
            "confidence": 0.3571,
            "all_probabilities": {"主胜": 0.3571, "平局": 0.3504, "客胜": 0.2924},
            "expected_goals": {"home": 1.19, "away": 1.06, "total": 2.25},
            "top_scores": [["1-1", 0.126], ["1-0", 0.132], ["0-1", 0.116]],
            "over_under": {"line": 2.5, "under": 0.6027, "over": 0.3973},
            "upset_potential": {"level": "低", "index": 26.0, "factors": ["历史同向反打(6次)", "相似赔率反向结果偏多(80%)"]},
            "market_snapshot": {
                "欧赔": {"final": {"home": 2.52, "draw": 3.43, "away": 2.63}},
                "亚值": {"final": {"handicap_text": "平手", "home_water": 1.92, "away_water": 2.01}}
            }
        }
    ]
    
    # 打印卡片JSON
    card = FeishuEnhancedCardBuilder.build_enhanced_prediction_card(
        "2026-05-13", "西甲", test_matches
    )
    print(json.dumps(card, ensure_ascii=False, indent=2))
