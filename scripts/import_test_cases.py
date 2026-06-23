#!/usr/bin/env python3
"""从原项目的 test_compare_algorithms_v2.py 中导入 35 个已标注堆栈对到 history_golden.jsonl"""
import sys
import os
import json

sys.path.insert(0, r'C:\Users\tyboliu\CodeBuddy\20260414104711')
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 导入原测试文件中的 test_cases（通过 exec）
test_cases = {}
exec_globals = {'test_cases': test_cases}

# 只提取 test_cases 定义部分
source_file = r'C:\Users\tyboliu\CodeBuddy\20260608145437\test_compare_algorithms_v2.py'
with open(source_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 找到 test_cases 开始的位置
start_marker = "test_cases = {}"
start_idx = content.find(start_marker)
# 找到运行测试的位置
end_marker = "# ══════════════════════════════════════════════════════════\n# 运行对比测试"
end_idx = content.find(end_marker)

if start_idx < 0 or end_idx < 0:
    print("无法从源文件中定位 test_cases")
    sys.exit(1)

cases_code = content[start_idx:end_idx]
exec(cases_code, exec_globals)
test_cases = exec_globals['test_cases']

print(f'从 {source_file} 导入了 {len(test_cases)} 个测试用例')

# 转换为 history_golden.jsonl 格式
output_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tests', 'eval', 'data', 'history_golden.jsonl')

# 对每个 case，提取关键帧
from crashsight_agent.tools.keyframe import extract_key_frame

pairs = []
for name, tc in test_cases.items():
    stack_a = tc['stack1']
    stack_b = tc['stack2']
    exc_a = tc['exp_exc']
    exc_b = tc['cand_exc']
    label = tc['expected']

    # 提取关键帧
    kf_ret = extract_key_frame(stack_a, stack_a, exception_name=exc_a)
    key_frame = kf_ret[0] if kf_ret else ''

    pairs.append({
        'stack_a': stack_a.strip(),
        'stack_b': stack_b.strip(),
        'exc_a': exc_a,
        'exc_b': exc_b,
        'key_frame': key_frame,
        'label': label,
        'name': name,
    })

with open(output_file, 'w', encoding='utf-8') as f:
    for p in pairs:
        f.write(json.dumps(p, ensure_ascii=False) + '\n')

true_count = sum(1 for p in pairs if p['label'])
false_count = sum(1 for p in pairs if not p['label'])
print(f'\n输出: {output_file}')
print(f'总数: {len(pairs)} 对')
print(f'正例(同一Bug): {true_count}')
print(f'负例(不同Bug): {false_count}')
print(f'\n现在可以运行: python -m tests.eval.runner history')
