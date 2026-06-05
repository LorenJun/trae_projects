#!/usr/bin/env python3
"""模块说明：为世界杯 players JSON 批量补充中文球员名字段。

Behavior:
- Keep existing `name` untouched (current files mostly use English names).
- Add `name_cn` as the Chinese player name field.
- Add/refresh `name_cn_source`.
- Best-effort sources in order:
  1) Manual overrides for unresolved names
  2) Existing Chinese name mappings from other league player files
  3) Existing Wikidata cache
  4) Live Wikidata lookup
  5) If player `name` already contains CJK characters, reuse it as `name_cn`
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent
WORLD_CUP_DIR = BASE_DIR / "world_cup" / "players"
CACHE_PATH = BASE_DIR / ".cache_wikidata_player_zh.json"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; europe_leagues/1.0)"}

SOURCE_MANUAL = "manual"
SOURCE_EXISTING = "existing_player_map"
SOURCE_CACHE = "wikidata_cache"
SOURCE_WIKIDATA = "wikidata"
SOURCE_SELF_CJK = "self_cjk"

MANUAL_NAME_CN = {
    "Azizbek Ganiev": "阿齐兹别克·加涅耶夫",
    "Jashur Jaloliddinov": "贾舒尔·贾洛利季诺夫",
    "Abdullah Abdullaev": "阿卜杜拉·阿卜杜拉耶夫",
    "Ruslanbek Yiyanov": "鲁斯兰别克·伊亚诺夫",
    "Rustamjon Ashurmatov": "鲁斯塔姆琼·阿舒尔马托夫",
    "Umaraali Rakhmonaliev": "乌马拉利·拉赫莫纳利耶夫",
    "Umarbek Eshmuradov": "乌马尔别克·埃什穆拉多夫",
    "Ahmed Qasim": "艾哈迈德·卡西姆",
    "Ali Jassim": "阿里·贾西姆",
    "Karar Nabeel": "卡拉尔·纳比勒",
    "Akam Hashem": "阿卡姆·哈希姆",
    "Maytham Jabbar": "迈萨姆·贾巴尔",
    "Kumel Saadi": "库梅尔·萨阿迪",
    "Dennis Dargahi": "丹尼斯·达尔加希",
    "Kasra Taheri": "卡斯拉·塔赫里",
    "Mehdi Taremi": "迈赫迪·塔雷米",
    "Danial Eiri": "达尼亚尔·埃里",
    "Ehsan Hajsafi": "埃赫桑·哈吉萨菲",
    "Mohammad Khalifeh": "穆罕默德·哈利法",
    "Diney Borges": "迪内·博尔热斯",
    "Ianique 'Stopira' Tavares": "伊阿尼克·斯托皮拉·塔瓦雷斯",
    "Roberto 'Pico' Lopes": "罗伯托·皮科·洛佩斯",
    "Sidny Cabral": "西德尼·卡布拉尔",
    "Aaron Wan-Bissaka": "阿龙·万-比萨卡",
    "Alex Tuanzebe": "阿莱克斯·图安泽贝",
    "Thimothy Fayulu": "蒂莫西·法尤卢",
    "Jonathan Osorio": "乔纳森·奥索里奥",
    "Junior Hoilett": "朱尼奥尔·霍伊莱特",
    "Cyle Larin": "赛尔·拉林",
    "Solomon Agbasi": "所罗门·阿格巴西",
    "Mohammed Manaai": "穆罕默德·马奈",
    "Ahmed Al Janhi": "艾哈迈德·詹希",
    "Mubarak Shannan": "穆巴拉克·沙南",
    "Youssef Abdelrisaq": "优素福·阿卜杜勒里扎克",
    "Al Hashemi Al Hussein": "阿勒哈希米·侯赛因",
    "Ayub Al Alawi": "阿尤布·阿拉维",
    "Hommam Al Amin": "胡马姆·阿明",
    "Ryan Al Ali": "瑞安·阿里",
    "Mahmoud Abunada": "马哈茂德·阿布纳达",
    "Shehab Al Lithi": "希哈卜·阿勒利西",
    "Richard Rios": "理查德·里奥斯",
    "Kevin Castano": "凯文·卡斯塔尼奥",
    "Juan Camilo Portilla": "胡安·卡米洛·波尔蒂利亚",
    "Luis Suarez": "路易斯·苏亚雷斯",
    "Deniz Gul": "德尼兹·居尔",
    'Ahmed Sayed "Zizo"': "艾哈迈德·赛义德·齐佐",
    "Haitham Hassan": "海赛姆·哈桑",
    'Mahmoud Hassan "Trezeguet"': "马哈茂德·哈桑·特雷泽盖",
    "Mohannad Lashin": "穆汉纳德·拉辛",
    "Mostafa Zico": "穆斯塔法·齐科",
    "Aqtay Abdullah": "阿克泰·阿卜杜拉",
    "Hamza Abdulkarim": "哈姆扎·阿卜杜勒卡里姆",
    "Ahmed Fatouh": "艾哈迈德·法图",
    "Hossam Abdelmagid": "胡萨姆·阿卜杜勒马吉德",
    "Mohamed Alaa": "穆罕默德·阿拉",
    "Jesús Alberto Angulo": "赫苏斯·阿尔韦托·安古洛",
    "Érick Sánchez": "埃里克·桑切斯",
    "Guillermo Martínez": "吉列尔莫·马丁内斯",
    "Kaku Romero": "卡库·罗梅罗",
    "Jorge Gutierrez": "豪尔赫·古铁雷斯",
    "Danilo Santos": "达尼洛·桑托斯",
    "Gabriel Magalhees": "加布里埃尔·马加良斯",
    "Ibanez": "伊瓦涅斯",
    "Tyrick Bodack": "泰里克·博达克",
    "Thelonious Aasgaard": "特洛尼厄斯·奥斯高",
    "Marcus Holmgren Pedersen": "马库斯·霍尔姆格伦·彼得森",
    "Lukas Cerv": "卢卡什·切尔夫",
    "Tomas Ladra": "托马什·拉德拉",
    "David Jurasek": "达维德·尤拉塞克",
    "Jindrich Stanrk": "金德日赫·斯塔涅克",
    "Reda Tagnaouti": "雷达·塔格瑙蒂",
    "Matt Garbett": "马特·加贝特",
    "Eli Just": "伊莱·贾斯特",
    "Hans Vanaken": "汉斯·范阿肯",
    "Alexis Saelemekars": "阿莱克西斯·萨勒马克尔斯",
    "Alaa Al Hajji": "阿拉·哈吉",
    "Mohammed Kanno": "穆罕默德·卡努",
    "Abdullah Al Salem": "阿卜杜拉·萨利姆",
    "Feras Al Brikan": "菲拉斯·布赖坎",
    "Jehad Thikri": "吉哈德·西克里",
    "Abdulqudus Attia": "阿卜杜勒库杜斯·阿提亚",
    "Armina Gigovic": "阿尔明·吉戈维奇",
    "Esmir Bajraktarevic": "埃斯米尔·巴伊拉克塔雷维奇",
    "Ivan Sunjic": "伊万·孙伊奇",
    "Kerim Alajbegovic": "凯里姆·阿拉伊别戈维奇",
    "Amar Dedic": "阿马尔·德迪奇",
    "Dennis Hadzikadunic": "丹尼斯·哈季卡杜尼奇",
    "Nihad Mujakic": "尼哈德·穆亚基奇",
    "Osman Hadzikic": "奥斯曼·哈季基奇",
    "Victor Nilsson Lindelof": "维克托·尼尔松·林德洛夫",
    "Eray Comert": "埃赖·科梅特",
    "Marvin Keller": "马尔温·凯勒",
    "Franck Kessie": "弗兰克·凯西",
    "Abdelmouhib Chamakh": "阿卜杜勒穆希布·沙马赫",
    "Aymen Dahmene": "艾曼·达门",
    "Sabri Ben Hassan": "萨布里·本·哈桑",
    "Mohammad Al Dawoud": "穆罕默德·达乌德",
    "Mohammad Abu Zraiq": "穆罕默德·阿布·兹赖格",
    "Odeh Fakhoury": "乌代·法胡里",
    "Ehsan Haddad": "埃赫桑·哈达德",
    "Mohammad Abualnadi": "穆罕默德·阿布纳迪",
    "Yazid Abulaila": "亚齐德·阿布莱拉",
    "Tyler Adams": "泰勒·亚当斯",
    "Sebastian Berhalter": "塞巴斯蒂安·贝尔哈尔特",
    "Dom Hyam": "多姆·海厄姆",
    "Tijjani Reijnders": "蒂贾尼·赖因德斯",
    "Joao Cancelo": "若昂·坎塞洛",
    "Rodri": "罗德里",
    "Kilian Belazzoug": "基利安·贝拉祖格",
    "Emile Dorval": "埃米尔·多瓦尔",
    "Ahmed Benbouali": "艾哈迈德·本布阿里",
    "Eom Jisung": "严志晟",
    "Kim Jingyu": "金镇圭",
    "Kim Taehyeon": "金泰贤",
    "Lee Hanbeom": "李韩范",
    "Lee Taeseok": "李太锡",
    "Park Jinseob": "朴镇燮",
    "Seol Youngwoo": "薛英佑",
    "早川 友基": "早川友基",
    "大迫 敬介": "大迫敬介",
    "铃木 彩艳": "铃木彩艳",
    "长友 佑都": "长友佑都",
    "谷口 彰悟": "谷口彰悟",
    "板仓 滉": "板仓滉",
    "渡边 刚": "渡边刚",
    "富安 健洋": "富安健洋",
    "伊藤 洋辉": "伊藤洋辉",
    "濑古 步梦": "濑古步梦",
    "菅原 由势": "菅原由势",
    "铃木 淳之介": "铃木淳之介",
    "远藤 航": "远藤航",
    "伊东 纯也": "伊东纯也",
    "镰田 大地": "镰田大地",
    "小川 航基": "小川航基",
    "前田 大然": "前田大然",
    "堂安 律": "堂安律",
    "上田 绮世": "上田绮世",
    "田中 碧": "田中碧",
    "中村 敬斗": "中村敬斗",
    "佐野 海舟": "佐野海舟",
    "久保 建英": "久保建英",
    "铃木 唯人": "铃木唯人",
    "盐贝 健人": "盐贝健人",
    "后藤 启介": "后藤启介",
}


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def norm_name(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s+", " ", text)
    return text


def load_cache() -> Dict[str, Dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def looks_like_footballer(desc: str) -> bool:
    desc_l = (desc or "").lower()
    keywords = [
        "footballer",
        "soccer player",
        "association football",
        "football player",
        "足球",
        "足球运动员",
        "足球運動員",
    ]
    return any(k in desc_l for k in keywords)


def query_wikidata_zh_label(english_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    params = {
        "action": "wbsearchentities",
        "format": "json",
        "language": "zh",
        "uselang": "zh",
        "search": english_name,
        "limit": 5,
    }
    last_error: Optional[Exception] = None
    for attempt in range(5):
        try:
            resp = requests.get(
                WIKIDATA_API,
                params=params,
                headers=HEADERS,
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
    else:
        if last_error:
            raise last_error
        return None, None, None

    results = data.get("search", [])
    if not results:
        return None, None, None

    best = None
    for r in results:
        label = r.get("label") or ""
        desc = r.get("description") or ""
        if not looks_like_footballer(desc):
            continue
        best = r
        if contains_cjk(label):
            break

    if not best:
        return None, None, None
    return best.get("label"), best.get("id"), best.get("description")


def bootstrap_existing_cn_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for league in ("premier_league", "la_liga", "bundesliga", "serie_a", "ligue_1"):
        league_dir = BASE_DIR / league / "players"
        if not league_dir.exists():
            continue
        for path in league_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for p in data.get("players", []):
                en = (p.get("english_name") or "").strip()
                zh = (p.get("name") or "").strip()
                if en and zh:
                    mapping[norm_name(en)] = zh
    return mapping


def resolve_name_cn(
    raw_name: str,
    mapping: Dict[str, str],
    cache: Dict[str, Dict[str, Any]],
    sleep_s: float,
) -> Tuple[str, str, Optional[str]]:
    if not raw_name:
        return "", "", None

    if raw_name in MANUAL_NAME_CN:
        return MANUAL_NAME_CN[raw_name], SOURCE_MANUAL, None

    if contains_cjk(raw_name):
        return raw_name.replace(" ", ""), SOURCE_SELF_CJK, None

    key = norm_name(raw_name)
    if key in mapping:
        return mapping[key], SOURCE_EXISTING, None

    cached = cache.get(raw_name) or cache.get(key)
    if cached:
        label_zh = (cached.get("label_zh") or "").strip()
        qid = (cached.get("qid") or "").strip()
        if label_zh:
            return label_zh, SOURCE_CACHE, qid or None

    label_zh, qid, desc = query_wikidata_zh_label(raw_name)
    cache[key] = {
        "label_zh": label_zh or "",
        "qid": qid or "",
        "desc": desc or "",
    }
    if sleep_s:
        time.sleep(sleep_s)
    if label_zh:
        return label_zh, SOURCE_WIKIDATA, qid
    return "", "", None


def process_file(path: Path, mapping: Dict[str, str], cache: Dict[str, Dict[str, Any]], sleep_s: float) -> Tuple[int, List[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    players = data.get("players", [])
    updated = 0
    missing: List[str] = []

    for p in players:
        raw_name = (p.get("english_name") or p.get("name") or "").strip()
        if not raw_name:
            continue

        name_cn, source, qid = resolve_name_cn(raw_name, mapping, cache, sleep_s)
        if not name_cn:
            missing.append(raw_name)
            continue

        p["name_cn"] = name_cn
        p["name_cn_source"] = source
        if qid and not p.get("wikidata_id"):
            p["wikidata_id"] = qid
        updated += 1

    data["players"] = players
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated, missing


def main() -> int:
    if not WORLD_CUP_DIR.exists():
        raise SystemExit(f"目录不存在: {WORLD_CUP_DIR}")

    mapping = bootstrap_existing_cn_map()
    cache = load_cache()
    total_updated = 0
    total_missing = 0
    sleep_s = 0.05

    for path in sorted(WORLD_CUP_DIR.glob("*.json")):
        updated, missing = process_file(path, mapping, cache, sleep_s)
        total_updated += updated
        total_missing += len(missing)
        print(f"{path.stem}: name_cn_updated={updated}, missing={len(missing)}")
        if missing:
            print("  missing_sample:", ", ".join(missing[:6]))

    save_cache(cache)
    print(f"TOTAL updated={total_updated}, missing={total_missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
