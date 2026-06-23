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
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from tests.eval.metrics import precision_recall_f1, route_accuracy, tool_reliability


EVAL_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def _resolve_auto_date(value: str) -> str:
    """将 golden 数据中的 AUTO_xxx 占位符解析为当日实际值"""
    if not value or not value.startswith('AUTO_'):
        return value
    today = datetime.now()
    mapping = {
        'AUTO_YESTERDAY': (today - timedelta(days=1)).strftime('%Y%m%d'),
        'AUTO_TODAY': today.strftime('%Y%m%d'),
        'AUTO_7DAYS': (today - timedelta(days=6)).strftime('%Y%m%d'),
        'AUTO_3DAYS': (today - timedelta(days=2)).strftime('%Y%m%d'),
        'AUTO_30DAYS': (today - timedelta(days=29)).strftime('%Y%m%d'),
        'AUTO_LAST_WEEK_MON': (today - timedelta(days=today.weekday() + 7)).strftime('%Y%m%d'),
        'AUTO_LAST_WEEK_SUN': (today - timedelta(days=today.weekday() + 1)).strftime('%Y%m%d'),
    }
    # AUTO_JUN1 等特殊值
    if value == 'AUTO_JUN1':
        return f'{today.year}0601'
    return mapping.get(value, value)


def eval_route():
    """评估 Route 节点（意图识别 + 参数解析）"""
    golden_file = os.path.join(EVAL_DATA_DIR, 'route_golden.jsonl')
    if not os.path.exists(golden_file):
        print(f'[Route Eval] 测试集不存在: {golden_file}')
        return

    from crashsight_agent.orchestration.nodes.route import route_node

    cases = [json.loads(line) for line in open(golden_file, 'r', encoding='utf-8') if line.strip()]
    print(f'[Route Eval] 加载 {len(cases)} 条测试用例\n')

    predictions = []
    ground_truths = []
    intent_errors = []

    for i, case in enumerate(cases):
        query = case['query']
        # 解析动态日期占位符
        expected = {
            'intent': case.get('intent'),
            'project_id': case.get('project_id'),
            'version': case.get('version'),
            'start_date': _resolve_auto_date(case.get('start_date')),
            'end_date': _resolve_auto_date(case.get('end_date')),
        }

        # 运行 Route
        state = {'query': query, 'session_history': []}
        result = route_node(state)

        actual = {k: result.get(k) for k in ['intent', 'project_id', 'version', 'start_date', 'end_date']}
        predictions.append(actual)
        ground_truths.append(expected)

        # 意图是否正确
        intent_ok = actual['intent'] == expected['intent']
        # 参数是否正确（忽略 None vs None）
        params_ok = all(
            actual.get(k) == expected.get(k)
            for k in ['project_id', 'version', 'start_date', 'end_date']
            if expected.get(k) is not None  # 只检查 golden 中有明确期望的字段
        )
        match = '✓' if intent_ok and params_ok else '✗'
        print(f'  [{i+1:2d}] {match} "{query[:35]}" → {actual["intent"]}|{actual["project_id"]}|{actual["version"]}|{actual["start_date"]}')

        if not intent_ok:
            intent_errors.append(f'  Case{i+1}: "{query[:40]}" expect={expected["intent"]} got={actual["intent"]}')

    # 计算指标
    print(f'\n{"="*60}')
    print(f'  Route 评估结果 ({len(cases)} 条)')
    print(f'{"="*60}')
    stats = route_accuracy(predictions, ground_truths)
    print(f'  意图准确率:   {stats["intent_accuracy"]*100:.1f}% ({stats["intent_correct"]}/{stats["total"]})')
    print(f'  全参数准确率: {stats["full_accuracy"]*100:.1f}% ({stats["params_correct"]}/{stats["total"]})')
    print(f'  各字段准确率:')
    for field, acc in stats['field_accuracy'].items():
        print(f'    {field}: {acc*100:.1f}%')

    if intent_errors:
        print(f'\n  意图错误 ({len(intent_errors)} 条):')
        for e in intent_errors[:10]:
            print(f'    {e}')
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


def eval_robustness():
    """鲁棒性评估 — 异常输入不崩溃"""
    from crashsight_agent.orchestration.nodes.route import route_node

    abnormal_inputs = [
        '',                                    # 空字符串
        '   ',                                 # 纯空格
        'a' * 5000,                            # 超长输入
        '!@#$%^&*()',                          # 纯特殊字符
        '安卓' * 500,                          # 超长中文
        'SELECT * FROM users; DROP TABLE;',    # SQL 注入
        '<script>alert(1)</script>',           # XSS
        '{"intent":"crash_report"}',           # JSON 注入
        'None',                                # Python 关键字
        '3.7.365 昨天',                        # 缺项目
        '安卓体验服',                           # 只有项目
        '\\n\\n\\n',                           # 转义字符
        '崩溃' * 100 + '趋势' * 100,           # 多意图混合
        '2026-13-45~2026-99-99 的崩溃',        # 无效日期
        'android_exp 版本 -1 时间 99999999',   # 边界值
    ]

    print(f'[Robustness] 测试 {len(abnormal_inputs)} 条异常输入\n')
    crashed = 0
    results = []

    for i, inp in enumerate(abnormal_inputs):
        display = repr(inp[:40])
        try:
            state = {'query': inp, 'session_history': []}
            result = route_node(state)
            intent = result.get('intent', 'unknown')
            results.append({'input': display, 'success': True, 'intent': intent})
            print(f'  [{i+1:2d}] OK  {display} -> {intent}')
        except Exception as e:
            crashed += 1
            results.append({'input': display, 'success': False, 'error': str(e)[:60]})
            print(f'  [{i+1:2d}] CRASH  {display} -> {str(e)[:50]}')

    print(f'\n{"="*60}')
    print(f'  鲁棒性评估结果')
    print(f'{"="*60}')
    print(f'  测试数: {len(abnormal_inputs)}')
    print(f'  通过数: {len(abnormal_inputs) - crashed}')
    print(f'  崩溃数: {crashed}')
    print(f'  鲁棒率: {(len(abnormal_inputs) - crashed) / len(abnormal_inputs) * 100:.0f}%')
    print()
    return {'total': len(abnormal_inputs), 'passed': len(abnormal_inputs) - crashed, 'crashed': crashed}


def eval_tool_reliability():
    """工具可靠性评估 — 从日志中分析 API 调用成功率"""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'logs')
    if not os.path.exists(log_dir):
        print('[Tool Reliability] 日志目录不存在，请先运行 Agent 产生日志')
        return

    # 读取所有日志
    tool_calls = []
    for f in sorted(os.listdir(log_dir)):
        if f.endswith('.jsonl'):
            filepath = os.path.join(log_dir, f)
            for line in open(filepath, 'r', encoding='utf-8'):
                try:
                    record = json.loads(line)
                    if record.get('category') == 'tool' and record.get('event') == 'tool_call':
                        tool_calls.append(record.get('data', {}))
                except:
                    continue

    if not tool_calls:
        print('[Tool Reliability] 无工具调用日志')
        return

    stats = tool_reliability(tool_calls)

    print(f'\n{"="*60}')
    print(f'  工具可靠性评估结果 ({len(tool_calls)} 次调用)')
    print(f'{"="*60}')
    print(f'  {"工具":<25} {"成功率":>8} {"调用数":>6} {"平均耗时":>10} {"P95耗时":>10}')
    print(f'  {"-"*25} {"-"*8} {"-"*6} {"-"*10} {"-"*10}')
    for tool, s in sorted(stats.items()):
        print(f'  {tool:<25} {s["success_rate"]*100:>6.1f}% {s["total_calls"]:>6} {s["avg_duration_ms"]:>8}ms {s["p95_duration_ms"]:>8}ms')
    print()
    return stats


def eval_performance():
    """性能评估 — 从日志中统计端到端响应时间"""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'logs')
    if not os.path.exists(log_dir):
        print('[Performance] 日志目录不存在')
        return

    sessions = []
    for f in sorted(os.listdir(log_dir)):
        if f.endswith('.jsonl'):
            filepath = os.path.join(log_dir, f)
            for line in open(filepath, 'r', encoding='utf-8'):
                try:
                    record = json.loads(line)
                    if record.get('event') == 'query_end' and record.get('duration_ms'):
                        sessions.append(record)
                except:
                    continue

    if not sessions:
        print('[Performance] 无会话记录')
        return

    durations = [s['duration_ms'] for s in sessions if s.get('duration_ms')]
    success_count = sum(1 for s in sessions if s.get('data', {}).get('success'))

    print(f'\n{"="*60}')
    print(f'  性能评估结果 ({len(sessions)} 次请求)')
    print(f'{"="*60}')
    if durations:
        durations.sort()
        print(f'  平均响应时间: {sum(durations)/len(durations)/1000:.1f}s')
        print(f'  中位数:       {durations[len(durations)//2]/1000:.1f}s')
        print(f'  P95:          {durations[int(len(durations)*0.95)]/1000:.1f}s' if len(durations) > 1 else '')
        print(f'  最快:         {min(durations)/1000:.1f}s')
        print(f'  最慢:         {max(durations)/1000:.1f}s')
    print(f'  成功率:       {success_count}/{len(sessions)} ({success_count/len(sessions)*100:.0f}%)')
    print()
    return {'count': len(sessions), 'avg_ms': sum(durations)//len(durations) if durations else 0, 'durations': durations}


def eval_cost():
    """成本评估 — 从日志中统计 LLM 调用量"""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'logs')
    if not os.path.exists(log_dir):
        print('[Cost] 日志目录不存在')
        return

    llm_calls = []
    route_layers = {'layer1': 0, 'layer2': 0, 'layer3': 0}

    for f in sorted(os.listdir(log_dir)):
        if f.endswith('.jsonl'):
            filepath = os.path.join(log_dir, f)
            for line in open(filepath, 'r', encoding='utf-8'):
                try:
                    record = json.loads(line)
                    if record.get('category') == 'llm':
                        llm_calls.append(record.get('data', {}))
                    if record.get('event') == 'intent_resolved':
                        layer = record.get('data', {}).get('layer', '')
                        if layer in route_layers:
                            route_layers[layer] += 1
                except:
                    continue

    total_routes = sum(route_layers.values())

    print(f'\n{"="*60}')
    print(f'  成本评估')
    print(f'{"="*60}')
    print(f'  LLM 总调用次数: {len(llm_calls)}')
    if llm_calls:
        total_input = sum(c.get('input_tokens', 0) for c in llm_calls)
        total_output = sum(c.get('output_tokens', 0) for c in llm_calls)
        print(f'  总 Input Tokens:  {total_input:,}')
        print(f'  总 Output Tokens: {total_output:,}')
        by_purpose = {}
        for c in llm_calls:
            p = c.get('purpose', 'unknown')
            by_purpose[p] = by_purpose.get(p, 0) + 1
        print(f'  按用途分布:')
        for p, count in sorted(by_purpose.items(), key=lambda x: -x[1]):
            print(f'    {p}: {count} 次')

    if total_routes > 0:
        print(f'\n  路由层命中分布 ({total_routes} 次):')
        for layer, count in route_layers.items():
            print(f'    {layer}: {count} ({count/total_routes*100:.0f}%)')
        llm_route_rate = route_layers['layer3'] / total_routes * 100
        print(f'  路由 LLM 调用率: {llm_route_rate:.1f}% (越低越好)')
    print()
    return {'llm_calls': len(llm_calls), 'route_layers': route_layers}


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if cmd == 'route':
        eval_route()
    elif cmd == 'history':
        eval_history()
    elif cmd == 'e2e':
        eval_e2e()
    elif cmd == 'robustness':
        eval_robustness()
    elif cmd == 'tool':
        eval_tool_reliability()
    elif cmd == 'performance':
        eval_performance()
    elif cmd == 'cost':
        eval_cost()
    elif cmd == 'all':
        print('=' * 60)
        print(' CrashSight Agent 全面评估')
        print('=' * 60)
        print()
        eval_route()
        eval_robustness()
        eval_tool_reliability()
        eval_performance()
        eval_cost()
        print('\n[提示] E2E 评估需要调真实 API，请单独运行: python -m tests.eval.runner e2e')
    else:
        print(f'未知命令: {cmd}')
        print('用法: python -m tests.eval.runner [route|history|e2e|robustness|tool|performance|cost|all]')
