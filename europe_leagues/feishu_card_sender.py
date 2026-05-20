#!/usr/bin/env python3
"""
飞书精美卡片消息发送器
用于发送足球预测结果的交互式卡片消息
"""

import json
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime


class FeishuCardBuilder:
    """飞书卡片构建器 - 创建精美的预测结果卡片"""
    
    # 颜色配置
    COLORS = {
        'primary': '#3370FF',      # 主色调 - 蓝色
        'success': '#00B42A',      # 成功 - 绿色
        'warning': '#FF7D00',      # 警告 - 橙色
        'danger': '#F53F3F',       # 危险 - 红色
        'info': '#165DFF',         # 信息 - 深蓝
        'purple': '#722ED1',       # 紫色
        'cyan': '#14C9C9',         # 青色
        'gold': '#F7BA1E',         # 金色
        'gray': '#86909C',         # 灰色
        'light_bg': '#F2F3F5',     # 浅灰背景
        'dark_text': '#1D2129',    # 深色文字
        'medium_text': '#4E5969',  # 中等文字
        'light_text': '#86909C',   # 浅色文字
    }
    
    # 爆冷等级颜色
    UPSET_COLORS = {
        '极低': '#00B42A',   # 🟢
        '低': '#7AD91B',     # 🟢
        '中': '#FF7D00',     # 🟠
        '高': '#F53F3F',     # 🔴
        '极高': '#F53F3F',   # 🔴
    }
    
    @classmethod
    def build_prediction_card(cls, 
                              match_date: str,
                              league_name: str,
                              matches: List[Dict[str, Any]]) -> Dict:
        """
        构建预测结果卡片
        
        Args:
            match_date: 比赛日期
            league_name: 联赛名称
            matches: 比赛列表，每项包含预测详情
        """
        elements = []
        
        # 1. 头部标题
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**⚽ {league_name} 比赛预测**\n*{match_date}*"
            },
            "padding": "16px 0 8px 0"
        })
        
        # 2. 分隔线
        elements.append(cls._build_divider())
        
        # 3. 每场比赛的卡片
        for idx, match in enumerate(matches, 1):
            match_card = cls._build_match_section(match, idx)
            elements.extend(match_card)
            if idx < len(matches):
                elements.append(cls._build_divider())
        
        # 4. 底部说明
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"\n💡 **提示**: 以上预测基于10模型融合分析，仅供参考"
            },
            "padding": "12px 0 0 0"
        })
        
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"🤖 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            },
            "padding": "4px 0 0 0"
        })
        
        return {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"🏆 {league_name} 预测报告"
                },
                "subtitle": {
                    "tag": "plain_text",
                    "content": f"{match_date} · {len(matches)}场比赛"
                },
                "template": "blue"
            },
            "elements": elements
        }
    
    @classmethod
    def _build_match_section(cls, match: Dict, idx: int) -> List[Dict]:
        """构建单场比赛的卡片内容"""
        elements = []
        
        home_team = match.get('home_team', '')
        away_team = match.get('away_team', '')
        prediction = match.get('prediction', '')
        confidence = match.get('confidence', 0)
        match_id = match.get('match_id', '')
        
        # 获取概率分布
        probs = match.get('all_probabilities', {})
        home_prob = probs.get('主胜', 0) * 100
        draw_prob = probs.get('平局', 0) * 100
        away_prob = probs.get('客胜', 0) * 100
        
        # 预期进球
        expected_goals = match.get('expected_goals', {})
        home_xg = expected_goals.get('home', 0)
        away_xg = expected_goals.get('away', 0)
        total_xg = expected_goals.get('total', 0)
        
        # 推荐比分
        top_scores = match.get('top_scores', [])
        score_display = " / ".join([f"{s[0]} ({s[1]*100:.1f}%)" for s in top_scores[:3]]) if top_scores else "暂无"
        
        # 大小球
        over_under = match.get('over_under', {})
        ou_line = over_under.get('line')
        ou_line_display = f"{ou_line:g}" if isinstance(ou_line, (int, float)) else "未取得真实盘口"
        under_prob = over_under.get('under', 0) * 100
        over_prob = over_under.get('over', 0) * 100
        
        # 爆冷指数
        upset = match.get('upset_potential', {})
        upset_level = upset.get('level', '低')
        upset_index = upset.get('index', 0)
        upset_color = cls.UPSET_COLORS.get(upset_level, cls.COLORS['gray'])
        
        # 盘口信息
        market = match.get('market_snapshot', {})
        euro = market.get('欧赔', {})
        final_euro = euro.get('final', {})
        euro_str = f"{final_euro.get('home', 0):.2f} / {final_euro.get('draw', 0):.2f} / {final_euro.get('away', 0):.2f}" if final_euro else "暂无"
        
        asian = market.get('亚值', {})
        final_asian = asian.get('final', {})
        asian_handicap = final_asian.get('handicap_text', '-')
        asian_str = f"{asian_handicap} {final_asian.get('home_water', 0):.2f}/{final_asian.get('away_water', 0):.2f}" if final_asian else "暂无"
        
        # 预测结果颜色
        pred_color = cls.COLORS['success'] if prediction == '主胜' else (cls.COLORS['warning'] if prediction == '平局' else cls.COLORS['danger'])
        
        # 比赛标题栏
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**#{idx}** {home_team} **VS** {away_team}"
            },
            "padding": "8px 0"
        })
        
        # 核心预测结果 - 使用高亮样式
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**预测结果**\n<span style='color:{pred_color};font-size:20px;font-weight:bold'>{prediction}</span>"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**信心度**\n<span style='color:{cls.COLORS['primary']};font-size:18px'>{confidence*100:.1f}%</span>"
                    }
                }
            ],
            "padding": "8px 0"
        })
        
        # 概率分布 - 使用进度条样式
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**胜率分布**"
            },
            "padding": "8px 0 4px 0"
        })
        
        elements.append(cls._build_progress_bar("主胜", home_prob, cls.COLORS['success']))
        elements.append(cls._build_progress_bar("平局", draw_prob, cls.COLORS['warning']))
        elements.append(cls._build_progress_bar("客胜", away_prob, cls.COLORS['danger']))
        
        # 预期进球和比分
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**预期进球**\n{home_xg:.2f} - {away_xg:.2f} (总{total_xg:.2f})"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**推荐比分**\n{score_display}"
                    }
                }
            ],
            "padding": "8px 0"
        })
        
        # 大小球和爆冷指数
        elements.append({
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**大小球 {ou_line_display}**\n小 {under_prob:.1f}% / 大 {over_prob:.1f}%"
                    }
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**爆冷指数**\n<span style='color:{upset_color};font-weight:bold'>{upset_level}</span> ({upset_index:.0f})"
                    }
                }
            ],
            "padding": "8px 0"
        })
        
        # 盘口信息 - 折叠显示
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**📊 盘口参考**\n欧赔: {euro_str}\n亚盘: {asian_str}"
            },
            "padding": "8px 0"
        })
        
        # 风险提示
        upset_factors = upset.get('factors', [])
        if upset_factors:
            risk_text = "\n".join([f"• {f}" for f in upset_factors[:3]])
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**⚠️ 风险提示**\n{risk_text}"
                },
                "padding": "8px 0"
            })
        
        return elements
    
    @classmethod
    def _build_progress_bar(cls, label: str, value: float, color: str) -> Dict:
        """构建进度条样式的概率显示"""
        filled = int(value / 5)  # 每5%一个方块
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        return {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{label}: `{bar}` **{value:.1f}%**"
            },
            "padding": "2px 0"
        }
    
    @classmethod
    def _build_divider(cls) -> Dict:
        """构建分隔线"""
        return {
            "tag": "hr",
            "padding": "8px 0"
        }
    
    @classmethod
    def build_simple_text_card(cls, title: str, content: str, 
                               subtitle: str = "", 
                               template: str = "blue") -> Dict:
        """构建简单文本卡片"""
        elements = [{
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content
            },
            "padding": "12px 0"
        }]
        
        if subtitle:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"*{subtitle}*"
                },
                "padding": "4px 0 0 0"
            })
        
        return {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": template
            },
            "elements": elements
        }


class FeishuCardSender:
    """飞书卡片消息发送器"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        初始化发送器
        
        Args:
            webhook_url: 飞书机器人Webhook地址，如果不提供则从环境变量读取
        """
        self.webhook_url = webhook_url or os.environ.get('FEISHU_WEBHOOK_URL')
        if not self.webhook_url:
            raise ValueError("请提供webhook_url或设置FEISHU_WEBHOOK_URL环境变量")
    
    def send_card(self, card_content: Dict) -> bool:
        """
        发送卡片消息
        
        Args:
            card_content: 卡片内容字典
            
        Returns:
            是否发送成功
        """
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
                print(f"✅ 卡片消息发送成功")
                return True
            else:
                print(f"❌ 发送失败: {result.get('msg', '未知错误')}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求异常: {e}")
            return False
        except Exception as e:
            print(f"❌ 发送异常: {e}")
            return False
    
    def send_prediction_report(self, 
                               match_date: str,
                               league_name: str,
                               matches: List[Dict[str, Any]]) -> bool:
        """
        发送预测报告卡片
        
        Args:
            match_date: 比赛日期
            league_name: 联赛名称
            matches: 比赛预测列表
        """
        card = FeishuCardBuilder.build_prediction_card(match_date, league_name, matches)
        return self.send_card(card)
    
    def send_text_message(self, title: str, content: str, 
                          subtitle: str = "",
                          template: str = "blue") -> bool:
        """发送简单文本卡片"""
        card = FeishuCardBuilder.build_simple_text_card(title, content, subtitle, template)
        return self.send_card(card)


# 便捷函数
def send_prediction_to_feishu(matches_data: List[Dict], 
                               match_date: str = "2026-05-13",
                               league_name: str = "西甲",
                               webhook_url: Optional[str] = None) -> bool:
    """
    便捷函数：发送预测结果到飞书
    
    Args:
        matches_data: 预测结果数据列表
        match_date: 比赛日期
        league_name: 联赛名称
        webhook_url: 飞书Webhook地址
    """
    sender = FeishuCardSender(webhook_url)
    return sender.send_prediction_report(match_date, league_name, matches_data)


# 示例用法
if __name__ == "__main__":
    import os
    
    # 示例数据
    sample_matches = [
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
    
    # 发送测试
    webhook = os.environ.get('FEISHU_WEBHOOK_URL')
    if webhook:
        sender = FeishuCardSender(webhook)
        success = sender.send_prediction_report("2026-05-13", "西甲", sample_matches)
        print(f"发送结果: {'成功' if success else '失败'}")
    else:
        # 打印卡片JSON供测试
        card = FeishuCardBuilder.build_prediction_card("2026-05-13", "西甲", sample_matches)
        print(json.dumps(card, ensure_ascii=False, indent=2))
