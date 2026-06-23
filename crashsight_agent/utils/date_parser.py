"""日期解析工具 — 将中文日期描述转为 YYYYMMDD 格式"""
import re
from datetime import datetime, timedelta


def parse_date_range(text: str) -> tuple:
    """
    解析日期范围描述，返回 (start_date, end_date) 格式 YYYYMMDD
    
    支持:
      "昨天" → (昨天, 昨天)
      "今天" → (今天, 今天)
      "最近一周" / "这周" → (7天前, 今天)
      "上周" → (上周一, 上周日)
      "最近30天" → (30天前, 今天)
      "6月1号到今天" → (20260601, 今天)
      "2026-06-17~2026-06-23" → (20260617, 20260623)
      "2026/06/17~2026/06/23" → (20260617, 20260623)
    """
    today = datetime.now()
    text = text.strip()

    if '昨天' in text:
        d = today - timedelta(days=1)
        return d.strftime('%Y%m%d'), d.strftime('%Y%m%d')

    if '今天' in text:
        return today.strftime('%Y%m%d'), today.strftime('%Y%m%d')

    if '最近一周' in text or '这周' in text or '近7天' in text:
        start = today - timedelta(days=6)
        return start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

    if '上周' in text:
        # 上周一到上周日
        days_since_monday = today.weekday()
        last_monday = today - timedelta(days=days_since_monday + 7)
        last_sunday = last_monday + timedelta(days=6)
        return last_monday.strftime('%Y%m%d'), last_sunday.strftime('%Y%m%d')

    if '最近30天' in text or '近30天' in text or '最近一个月' in text:
        start = today - timedelta(days=29)
        return start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

    if '最近3天' in text or '近3天' in text:
        start = today - timedelta(days=2)
        return start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

    # ISO日期范围: "2026-06-17~2026-06-23" 或 "2026/06/17~2026/06/23"
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*[~\-至到]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        start_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        end_date = f"{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}"
        return start_date, end_date

    # 单个ISO日期: "2026-06-17"
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        d = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        return d, d

    return None, None
