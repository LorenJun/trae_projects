"""模块说明：滚动记忆去重与标准化工具。

提供球队名称标准化、比赛唯一性检测、重复条目清理等功能。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

# 球队名称标准化映射（硬编码常用别名）
TEAM_NAME_ALIASES: Dict[str, Dict[str, str]] = {
    'la_liga': {
        # 马略卡的各种译名
        '马略卡': '马略卡',
        '马洛卡': '马略卡',
        '馬略卡': '马略卡',
        'Mallorca': '马略卡',
        # 瓦伦西亚/巴伦西亚
        '瓦伦西亚': '瓦伦西亚',
        '巴伦西亚': '瓦伦西亚',
        '華倫西亞': '瓦伦西亚',
        'Valencia': '瓦伦西亚',
        # 皇家社会
        '皇家社会': '皇家社会',
        '皇家蘇斯達': '皇家社会',
        'Real Sociedad': '皇家社会',
        # 毕尔巴鄂竞技
        '毕尔巴鄂竞技': '毕尔巴鄂竞技',
        '毕尔包': '毕尔巴鄂竞技',
        'Athletic Bilbao': '毕尔巴鄂竞技',
        # 奥萨苏纳
        '奥萨苏纳': '奥萨苏纳',
        '奧沙辛拿': '奥萨苏纳',
        'Osasuna': '奥萨苏纳',
        # 比利亚雷亚尔
        '比利亚雷亚尔': '比利亚雷亚尔',
        '比利亚雷': '比利亚雷亚尔',
        '維拉利爾': '比利亚雷亚尔',
        'Villarreal': '比利亚雷亚尔',
        # 塞维利亚
        '塞维利亚': '塞维利亚',
        '塞維利亞': '塞维利亚',
        'Sevilla': '塞维利亚',
        # 塞尔塔
        '塞尔塔': '塞尔塔',
        '切爾達': '塞尔塔',
        'Celta': '塞尔塔',
        # 格拉纳达
        '格拉纳达': '格拉纳达',
        '格蘭納達': '格拉纳达',
        'Granada': '格拉纳达',
        # 莱万特
        '莱万特': '莱万特',
        '利雲特': '莱万特',
        'Levante': '莱万特',
        # 西班牙人
        '西班牙人': '西班牙人',
        '愛斯賓奴': '西班牙人',
        'Espanyol': '西班牙人',
        # 阿拉维斯
        '阿拉维斯': '阿拉维斯',
        '艾拉維斯': '阿拉维斯',
        'Alaves': '阿拉维斯',
        # 赫塔费
        '赫塔费': '赫塔费',
        '基達菲': '赫塔费',
        'Getafe': '赫塔费',
        # 赫罗纳
        '赫罗纳': '赫罗纳',
        '基羅納': '赫罗纳',
        'Girona': '赫罗纳',
        # 巴列卡诺
        '巴列卡诺': '巴列卡诺',
        '華歷簡奴': '巴列卡诺',
        'Rayo Vallecano': '巴列卡诺',
        # 加的斯
        '加的斯': '加的斯',
        '卡迪斯': '加的斯',
        'Cadiz': '加的斯',
    },
    'premier_league': {
        # 曼联
        '曼联': '曼联',
        '曼聯': '曼联',
        'Manchester United': '曼联',
        # 曼城
        '曼城': '曼城',
        '曼聯城': '曼城',
        'Manchester City': '曼城',
        # 切尔西
        '切尔西': '切尔西',
        '車路士': '切尔西',
        'Chelsea': '切尔西',
        # 阿森纳
        '阿森纳': '阿森纳',
        '阿仙奴': '阿森纳',
        'Arsenal': '阿森纳',
        # 利物浦
        '利物浦': '利物浦',
        'Liverpool': '利物浦',
        # 热刺
        '热刺': '热刺',
        '熱刺': '热刺',
        'Tottenham': '热刺',
        # 莱斯特城
        '莱斯特城': '莱斯特城',
        '李斯特城': '莱斯特城',
        'Leicester': '莱斯特城',
    },
    'serie_a': {
        # 尤文图斯
        '尤文图斯': '尤文图斯',
        '祖雲達斯': '尤文图斯',
        'Juventus': '尤文图斯',
        # AC米兰
        'AC米兰': 'AC米兰',
        'AC米蘭': 'AC米兰',
        'Milan': 'AC米兰',
        # 国际米兰
        '国际米兰': '国际米兰',
        '國際米蘭': '国际米兰',
        'Inter': '国际米兰',
        # 罗马
        '罗马': '罗马',
        '羅馬': '罗马',
        'Roma': '罗马',
        # 那不勒斯
        '那不勒斯': '那不勒斯',
        '拿坡里': '那不勒斯',
        'Napoli': '那不勒斯',
        # 拉齐奥
        '拉齐奥': '拉齐奥',
        '拉素': '拉齐奥',
        'Lazio': '拉齐奥',
        # 亚特兰大
        '亚特兰大': '亚特兰大',
        '阿特蘭大': '亚特兰大',
        'Atalanta': '亚特兰大',
        # 佛罗伦萨
        '佛罗伦萨': '佛罗伦萨',
        '費倫天拿': '佛罗伦萨',
        'Fiorentina': '佛罗伦萨',
    },
    'bundesliga': {
        # 拜仁慕尼黑
        '拜仁慕尼黑': '拜仁慕尼黑',
        '拜仁': '拜仁慕尼黑',
        'Bayern': '拜仁慕尼黑',
        # 多特蒙德
        '多特蒙德': '多特蒙德',
        '多蒙特': '多特蒙德',
        'Dortmund': '多特蒙德',
        # 勒沃库森
        '勒沃库森': '勒沃库森',
        '利華古遜': '勒沃库森',
        'Leverkusen': '勒沃库森',
        # 莱比锡
        '莱比锡': '莱比锡',
        '萊比錫': '莱比锡',
        'Leipzig': '莱比锡',
    },
    'ligue_1': {
        # 巴黎圣日耳曼
        '巴黎圣日耳曼': '巴黎圣日耳曼',
        '巴黎聖日耳門': '巴黎圣日耳曼',
        'PSG': '巴黎圣日耳曼',
        'Paris SG': '巴黎圣日耳曼',
        # 马赛
        '马赛': '马赛',
        '馬賽': '马赛',
        'Marseille': '马赛',
        # 里昂
        '里昂': '里昂',
        'Lyon': '里昂',
        # 摩纳哥
        '摩纳哥': '摩纳哥',
        '摩納哥': '摩纳哥',
        'Monaco': '摩纳哥',
        # 里尔
        '里尔': '里尔',
        '里爾': '里尔',
        'Lille': '里尔',
        # 尼斯
        '尼斯': '尼斯',
        'Nice': '尼斯',
        # 朗斯
        '朗斯': '朗斯',
        'Lens': '朗斯',
        # 布雷斯特
        '布雷斯特': '布雷斯特',
        '比斯特': '布雷斯特',
        'Brest': '布雷斯特',
        # 斯特拉斯堡
        '斯特拉斯堡': '斯特拉斯堡',
        '史特拉斯堡': '斯特拉斯堡',
        'Strasbourg': '斯特拉斯堡',
    },
}


def normalize_team_name(league_code: str, name: str) -> str:
    """标准化球队名称。"""
    raw_name = str(name or '').strip()
    if not raw_name:
        return ''
    
    league_map = TEAM_NAME_ALIASES.get(league_code, {})
    return league_map.get(raw_name, raw_name)


def normalize_memory_entry_key(key: str) -> Tuple[str, str, str, str]:
    """标准化记忆条目key，返回 (league, date, home, away)。
    
    处理以下情况：
    1. 球队名称标准化（马洛卡->马略卡）
    2. 忽略时间部分，只保留日期
    """
    parts = key.split('|')
    if len(parts) != 4:
        return ('', '', '', '')
    
    league, date, home, away = parts
    
    # 标准化球队名称
    home_normalized = normalize_team_name(league, home)
    away_normalized = normalize_team_name(league, away)
    
    return (league, date, home_normalized, away_normalized)


def are_entries_duplicate(key1: str, key2: str) -> bool:
    """判断两个条目key是否指向同一场比赛。"""
    norm1 = normalize_memory_entry_key(key1)
    norm2 = normalize_memory_entry_key(key2)
    
    # 如果联赛、主队、客队都相同，则认为是同一场比赛
    return (norm1[0] == norm2[0] and  # 联赛相同
            norm1[2] == norm2[2] and  # 主队相同（标准化后）
            norm1[3] == norm2[3])     # 客队相同（标准化后）


def find_duplicate_entries(entry_keys: List[str]) -> Dict[str, List[str]]:
    """在一组条目key中查找重复项。
    
    返回: {canonical_key: [duplicate_key1, duplicate_key2, ...]}
    """
    normalized_map: Dict[Tuple[str, str, str], List[str]] = {}
    
    for key in entry_keys:
        league, date, home, away = normalize_memory_entry_key(key)
        if not league or not home or not away:
            continue
        
        # 使用 (league, home, away) 作为去重键（忽略日期）
        dedupe_key = (league, home, away)
        
        if dedupe_key not in normalized_map:
            normalized_map[dedupe_key] = []
        normalized_map[dedupe_key].append(key)
    
    # 找出有重复的组
    duplicates = {}
    for dedupe_key, keys in normalized_map.items():
        if len(keys) > 1:
            # 选择最新的作为canonical key
            canonical = max(keys, key=lambda k: extract_date_from_key(k) or '0000-00-00')
            duplicates[canonical] = [k for k in keys if k != canonical]
    
    return duplicates


def extract_date_from_key(key: str) -> Optional[str]:
    """从条目key中提取日期。"""
    parts = key.split('|')
    if len(parts) >= 2:
        return parts[1]
    return None


def extract_teams_from_entry_line(line: str) -> Tuple[str, str, str]:
    """从记忆条目行中提取联赛和球队名称。
    
    返回: (league, home, away)
    """
    # 匹配格式: - [league|date|home|away] ...
    match = re.match(r'- \[([^\]]+)\]', line)
    if match:
        key = match.group(1)
        parts = key.split('|')
        if len(parts) == 4:
            return (parts[0], parts[2], parts[3])
    
    # 尝试从内容中提取
    vs_match = re.search(r'(\S+)\s+vs\s+(\S+)', line)
    if vs_match:
        home = vs_match.group(1)
        away = vs_match.group(2)
        # 尝试推断联赛
        league_match = re.search(r'(la_liga|premier_league|serie_a|bundesliga|ligue_1)', line)
        league = league_match.group(1) if league_match else ''
        return (league, home, away)
    
    return ('', '', '')


def clean_memory_duplicates(memory_content: str) -> Tuple[str, int]:
    """清理记忆内容中的重复条目。
    
    返回: (清理后的内容, 删除的条目数)
    """
    from domain.persistence import PredictionPersistenceService
    
    # 提取所有条目
    entries = PredictionPersistenceService._extract_memory_entry_lines(memory_content)
    
    # 收集所有条目的key
    entry_keys = []
    for entry in entries:
        first_line = entry.split('\n')[0] if entry else ''
        match = re.match(r'- \[([^\]]+)\]', first_line)
        if match:
            entry_keys.append(match.group(1))
        else:
            entry_keys.append('')
    
    # 查找重复
    duplicates = find_duplicate_entries([k for k in entry_keys if k])
    
    if not duplicates:
        return memory_content, 0
    
    # 标记要删除的条目索引
    indices_to_remove = set()
    for canonical_key, dup_keys in duplicates.items():
        for dup_key in dup_keys:
            try:
                idx = entry_keys.index(dup_key)
                indices_to_remove.add(idx)
            except ValueError:
                pass
    
    if not indices_to_remove:
        return memory_content, 0
    
    # 构建新的条目列表
    new_entries = [entry for i, entry in enumerate(entries) if i not in indices_to_remove]
    
    # 重新渲染
    start_marker = '<!-- prediction-memory:start -->'
    end_marker = '<!-- prediction-memory:end -->'
    new_block = PredictionPersistenceService.render_prediction_memory_block(
        new_entries, start_marker, end_marker
    )
    
    # 替换原内容
    pattern = rf'{re.escape(start_marker)}\n.*?{re.escape(end_marker)}'
    new_content = re.sub(pattern, new_block, memory_content, flags=re.DOTALL)
    
    return new_content, len(indices_to_remove)


def validate_memory_entry(entry: str, existing_entries: List[str]) -> Tuple[bool, Optional[str]]:
    """验证新条目是否与现有条目重复。
    
    返回: (是否有效, 重复条目的key或None)
    """
    # 提取新条目的key
    first_line = entry.split('\n')[0] if entry else ''
    match = re.match(r'- \[([^\]]+)\]', first_line)
    if not match:
        return True, None
    
    new_key = match.group(1)
    new_norm = normalize_memory_entry_key(new_key)
    
    # 检查是否与现有条目重复
    for existing in existing_entries:
        existing_first = existing.split('\n')[0] if existing else ''
        existing_match = re.match(r'- \[([^\]]+)\]', existing_first)
        if existing_match:
            existing_key = existing_match.group(1)
            existing_norm = normalize_memory_entry_key(existing_key)
            
            # 检查是否是同一场比赛
            if (new_norm[0] == existing_norm[0] and  # 联赛相同
                new_norm[2] == existing_norm[2] and  # 主队相同
                new_norm[3] == existing_norm[3]):    # 客队相同
                return False, existing_key
    
    return True, None
