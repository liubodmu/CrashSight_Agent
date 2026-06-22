"""核心 Agent — 基于 OpenAI Function Calling 的 ReAct 循环"""
import json
from ..llm_client import call_llm_with_tools
from ..tools import TOOL_SCHEMAS, execute_tool
from .prompts import get_system_prompt


class CrashSightAgent:
    """CrashSight 崩溃分析 Agent
    
    基于 Function Calling 的简洁 ReAct 循环:
    1. 用户输入 → LLM 决定调用哪个工具
    2. 执行工具 → 结果反馈给 LLM
    3. LLM 决定继续调用工具或直接回答
    4. 最多 max_steps 步
    """

    def __init__(self, max_steps: int = 8):
        self.max_steps = max_steps
        self.system_prompt = get_system_prompt()
        self.conversation_history = []  # 多轮对话历史
        self.last_tool_results = {}     # 上一轮工具结果（供追问使用）

    def chat(self, user_message: str) -> str:
        """处理一次用户消息，返回 Agent 回答"""
        # 构建消息列表
        messages = [{'role': 'system', 'content': self.system_prompt}]

        # 加入对话历史（最近 10 轮）
        for turn in self.conversation_history[-10:]:
            messages.append({'role': 'user', 'content': turn['user']})
            messages.append({'role': 'assistant', 'content': turn['assistant']})

        # 如果有上一轮结果作为上下文
        if self.last_tool_results:
            context = f"\n[上一轮查询结果摘要: {json.dumps(self._summarize_results(), ensure_ascii=False)[:500]}]"
            messages.append({'role': 'user', 'content': user_message + context})
        else:
            messages.append({'role': 'user', 'content': user_message})

        # ReAct 循环
        step = 0
        while step < self.max_steps:
            step += 1
            print(f'[Agent] Step {step}/{self.max_steps}')

            # 调用 LLM（带 tools）
            response_message = call_llm_with_tools(messages, TOOL_SCHEMAS)

            if response_message is None:
                return "抱歉，LLM 服务暂时不可用。请检查 API Key 配置。"

            # 如果 LLM 直接回答（没有调用工具）
            if not response_message.tool_calls:
                answer = response_message.content or ''
                self._save_turn(user_message, answer)
                return answer

            # 执行工具调用
            messages.append(response_message)  # 把 assistant 的 tool_calls 加入历史

            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                print(f'[Agent]   → {func_name}({json.dumps(func_args, ensure_ascii=False)[:100]})')

                # 执行工具
                result = execute_tool(func_name, func_args)

                # 保存结果
                self.last_tool_results[func_name] = result

                # 将工具结果反馈给 LLM
                result_str = json.dumps(result, ensure_ascii=False)
                # 截断过长结果
                if len(result_str) > 3000:
                    result_str = result_str[:3000] + '...(已截断)'

                messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_call.id,
                    'content': result_str,
                })

                if result.get('success'):
                    print(f'[Agent]   ✓ {func_name} 成功')
                else:
                    print(f'[Agent]   ✗ {func_name} 失败: {result.get("error", "")[:60]}')

        # 步数耗尽，让 LLM 用已有数据总结
        messages.append({'role': 'user', 'content': '请用已收集到的数据回答用户的问题。'})
        final = call_llm_with_tools(messages, [])  # 不给工具，强制文本输出
        answer = final.content if final else '抱歉，处理超时。'
        self._save_turn(user_message, answer)
        return answer

    def _save_turn(self, user_msg: str, assistant_msg: str):
        """保存对话轮次"""
        self.conversation_history.append({
            'user': user_msg,
            'assistant': assistant_msg,
        })

    def _summarize_results(self) -> dict:
        """摘要上一轮工具结果（供追问上下文）"""
        summary = {}
        for name, result in self.last_tool_results.items():
            if not result.get('success'):
                continue
            data = result.get('data')
            if name == 'get_top_issues' and isinstance(data, list):
                summary['top_issues'] = [
                    {'rank': i+1, 'issueId': item.get('issueId', ''), 'exception': item.get('exceptionName', '')}
                    for i, item in enumerate(data[:5])
                ]
            elif name == 'get_crash_trend' and isinstance(data, dict):
                summary['trend'] = {
                    'minRate': data.get('minRate'),
                    'maxRate': data.get('maxRate'),
                }
        return summary

    def reset(self):
        """重置对话"""
        self.conversation_history = []
        self.last_tool_results = {}
