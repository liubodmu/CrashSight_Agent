#!/usr/bin/env python3
"""评估流水线 — 自动跑测试集，输出各维度指标

用法:
  python -m tests.eval.runner route          # 评估意图路由
  python -m tests.eval.runner history        # 评估历史问题判定
  python -m tests.eval.runner e2e            # 评估端到端
  python -m tests.eval.runner all            # 全部评估
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from tests.eval.metrics import precision_recall_f1, route_accuracy, tool_reliability


EVAL_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def eval_route():
    """评估 Route 节点（意图识别 + 参数解析）"""
    golden_file = os.path.join(EVAL_DATA_DIR, 'route_golden.jsonl')
    if not os.path.exists(golden_file):
        print(f'[Route Eval] 测试集不存在: {golden_file}')
        print(f'  请先创建测试数据，格式:')
        print(f'  {{"query": "安卓体验服3.7昨天崩溃", "intent": "crash_report", "project_id": "android_exp", "version": "3.7.*", "start_date": "20260621", "end_date": "20260621"}}')
        return

    from crashsight_agent.orchestration.nodes.route import route_node

    cases = [json.loads(line) for line in open(golden_file, 'r', encoding='utf-8') if line.strip()]
    print(f'[Route Eval] 加载 {len(cases)} 条测试用例\n')

    predictions = []
    ground_truths = []
    layer_hits = {'layer1': 0, 'layer2': 0, 'layer3': 0}

    for i, case in enumerate(cases):
        query = case['query']
        expected = {k: case.get(k) for k in ['intent', 'project_id', 'version', 'start_date', 'end_date']}

        # 运行 Route
        state = {'query': query, 'session_history': []}
        result = route_node(state)

        actual = {k: result.get(k) for k in ['intent', 'project_id', 'version', 'start_date', 'end_date']}
        predictions.append(actual)
        ground_truths.append(expected)

        # 判断命中层
        # （通过打印日志判断，简化处理）
        match = '✓' if actual == expected else '✗'
        print(f'  [{i+1:2d}] {match} query="{query[:30]}" → intent={actual["intent"]}, project={actual["project_id"]}')

    # 计算指标
    print(f'\n{"="*60}')
    print(f'Route 评估结果')
    print(f'{"="*60}')
    stats = route_accuracy(predictions, ground_truths)
    print(f'  意图准确率: {stats["intent_accuracy"]*100:.1f}% ({stats["intent_correct"]}/{stats["total"]})')
    print(f'  全参数准确率: {stats["full_accuracy"]*100:.1f}% ({stats["params_correct"]}/{stats["total"]})')
    print(f'  各字段准确率:')
    for field, acc in stats['field_accuracy'].items():
        print(f'    {field}: {acc*100:.1f}%')
    print()
    return stats


def eval_history():
    """评估历史问题判定（LLM vs 原算法）"""
    golden_file = os.path.join(EVAL_DATA_DIR, 'history_golden.jsonl')
    if not os.path.exists(golden_file):
        print(f'[History Eval] 测试集不存在: {golden_file}')
        print(f'  请先创建测试数据，格式:')
        print(f'  {{"stack_a": "...", "stack_b": "...", "exc_a": "SIGSEGV", "exc_b": "SIGSEGV", "label": true, "key_frame": "FuncName"}}')
        print(f'  label=true 表示同一 Bug，label=false 表示不同 Bug')
        return

    from crashsight_agent.tools.history_tool import _llm_compare_stacks

    cases = [json.loads(line) for line in open(golden_file, 'r', encoding='utf-8') if line.strip()]
    print(f'[History Eval] 加载 {len(cases)} 条测试用例\n')

    llm_predictions = []
    ground_truths = []

    for i, case in enumerate(cases):
        label = case['label']  # ground truth
        ground_truths.append(label)

        # LLM 判定
        start = time.time()
        llm_result = _llm_compare_stacks(
            case['stack_a'], case['stack_b'],
            case.get('exc_a', ''), case.get('exc_b', ''),
            case.get('key_frame', '')
        )
        duration = (time.time() - start) * 1000
        llm_predictions.append(llm_result)

        match = '✓' if llm_result == label else '✗'
        print(f'  [{i+1:2d}] {match} LLM={llm_result} GT={label} ({duration:.0f}ms) kf={case.get("key_frame", "")[:30]}')

        time.sleep(1)  # 避免限流

    # 计算 LLM 的 F1
    print(f'\n{"="*60}')
    print(f'历史问题判定评估结果')
    print(f'{"="*60}')

    llm_stats = precision_recall_f1(llm_predictions, ground_truths)
    print(f'\n  LLM 判定:')
    print(f'    Precision: {llm_stats["precision"]*100:.1f}%')
    print(f'    Recall:    {llm_stats["recall"]*100:.1f}%')
    print(f'    F1:        {llm_stats["f1"]*100:.1f}%')
    print(f'    Accuracy:  {llm_stats["accuracy"]*100:.1f}%')
    print(f'    TP={llm_stats["tp"]} FP={llm_stats["fp"]} FN={llm_stats["fn"]} TN={llm_stats["tn"]}')

    # 错误 case 分析
    errors = []
    for i, (pred, gt) in enumerate(zip(llm_predictions, ground_truths)):
        if pred != gt:
            errors.append({'index': i+1, 'predicted': pred, 'ground_truth': gt, 'key_frame': cases[i].get('key_frame', '')})

    if errors:
        print(f'\n  错误 case ({len(errors)} 条):')
        for e in errors:
            label = 'FP(误判为历史)' if e['predicted'] else 'FN(漏判为新问题)'
            print(f'    Case{e["index"]}: {label} kf={e["key_frame"][:40]}')

    print()
    return llm_stats


def eval_e2e():
    """端到端评估（完整查询 → 报告）"""
    golden_file = os.path.join(EVAL_DATA_DIR, 'e2e_golden.jsonl')
    if not os.path.exists(golden_file):
        print(f'[E2E Eval] 测试集不存在: {golden_file}')
        print(f'  请先创建测试数据，格式:')
        print(f'  {{"query": "安卓体验服3.7昨天崩溃", "expect_intent": "crash_report", "expect_has_trend": true, "expect_has_issues": true}}')
        return

    from crashsight_agent.orchestration.agent import CrashSightAgent

    cases = [json.loads(line) for line in open(golden_file, 'r', encoding='utf-8') if line.strip()]
    print(f'[E2E Eval] 加载 {len(cases)} 条测试用例\n')

    results = []
    for i, case in enumerate(cases):
        query = case['query']
        print(f'  [{i+1:2d}] 执行: "{query[:40]}"')

        agent = CrashSightAgent()
        start = time.time()
        try:
            answer = agent.chat(query)
            duration = time.time() - start
            success = bool(answer and len(answer) > 50)

            # 检查报告完整性
            has_trend = '崩溃率' in answer or 'minRate' in answer
            has_issues = 'Issue' in answer or '异常名' in answer or '#1' in answer
            has_history = '历史问题' in answer or '新问题' in answer

            results.append({
                'query': query,
                'success': success,
                'duration_s': round(duration, 1),
                'has_trend': has_trend,
                'has_issues': has_issues,
                'has_history': has_history,
                'answer_length': len(answer),
            })
            print(f'       {"✓" if success else "✗"} {duration:.1f}s len={len(answer)} trend={has_trend} issues={has_issues}')
        except Exception as e:
            results.append({'query': query, 'success': False, 'error': str(e)})
            print(f'       ✗ Error: {str(e)[:60]}')

        time.sleep(2)

    # 汇总
    print(f'\n{"="*60}')
    print(f'端到端评估结果')
    print(f'{"="*60}')
    success_count = sum(1 for r in results if r.get('success'))
    durations = [r['duration_s'] for r in results if r.get('duration_s')]
    print(f'  成功率: {success_count}/{len(results)} ({success_count/len(results)*100:.0f}%)')
    if durations:
        print(f'  平均耗时: {sum(durations)/len(durations):.1f}s')
        print(f'  最大耗时: {max(durations):.1f}s')
    trend_pct = sum(1 for r in results if r.get('has_trend')) / len(results) * 100
    issues_pct = sum(1 for r in results if r.get('has_issues')) / len(results) * 100
    history_pct = sum(1 for r in results if r.get('has_history')) / len(results) * 100
    print(f'  报告完整性:')
    print(f'    含趋势数据: {trend_pct:.0f}%')
    print(f'    含问题列表: {issues_pct:.0f}%')
    print(f'    含历史判定: {history_pct:.0f}%')
    print()
    return results


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if cmd == 'route':
        eval_route()
    elif cmd == 'history':
        eval_history()
    elif cmd == 'e2e':
        eval_e2e()
    elif cmd == 'all':
        print('=' * 60)
        print(' CrashSight Agent 全面评估')
        print('=' * 60)
        print()
        eval_route()
        eval_history()
        eval_e2e()
    else:
        print(f'未知命令: {cmd}')
        print('用法: python -m tests.eval.runner [route|history|e2e|all]')
