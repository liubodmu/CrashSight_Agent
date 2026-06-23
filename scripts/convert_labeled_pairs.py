#!/usr/bin/env python3
"""将标注完成的堆栈对转为评估用的 history_golden.jsonl

用法:
    python scripts/convert_labeled_pairs.py

输入: tests/eval/data/history_pairs_to_label.jsonl（已标注 label）
输出: tests/eval/data/history_golden.jsonl
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

INPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tests', 'eval', 'data', 'history_pairs_to_label.jsonl')
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tests', 'eval', 'data', 'history_golden.jsonl')


def convert():
    if not os.path.exists(INPUT_FILE):
        print(f'输入文件不存在: {INPUT_FILE}')
        print('请先运行: python scripts/collect_history_pairs.py')
        return

    pairs = []
    unlabeled = 0
    for line in open(INPUT_FILE, 'r', encoding='utf-8'):
        if not line.strip():
            continue
        pair = json.loads(line)
        if pair.get('label') is None:
            unlabeled += 1
            continue
        # 转为 eval runner 需要的格式
        golden = {
            'stack_a': pair['stack_a'],
            'stack_b': pair['stack_b'],
            'exc_a': pair.get('exc_a', ''),
            'exc_b': pair.get('exc_b', ''),
            'key_frame': pair.get('key_frame', ''),
            'label': pair['label'],
        }
        pairs.append(golden)

    if unlabeled > 0:
        print(f'警告: {unlabeled} 对尚未标注（label=null），已跳过')

    if not pairs:
        print('没有已标注的数据！请先标注 label 字段。')
        return

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + '\n')

    true_count = sum(1 for p in pairs if p['label'])
    false_count = sum(1 for p in pairs if not p['label'])
    print(f'转换完成！')
    print(f'  输出: {OUTPUT_FILE}')
    print(f'  总数: {len(pairs)} 对')
    print(f'  正例(同一Bug): {true_count}')
    print(f'  负例(不同Bug): {false_count}')
    print(f'\n现在可以运行评估:')
    print(f'  python -m tests.eval.runner history')


if __name__ == '__main__':
    convert()
