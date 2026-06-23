#!/usr/bin/env python3
"""收集历史问题堆栈对 — 从真实 API 抓取体验服+正式服堆栈，生成待标注文件

用法:
    python scripts/collect_history_pairs.py

输出:
    tests/eval/data/history_pairs_to_label.jsonl
    
每行格式:
    {
        "issue_id": "xxx",
        "exc_a": "OnDumpDependencies",
        "exc_b": "OnDumpDependencies", 
        "key_frame": "FuncName",
        "stack_a_head": "体验服堆栈前20行...",
        "stack_b_head": "正式服堆栈前20行...",
        "jaccard": 0.45,
        "label": null   ← 你需要标注为 true/false
    }

标注完成后，运行:
    python scripts/convert_labeled_pairs.py
将标注结果转为 tests/eval/data/history_golden.jsonl
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from crashsight_agent.tools import execute_tool
from crashsight_agent.tools.keyframe import extract_key_frame
from crashsight_agent.tools.history_tool import _multi_search_candidates, _get_candidate_stack
from crashsight_agent.tools.ensemble import _jaccard_match
from crashsight_agent.config import PROJECTS


OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tests', 'eval', 'data', 'history_pairs_to_label.jsonl')


def collect_pairs(project_id='android_exp', version='3.7.*', days_back=7):
    """收集堆栈对"""
    from datetime import datetime, timedelta
    
    today = datetime.now()
    start_date = (today - timedelta(days=days_back)).strftime('%Y%m%d')
    end_date = today.strftime('%Y%m%d')

    project = PROJECTS[project_id]
    target_project_id = project.get('prod_counterpart', '')
    if not target_project_id:
        print(f'项目 {project_id} 无对应正式服')
        return
    target_project = PROJECTS[target_project_id]

    print(f'收集堆栈对: {project["name"]} → {target_project["name"]}')
    print(f'时间范围: {start_date} ~ {end_date}, 版本: {version}')
    print()

    # Step 1: 拿 TOP 问题
    print('[1/4] 获取 TOP10 问题...')
    issues_result = execute_tool('get_top_issues', {
        'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date, 'top_n': 10,
    })
    if not issues_result.get('success') or not issues_result.get('data'):
        print(f'获取 TOP 问题失败: {issues_result.get("error", "")}')
        return

    issues = issues_result['data']
    print(f'  获取到 {len(issues)} 个问题')

    pairs = []

    for i, issue in enumerate(issues):
        issue_id = issue.get('issueId', '')
        exc_name = issue.get('exceptionName', '')
        print(f'\n[2/4] [{i+1}/{len(issues)}] {exc_name[:30]} ({issue_id[:8]})')

        # Step 2: 拿体验服堆栈
        stack_result = execute_tool('get_issue_full_stack', {
            'project_id': project_id, 'issue_id': issue_id,
        })
        if not stack_result.get('success') or not stack_result.get('data'):
            print(f'  堆栈为空，跳过')
            continue

        exp_stack = stack_result['data'].get('callStackFull', '') or stack_result['data'].get('callStack', '')
        if not exp_stack or len(exp_stack) < 50:
            print(f'  堆栈太短({len(exp_stack)}字符)，跳过')
            continue

        # Step 3: 提取关键帧
        kf_ret = extract_key_frame(exp_stack, exp_stack, exception_name=exc_name)
        if not kf_ret:
            print(f'  无法提取关键帧，跳过')
            continue

        key_frame, is_weak, feature_frames = kf_ret
        print(f'  关键帧: {key_frame[:40]}')

        # Step 4: 搜正式服候选
        target_app_id = target_project['appId']
        target_pid = target_project['pid']
        candidates = _multi_search_candidates(target_app_id, target_pid, feature_frames, limit_per_kw=5)

        # 排除自己
        candidates = [c for c in candidates if not c.get('issueId', '').startswith(issue_id[:8])]

        if not candidates:
            print(f'  正式服未搜到候选')
            continue

        print(f'  搜到 {len(candidates)} 个候选')

        # 对前3个候选拉堆栈
        for j, cand in enumerate(candidates[:3]):
            cand_id = cand.get('issueId', '')
            cand_exc = cand.get('exceptionName', '')

            cand_stack = _get_candidate_stack(cand, target_app_id, target_pid)
            if not cand_stack:
                continue

            # 计算 Jaccard
            _, jaccard_score = _jaccard_match(exp_stack, cand_stack, threshold=0.0)

            pair = {
                'issue_id': issue_id,
                'candidate_id': cand_id,
                'exc_a': exc_name,
                'exc_b': cand_exc,
                'key_frame': key_frame,
                'stack_a': '\n'.join(exp_stack.split('\n')[:25]),
                'stack_b': '\n'.join(cand_stack.split('\n')[:25]),
                'jaccard': round(jaccard_score, 3),
                'label': None,  # ← 需要人工标注
            }
            pairs.append(pair)
            print(f'    候选{j+1}: {cand_id[:8]} ({cand_exc[:20]}) Jaccard={jaccard_score:.2f}')

            time.sleep(0.5)

        time.sleep(1)

    # 保存
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + '\n')

    print(f'\n{"="*60}')
    print(f'收集完成！共 {len(pairs)} 对堆栈')
    print(f'输出文件: {OUTPUT_FILE}')
    print(f'\n请打开文件，将每行的 "label": null 改为:')
    print(f'  "label": true   ← 同一 Bug')
    print(f'  "label": false  ← 不同 Bug')
    print(f'\n标注完成后运行: python scripts/convert_labeled_pairs.py')


if __name__ == '__main__':
    collect_pairs()
