"""窗口管理器 — 确保 Context 不超过 Token 预算"""
from .token_counter import count_tokens, count_messages_tokens, truncate_to_tokens


class WindowManager:
    """Token 窗口管理器
    
    功能:
    1. 跟踪当前 token 占用
    2. 工具结果超限时智能截断
    3. 对话历史超限时触发压缩
    4. 堆栈文本智能截断（保留关键帧附近）
    
    预算分配:
    - system prompt:   ~1000 token (固定)
    - 对话历史:        最多 4000 token
    - 工具结果:        每次最多 2000 token
    - 总预算:          8000 token (留足余量给 LLM 输出)
    """

    def __init__(self, max_total: int = 8000, max_history: int = 4000, max_tool_result: int = 2000):
        self.max_total = max_total
        self.max_history = max_history
        self.max_tool_result = max_tool_result
        self.current_usage = 0

    def truncate_tool_result(self, result_text: str) -> str:
        """截断工具返回结果，确保不超预算"""
        tokens = count_tokens(result_text)
        if tokens <= self.max_tool_result:
            return result_text
        
        print(f'[Window] 工具结果 {tokens} token 超限 ({self.max_tool_result})，执行截断')
        return truncate_to_tokens(result_text, self.max_tool_result, keep_head=True)

    def truncate_stack(self, stack_text: str, max_tokens: int = 1500) -> str:
        """智能截断堆栈
        
        策略: 保留前 10 行（崩溃点）+ 后 10 行（调用入口），中间截断
        """
        if not stack_text:
            return stack_text
        
        tokens = count_tokens(stack_text)
        if tokens <= max_tokens:
            return stack_text
        
        lines = stack_text.split('\n')
        if len(lines) <= 30:
            # 行数不多，直接 token 截断
            return truncate_to_tokens(stack_text, max_tokens, keep_head=True)
        
        # 保留前 15 行（崩溃点附近）+ 后 10 行（调用入口）
        head = '\n'.join(lines[:15])
        tail = '\n'.join(lines[-10:])
        middle_count = len(lines) - 25
        
        result = f"{head}\n\n... (省略中间 {middle_count} 行) ...\n\n{tail}"
        
        # 如果拼接后仍超限，再做 token 截断
        if count_tokens(result) > max_tokens:
            result = truncate_to_tokens(result, max_tokens, keep_head=True)
        
        print(f'[Window] 堆栈 {len(lines)} 行 ({tokens} token) → 截断为 {count_tokens(result)} token')
        return result

    def check_history_budget(self, session_history: list) -> tuple:
        """检查对话历史是否超预算
        
        返回: (is_over_budget, current_tokens)
        """
        # 把历史拼成文本计算 token
        history_text = '\n'.join([
            f"用户: {t['user']}\n助手: {t['assistant']}" for t in session_history
        ])
        tokens = count_tokens(history_text)
        return tokens > self.max_history, tokens

    def get_budget_status(self, session_history: list) -> dict:
        """获取当前预算状态"""
        history_text = '\n'.join([
            f"用户: {t['user']}\n助手: {t['assistant']}" for t in session_history
        ])
        history_tokens = count_tokens(history_text)
        
        return {
            'history_tokens': history_tokens,
            'history_budget': self.max_history,
            'history_usage_pct': round(history_tokens / self.max_history * 100, 1),
            'needs_compression': history_tokens > self.max_history * 0.8,
        }
