#!/usr/bin/env python3
"""
球员信息录入脚本
为五大联赛主要球队添加详细的球员信息
"""

import os
import json
from datetime import datetime

# 定义各联赛主要球队的球员信息
TEAM_PLAYERS = {
    'premier_league': {
        '切尔西': [
            {'name': '凯帕', 'position': '门将', 'age': 30, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 15000000},
            {'name': '桑切斯', 'position': '门将', 'age': 25, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 8000000},
            {'name': '科尔威尔', 'position': '后卫', 'age': 21, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '蒂亚戈·席尔瓦', 'position': '后卫', 'age': 40, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 2000000},
            {'name': '福法纳', 'position': '后卫', 'age': 24, 'nationality': '法国', 'transfer_status': 'injured', 'market_value': 40000000},
            {'name': '奇尔韦尔', 'position': '后卫', 'age': 28, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '詹姆斯', 'position': '后卫', 'age': 24, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '古斯托', 'position': '后卫', 'age': 21, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 25000000},
            {'name': '卡萨迪', 'position': '中场', 'age': 21, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 20000000},
            {'name': '恩佐', 'position': '中场', 'age': 23, 'nationality': '阿根廷', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '加拉格尔', 'position': '中场', 'age': 24, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '楚克乌梅卡', 'position': '中场', 'age': 20, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 15000000},
            {'name': '穆德里克', 'position': '前锋', 'age': 22, 'nationality': '乌克兰', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '恩昆库', 'position': '前锋', 'age': 26, 'nationality': '法国', 'transfer_status': 'injured', 'market_value': 65000000},
            {'name': '斯特林', 'position': '前锋', 'age': 29, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '布罗亚', 'position': '前锋', 'age': 22, 'nationality': '阿尔巴尼亚', 'transfer_status': 'current', 'market_value': 25000000}
        ],
        '曼联': [
            {'name': '奥纳纳', 'position': '门将', 'age': 28, 'nationality': '喀麦隆', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '希顿', 'position': '门将', 'age': 38, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 1000000},
            {'name': '瓦拉内', 'position': '后卫', 'age': 31, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 20000000},
            {'name': '马奎尔', 'position': '后卫', 'age': 31, 'nationality': '英格兰', 'transfer_status': 'suspended', 'market_value': 15000000},
            {'name': '林德洛夫', 'position': '后卫', 'age': 29, 'nationality': '瑞典', 'transfer_status': 'current', 'market_value': 18000000},
            {'name': '卢克·肖', 'position': '后卫', 'age': 29, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '万·比萨卡', 'position': '后卫', 'age': 26, 'nationality': '英格兰', 'transfer_status': 'injured', 'market_value': 25000000},
            {'name': '达洛特', 'position': '后卫', 'age': 25, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 28000000},
            {'name': '卡塞米罗', 'position': '中场', 'age': 32, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '埃里克森', 'position': '中场', 'age': 31, 'nationality': '丹麦', 'transfer_status': 'current', 'market_value': 20000000},
            {'name': 'B费', 'position': '中场', 'age': 29, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '加纳乔', 'position': '前锋', 'age': 20, 'nationality': '阿根廷', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '桑乔', 'position': '前锋', 'age': 24, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '拉什福德', 'position': '前锋', 'age': 26, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '霍伊伦', 'position': '前锋', 'age': 21, 'nationality': '丹麦', 'transfer_status': 'injured', 'market_value': 50000000}
        ],
        '利物浦': [
            {'name': '阿利森', 'position': '门将', 'age': 31, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '凯莱赫', 'position': '门将', 'age': 24, 'nationality': '爱尔兰', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '范戴克', 'position': '后卫', 'age': 32, 'nationality': '荷兰', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '科纳特', 'position': '后卫', 'age': 24, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '罗伯逊', 'position': '后卫', 'age': 30, 'nationality': '苏格兰', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '阿诺德', 'position': '后卫', 'age': 25, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 65000000},
            {'name': '麦卡利斯特', 'position': '中场', 'age': 25, 'nationality': '阿根廷', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '索博斯洛伊', 'position': '中场', 'age': 23, 'nationality': '匈牙利', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '埃利奥特', 'position': '中场', 'age': 20, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '萨拉赫', 'position': '前锋', 'age': 31, 'nationality': '埃及', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '迪亚斯', 'position': '前锋', 'age': 27, 'nationality': '哥伦比亚', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '努涅斯', 'position': '前锋', 'age': 24, 'nationality': '乌拉圭', 'transfer_status': 'current', 'market_value': 75000000}
        ],
        '阿森纳': [
            {'name': '拉亚', 'position': '门将', 'age': 28, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '加布里埃尔', 'position': '后卫', 'age': 26, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '萨利巴', 'position': '后卫', 'age': 23, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '津琴科', 'position': '后卫', 'age': 27, 'nationality': '乌克兰', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '本·怀特', 'position': '后卫', 'age': 26, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '厄德高', 'position': '中场', 'age': 25, 'nationality': '挪威', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '托马斯', 'position': '中场', 'age': 30, 'nationality': '加纳', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '赖斯', 'position': '中场', 'age': 24, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '马丁内利', 'position': '前锋', 'age': 22, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 75000000},
            {'name': '萨卡', 'position': '前锋', 'age': 22, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 90000000},
            {'name': '热苏斯', 'position': '前锋', 'age': 26, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 60000000}
        ],
        '曼城': [
            {'name': '埃德森', 'position': '门将', 'age': 30, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '斯通斯', 'position': '后卫', 'age': 29, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '鲁本·迪亚斯', 'position': '后卫', 'age': 26, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '阿克', 'position': '后卫', 'age': 28, 'nationality': '荷兰', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '沃克', 'position': '后卫', 'age': 33, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 25000000},
            {'name': '罗德里', 'position': '中场', 'age': 27, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 90000000},
            {'name': '德布劳内', 'position': '中场', 'age': 32, 'nationality': '比利时', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': 'B席', 'position': '中场', 'age': 28, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 75000000},
            {'name': '福登', 'position': '中场', 'age': 23, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 85000000},
            {'name': '哈兰德', 'position': '前锋', 'age': 23, 'nationality': '挪威', 'transfer_status': 'current', 'market_value': 180000000},
            {'name': '阿尔瓦雷斯', 'position': '前锋', 'age': 23, 'nationality': '阿根廷', 'transfer_status': 'current', 'market_value': 60000000}
        ]
    },
    'serie_a': {
        '那不勒斯': [
            {'name': '梅雷特', 'position': '门将', 'age': 27, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 25000000},
            {'name': '迪洛伦佐', 'position': '后卫', 'age': 30, 'nationality': '意大利', 'transfer_status': 'injured', 'market_value': 30000000},
            {'name': '拉赫马尼', 'position': '后卫', 'age': 27, 'nationality': '阿尔巴尼亚', 'transfer_status': 'injured', 'market_value': 25000000},
            {'name': '金玟哉', 'position': '后卫', 'age': 27, 'nationality': '韩国', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '马里奥·鲁伊', 'position': '后卫', 'age': 32, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '泽林斯基', 'position': '中场', 'age': 29, 'nationality': '波兰', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '安古伊萨', 'position': '中场', 'age': 27, 'nationality': '喀麦隆', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '洛博特卡', 'position': '中场', 'age': 29, 'nationality': '斯洛伐克', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '克瓦拉茨赫利亚', 'position': '前锋', 'age': 23, 'nationality': '格鲁吉亚', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '内雷斯', 'position': '前锋', 'age': 28, 'nationality': '巴西', 'transfer_status': 'injured', 'market_value': 25000000},
            {'name': '卢卡库', 'position': '前锋', 'age': 31, 'nationality': '比利时', 'transfer_status': 'injured', 'market_value': 40000000}
        ],
        '罗马': [
            {'name': '帕特里西奥', 'position': '门将', 'age': 35, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 8000000},
            {'name': '曼奇尼', 'position': '后卫', 'age': 27, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '斯莫林', 'position': '后卫', 'age': 34, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '伊巴涅斯', 'position': '后卫', 'age': 25, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 25000000},
            {'name': '迪巴拉', 'position': '前锋', 'age': 31, 'nationality': '阿根廷', 'transfer_status': 'injured', 'market_value': 35000000},
            {'name': '佩莱格里尼', 'position': '中场', 'age': 27, 'nationality': '意大利', 'transfer_status': 'injured', 'market_value': 40000000},
            {'name': '科内', 'position': '中场', 'age': 23, 'nationality': '科特迪瓦', 'transfer_status': 'injured', 'market_value': 20000000},
            {'name': '博韦', 'position': '中场', 'age': 20, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 15000000},
            {'name': '亚伯拉罕', 'position': '前锋', 'age': 26, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '沙拉维', 'position': '前锋', 'age': 31, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 10000000}
        ]
    },
    'bundesliga': {
        '多特蒙德': [
            {'name': '科贝尔', 'position': '门将', 'age': 26, 'nationality': '瑞士', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '胡梅尔斯', 'position': '后卫', 'age': 35, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 8000000},
            {'name': '施洛特贝克', 'position': '后卫', 'age': 24, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 35000000},
            {'name': '阿坎吉', 'position': '后卫', 'age': 27, 'nationality': '瑞士', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '格雷罗', 'position': '后卫', 'age': 30, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 20000000},
            {'name': '埃姆雷·詹', 'position': '中场', 'age': 31, 'nationality': '德国', 'transfer_status': 'injured', 'market_value': 15000000},
            {'name': '贝林厄姆', 'position': '中场', 'age': 21, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 120000000},
            {'name': '布兰特', 'position': '中场', 'age': 27, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '桑乔', 'position': '前锋', 'age': 24, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '吉拉西', 'position': '前锋', 'age': 28, 'nationality': '塞内加尔', 'transfer_status': 'injured', 'market_value': 60000000},
            {'name': '穆科科', 'position': '前锋', 'age': 19, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 35000000}
        ],
        '拜仁慕尼黑': [
            {'name': '诺伊尔', 'position': '门将', 'age': 38, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '于帕梅卡诺', 'position': '后卫', 'age': 25, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '德里赫特', 'position': '后卫', 'age': 24, 'nationality': '荷兰', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '阿方索·戴维斯', 'position': '后卫', 'age': 24, 'nationality': '加拿大', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '帕瓦尔', 'position': '后卫', 'age': 27, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '基米希', 'position': '中场', 'age': 29, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '格雷茨卡', 'position': '中场', 'age': 28, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '穆西亚拉', 'position': '中场', 'age': 21, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 100000000},
            {'name': '萨内', 'position': '前锋', 'age': 28, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '凯恩', 'position': '前锋', 'age': 31, 'nationality': '英格兰', 'transfer_status': 'current', 'market_value': 100000000},
            {'name': '格纳布里', 'position': '前锋', 'age': 28, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 55000000}
        ]
    },
    'ligue_1': {
        '马赛': [
            {'name': '曼丹达', 'position': '门将', 'age': 38, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 2000000},
            {'name': '梅迪纳', 'position': '后卫', 'age': 25, 'nationality': '阿根廷', 'transfer_status': 'injured', 'market_value': 20000000},
            {'name': '吉戈', 'position': '后卫', 'age': 23, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 15000000},
            {'name': '康拉德', 'position': '后卫', 'age': 24, 'nationality': '法国', 'transfer_status': 'injured', 'market_value': 18000000},
            {'name': '孔多比亚', 'position': '中场', 'age': 30, 'nationality': '法国', 'transfer_status': 'injured', 'market_value': 15000000},
            {'name': '韦勒图', 'position': '中场', 'age': 30, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 12000000},
            {'name': '若尔丹', 'position': '中场', 'age': 28, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '奥巴梅扬', 'position': '前锋', 'age': 34, 'nationality': '加蓬', 'transfer_status': 'current', 'market_value': 8000000},
            {'name': '云代尔', 'position': '前锋', 'age': 27, 'nationality': '土耳其', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '巴坎布', 'position': '前锋', 'age': 32, 'nationality': '民主刚果', 'transfer_status': 'current', 'market_value': 5000000}
        ],
        '巴黎圣日耳曼': [
            {'name': '多纳鲁马', 'position': '门将', 'age': 25, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '马尔基尼奥斯', 'position': '后卫', 'age': 29, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '拉莫斯', 'position': '后卫', 'age': 37, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 5000000},
            {'name': '阿什拉夫', 'position': '后卫', 'age': 24, 'nationality': '摩洛哥', 'transfer_status': 'current', 'market_value': 55000000},
            {'name': '努诺·门德斯', 'position': '后卫', 'age': 22, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '维拉蒂', 'position': '中场', 'age': 30, 'nationality': '意大利', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '法比安·鲁伊斯', 'position': '中场', 'age': 27, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '登贝莱', 'position': '前锋', 'age': 26, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '姆巴佩', 'position': '前锋', 'age': 25, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 180000000},
            {'name': '梅西', 'position': '前锋', 'age': 37, 'nationality': '阿根廷', 'transfer_status': 'current', 'market_value': 30000000}
        ]
    },
    'la_liga': {
        '巴塞罗那': [
            {'name': '特尔施特根', 'position': '门将', 'age': 32, 'nationality': '德国', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '孔德', 'position': '后卫', 'age': 25, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '阿劳霍', 'position': '后卫', 'age': 24, 'nationality': '乌拉圭', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '巴尔德', 'position': '后卫', 'age': 21, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '坎塞洛', 'position': '后卫', 'age': 29, 'nationality': '葡萄牙', 'transfer_status': 'current', 'market_value': 40000000},
            {'name': '德容', 'position': '中场', 'age': 26, 'nationality': '荷兰', 'transfer_status': 'current', 'market_value': 70000000},
            {'name': '佩德里', 'position': '中场', 'age': 21, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 90000000},
            {'name': '加维', 'position': '中场', 'age': 19, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 75000000},
            {'name': '拉菲尼亚', 'position': '前锋', 'age': 26, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 60000000},
            {'name': '莱万多夫斯基', 'position': '前锋', 'age': 35, 'nationality': '波兰', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '费兰·托雷斯', 'position': '前锋', 'age': 23, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 50000000}
        ],
        '皇家马德里': [
            {'name': '库尔图瓦', 'position': '门将', 'age': 32, 'nationality': '比利时', 'transfer_status': 'current', 'market_value': 45000000},
            {'name': '米利唐', 'position': '后卫', 'age': 25, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 50000000},
            {'name': '阿拉巴', 'position': '后卫', 'age': 31, 'nationality': '奥地利', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '纳乔', 'position': '后卫', 'age': 33, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '卡瓦哈尔', 'position': '后卫', 'age': 31, 'nationality': '西班牙', 'transfer_status': 'current', 'market_value': 15000000},
            {'name': '巴尔韦德', 'position': '中场', 'age': 25, 'nationality': '乌拉圭', 'transfer_status': 'current', 'market_value': 80000000},
            {'name': '卡塞米罗', 'position': '中场', 'age': 32, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 30000000},
            {'name': '莫德里奇', 'position': '中场', 'age': 38, 'nationality': '克罗地亚', 'transfer_status': 'current', 'market_value': 10000000},
            {'name': '维尼修斯', 'position': '前锋', 'age': 23, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 120000000},
            {'name': '罗德里戈', 'position': '前锋', 'age': 22, 'nationality': '巴西', 'transfer_status': 'current', 'market_value': 90000000},
            {'name': '本泽马', 'position': '前锋', 'age': 36, 'nationality': '法国', 'transfer_status': 'current', 'market_value': 20000000}
        ]
    }
}

def update_player_info():
    """更新球员信息"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    for league_code, teams in TEAM_PLAYERS.items():
        print(f"\n更新 {league_code} 球员信息...")
        
        for team_name, players in teams.items():
            team_file = f"{league_code}/players/{team_name}.json"
            
            if os.path.exists(team_file):
                with open(team_file, 'r', encoding='utf-8') as f:
                    team_data = json.load(f)
                
                # 合并球员数据
                existing_players = {p['name']: p for p in team_data['players']}
                
                for player_info in players:
                    player_name = player_info['name']
                    
                    if player_name in existing_players:
                        # 更新现有球员信息
                        existing_player = existing_players[player_name]
                        existing_player.update(player_info)
                        existing_player['last_updated'] = datetime.now().isoformat()
                    else:
                        # 添加新球员
                        new_player = {
                            'name': player_info['name'],
                            'position': player_info['position'],
                            'age': player_info['age'],
                            'nationality': player_info['nationality'],
                            'transfer_status': player_info['transfer_status'],
                            'join_date': '',
                            'contract_until': '',
                            'market_value': player_info['market_value'],
                            'stats': {
                                'appearances': 0,
                                'goals': 0,
                                'assists': 0,
                                'yellow_cards': 0,
                                'red_cards': 0
                            },
                            'last_updated': datetime.now().isoformat()
                        }
                        team_data['players'].append(new_player)
                
                # 更新最后更新时间
                team_data['last_updated'] = datetime.now().isoformat()
                
                # 写回文件
                with open(team_file, 'w', encoding='utf-8') as f:
                    json.dump(team_data, f, ensure_ascii=False, indent=2)
                
                print(f"  更新 {team_name} 球员信息，共 {len(team_data['players'])} 名球员")
            else:
                print(f"  文件不存在: {team_file}")

def generate_detailed_player_report():
    """生成详细的球员报告"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    report = []
    
    for league_code, teams in TEAM_PLAYERS.items():
        league_report = {
            'league': league_code,
            'teams': []
        }
        
        for team_name in teams:
            team_file = f"{league_code}/players/{team_name}.json"
            
            if os.path.exists(team_file):
                with open(team_file, 'r', encoding='utf-8') as f:
                    team_data = json.load(f)
                
                # 统计球员信息
                total_players = len(team_data['players'])
                injured_players = [p for p in team_data['players'] if p.get('transfer_status') == 'injured']
                suspended_players = [p for p in team_data['players'] if p.get('transfer_status') == 'suspended']
                current_players = [p for p in team_data['players'] if p.get('transfer_status') == 'current']
                
                # 计算总市场价值
                total_market_value = sum(p.get('market_value', 0) for p in team_data['players'])
                
                team_info = {
                    'name': team_name,
                    'total_players': total_players,
                    'current_players': len(current_players),
                    'injured_players': len(injured_players),
                    'suspended_players': len(suspended_players),
                    'total_market_value': total_market_value,
                    'average_market_value': total_market_value / total_players if total_players > 0 else 0,
                    'players': team_data['players'],
                    'last_updated': team_data.get('last_updated', 'N/A')
                }
                
                league_report['teams'].append(team_info)
        
        report.append(league_report)
    
    # 生成详细报告文件
    report_file = 'detailed_player_report.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n生成详细球员报告: {report_file}")

def main():
    """主函数"""
    print("=" * 60)
    print("球员信息录入系统")
    print("=" * 60)
    
    # 更新球员信息
    update_player_info()
    
    # 生成详细报告
    generate_detailed_player_report()
    
    print("\n" + "=" * 60)
    print("球员信息录入完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()