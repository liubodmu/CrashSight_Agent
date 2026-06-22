"""请求守卫 — 拦截不合理的查询参数，防止 API 滥用/超时

拦截规则:
1. 时间范围过大（>90天）→ 建议缩小
2. 全版本 + 长时间 → 数据量爆炸，拦截
3. 多版本同时查 → 限制最多 3 个版本
4. 单天查全版本 → 允许（数据量不大）
5. 无时间范围 → 拦截
"""
from datetime import datetime, timedelta


class QueryGuard:
    """查询守卫 — 在工具执行前检查参数合理性"""

    # 配置
    MAX_DATE_RANGE_DAYS = 90        # 最大时间跨度
    MAX_VERSIONS = 3                # 同时查询最大版本数
    WARN_DATE_RANGE_DAYS = 30       # 超过30天给警告
    MAX_FULL_VERSION_DAYS = 7       # 全版本(-1)最多查7天

    def check(self, project_id: str, version: str, start_date: str, end_date: str) -> dict:
        """
        检查查询参数是否合理
        
        返回:
            {'allowed': True}  → 放行
            {'allowed': False, 'reason': '...', 'suggestion': '...'}  → 拦截
            {'allowed': True, 'warning': '...'}  → 放行但警告
        """
        # 1. 基本参数检查
        if not start_date or not end_date:
            return {
                'allowed': False,
                'reason': '缺少时间范围',
                'suggestion': '请指定时间范围，如"昨天"、"最近一周"',
            }

        # 2. 计算时间跨度
        try:
            start = datetime.strptime(start_date, '%Y%m%d')
            end = datetime.strptime(end_date, '%Y%m%d')
            days = (end - start).days + 1
        except ValueError:
            return {
                'allowed': False,
                'reason': f'日期格式错误: {start_date} ~ {end_date}',
                'suggestion': '日期格式应为 YYYYMMDD',
            }

        # 3. 未来日期检查
        today = datetime.now()
        if start > today:
            return {
                'allowed': False,
                'reason': '开始日期在未来',
                'suggestion': f'当前日期是 {today.strftime("%Y%m%d")}，请选择今天或之前的日期',
            }

        # 4. 时间范围过大
        if days > self.MAX_DATE_RANGE_DAYS:
            return {
                'allowed': False,
                'reason': f'时间范围过大 ({days}天)，超过上限 {self.MAX_DATE_RANGE_DAYS} 天',
                'suggestion': f'建议缩小到最近 30 天以内，或分多次查询',
            }

        # 5. 版本数检查
        is_full_version = (version == '-1' or not version)
        version_count = len(version.split(',')) if version and version != '-1' else 0

        if version_count > self.MAX_VERSIONS:
            return {
                'allowed': False,
                'reason': f'同时查询 {version_count} 个版本，超过上限 {self.MAX_VERSIONS} 个',
                'suggestion': f'建议一次最多查 {self.MAX_VERSIONS} 个版本，或用通配符如 "3.7.*"',
            }

        # 6. 全版本 + 长时间 → 数据量太大
        if is_full_version and days > self.MAX_FULL_VERSION_DAYS:
            return {
                'allowed': False,
                'reason': f'全版本查询 {days} 天数据量过大（全版本最多支持 {self.MAX_FULL_VERSION_DAYS} 天）',
                'suggestion': f'请指定具体版本号（如 "3.7.*"），或缩小时间范围到 {self.MAX_FULL_VERSION_DAYS} 天以内',
            }

        # 7. 警告：超过 30 天
        if days > self.WARN_DATE_RANGE_DAYS:
            return {
                'allowed': True,
                'warning': f'时间范围较大 ({days}天)，查询可能需要较长时间',
            }

        # 8. 通过
        return {'allowed': True}


# 全局单例
_guard = QueryGuard()


def check_query_safety(project_id: str, version: str, start_date: str, end_date: str) -> dict:
    """快捷调用"""
    return _guard.check(project_id, version, start_date, end_date)
