#!/bin/bash
"""
球员状态更新cron作业脚本
用于设置每周自动更新球员状态
"""

# 显示当前目录
echo "当前目录: $(pwd)"

# 创建cron作业配置
CRON_JOB="0 9 * * 1 cd /Users/lin/trae_projects/europe_leagues && python3 player_status_updater.py >> player_status_cron.log 2>&1"

# 检查是否已存在cron作业
EXISTING_JOB=$(crontab -l 2>/dev/null | grep -F "player_status_updater.py")

if [ -z "$EXISTING_JOB" ]; then
    # 添加新的cron作业
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "已添加每周一上午9点执行的球员状态更新作业"
else
    # 更新现有的cron作业
    crontab -l 2>/dev/null | sed '/player_status_updater.py/d' | (cat; echo "$CRON_JOB") | crontab -
    echo "已更新现有的球员状态更新作业"
fi

# 显示当前的cron作业
echo "\n当前的cron作业:"
crontab -l

# 创建日志目录
mkdir -p logs

# 显示成功信息
echo "\n球员状态定期更新配置完成！"
echo "每周一上午9点将自动更新球员状态并生成报告"