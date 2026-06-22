"""评估指标计算"""


def precision_recall_f1(predictions: list, ground_truths: list) -> dict:
    """计算 Precision / Recall / F1
    
    predictions: [True, False, True, ...]  (模型判定)
    ground_truths: [True, True, False, ...]  (人工标注)
    """
    assert len(predictions) == len(ground_truths)
    
    tp = sum(1 for p, g in zip(predictions, ground_truths) if p and g)
    fp = sum(1 for p, g in zip(predictions, ground_truths) if p and not g)
    fn = sum(1 for p, g in zip(predictions, ground_truths) if not p and g)
    tn = sum(1 for p, g in zip(predictions, ground_truths) if not p and not g)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(predictions) if predictions else 0.0

    return {
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'f1': round(f1, 4),
        'accuracy': round(accuracy, 4),
        'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        'total': len(predictions),
    }


def route_accuracy(predictions: list, ground_truths: list) -> dict:
    """Route 评估: 意图准确率 + 参数准确率
    
    predictions: [{'intent': ..., 'project_id': ..., 'version': ..., 'start_date': ..., 'end_date': ...}, ...]
    ground_truths: 同上格式
    """
    assert len(predictions) == len(ground_truths)

    intent_correct = 0
    params_correct = 0
    field_stats = {'project_id': 0, 'version': 0, 'start_date': 0, 'end_date': 0}

    for pred, gt in zip(predictions, ground_truths):
        # 意图
        if pred.get('intent') == gt.get('intent'):
            intent_correct += 1

        # 各参数
        all_correct = True
        for field in field_stats:
            if pred.get(field) == gt.get(field):
                field_stats[field] += 1
            else:
                all_correct = False

        if all_correct and pred.get('intent') == gt.get('intent'):
            params_correct += 1

    total = len(predictions)
    return {
        'intent_accuracy': round(intent_correct / total, 4) if total else 0,
        'full_accuracy': round(params_correct / total, 4) if total else 0,
        'field_accuracy': {k: round(v / total, 4) for k, v in field_stats.items()},
        'total': total,
        'intent_correct': intent_correct,
        'params_correct': params_correct,
    }


def tool_reliability(results: list) -> dict:
    """工具可靠性统计
    
    results: [{'tool': 'xxx', 'success': True/False, 'duration_ms': 123, 'retried': 0}, ...]
    """
    by_tool = {}
    for r in results:
        tool = r.get('tool', 'unknown')
        if tool not in by_tool:
            by_tool[tool] = {'success': 0, 'fail': 0, 'durations': [], 'retries': []}
        if r.get('success'):
            by_tool[tool]['success'] += 1
        else:
            by_tool[tool]['fail'] += 1
        if r.get('duration_ms'):
            by_tool[tool]['durations'].append(r['duration_ms'])
        if r.get('retried'):
            by_tool[tool]['retries'].append(r['retried'])

    stats = {}
    for tool, data in by_tool.items():
        total = data['success'] + data['fail']
        stats[tool] = {
            'success_rate': round(data['success'] / total, 4) if total else 0,
            'total_calls': total,
            'avg_duration_ms': round(sum(data['durations']) / len(data['durations'])) if data['durations'] else 0,
            'p95_duration_ms': sorted(data['durations'])[int(len(data['durations']) * 0.95)] if len(data['durations']) > 1 else 0,
            'avg_retries': round(sum(data['retries']) / len(data['retries']), 2) if data['retries'] else 0,
        }

    return stats
