"""模块说明：将已同步的比赛结果回填到 prediction_archive.json 中的 actual_result 和 actual_score 字段。"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def load_json_file(path: Path) -> Dict[str, Any]:
    """加载 JSON 文件。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_json_file(path: Path, data: Dict[str, Any]) -> None:
    """保存 JSON 文件。"""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def sync_archive_actual_results(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    从 result_sync_registry.json 中读取已完成的比赛结果，
    并同步到 prediction_archive.json 中的 actual_result 和 actual_score 字段。
    """
    if base_dir is None:
        base_dir = str(Path(__file__).parent.parent)
    
    base_path = Path(base_dir)
    
    # 文件路径
    archive_path = base_path / ".okooo-scraper" / "runtime" / "prediction_archive.json"
    registry_path = base_path / ".okooo-scraper" / "runtime" / "result_sync_registry.json"
    
    # 加载文件
    archive = load_json_file(archive_path)
    registry = load_json_file(registry_path)
    
    if not archive:
        return {"status": "error", "message": "prediction_archive.json 为空或不存在"}
    
    if not registry:
        return {"status": "error", "message": "result_sync_registry.json 为空或不存在"}
    
    updated_count = 0
    skipped_count = 0
    
    # 遍历 registry 中已完成的比赛
    for match_id, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        
        if entry.get("status") != "completed":
            continue
        
        actual_score = entry.get("actual_score")
        actual_winner = entry.get("actual_winner")
        
        if not actual_score or not actual_winner:
            continue
        
        # 在 archive 中查找匹配的记录
        for archive_key, archive_entry in archive.items():
            if not isinstance(archive_entry, dict):
                continue
            
            # 匹配条件：match_id、teams_match_id 或球队名称+日期
            archive_match_id = archive_entry.get("match_id", "")
            archive_teams_id = archive_entry.get("teams_match_id", "")
            archive_date = archive_entry.get("match_date", "")
            archive_home = archive_entry.get("home_team", "")
            archive_away = archive_entry.get("away_team", "")
            
            entry_teams_id = entry.get("teams_match_id", "")
            entry_date = entry.get("match_date", "")
            entry_home = entry.get("home_team", "")
            entry_away = entry.get("away_team", "")
            
            # 检查是否匹配
            is_match = False
            
            # 通过 match_id 匹配
            if match_id and (match_id == archive_match_id or match_id == archive_teams_id):
                is_match = True
            
            # 通过 teams_match_id 匹配
            if entry_teams_id and (entry_teams_id == archive_match_id or entry_teams_id == archive_teams_id):
                is_match = True
            
            # 通过球队名称+日期匹配
            if (entry_home and entry_away and entry_date and 
                entry_home == archive_home and entry_away == archive_away and entry_date == archive_date):
                is_match = True
            
            if is_match:
                # 检查是否已更新
                current_result = archive_entry.get("actual_result", "")
                current_score = archive_entry.get("actual_score", "")
                
                if current_result and current_score:
                    skipped_count += 1
                    continue
                
                # 更新 actual_result 和 actual_score
                archive_entry["actual_result"] = actual_winner
                archive_entry["actual_score"] = actual_score
                
                updated_count += 1
                break
    
    # 保存更新后的 archive
    if updated_count > 0:
        save_json_file(archive_path, archive)
    
    return {
        "status": "success",
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "archive_path": str(archive_path),
        "registry_path": str(registry_path),
    }


if __name__ == "__main__":
    base_dir = sys.argv[1] if len(sys.argv) > 1 else None
    result = sync_archive_actual_results(base_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
