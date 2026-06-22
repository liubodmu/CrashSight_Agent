"""历史压缩器 — 对话历史超限时用 LLM 摘要压缩"""
from .token_counter import count_tokens
from ..llm_client import call_llm


class HistoryCompressor:
    """对话历史压缩器
    
    当对话历史 token 超过阈值时:
    1. 把早期对话（前 N 轮）发给 LLM 做摘要
    2. 摘要替换原始历史
    3. 保留最近 3 轮完整对话（不压缩）
    
    效果:
    - 压缩前: 10 轮完整对话 = 4000 token
    - 压缩后: 1 段摘要(200 token) + 最近 3 轮(800 token) = 1000 token
    """

    def __init__(self, threshold_tokens: int = 3000, keep_recent: int = 3):
        self.threshold_tokens = threshold_tokens
        self.keep_recent = keep_recent

    def maybe_compress(self, session_history: list) -> list:
        """检查是否需要压缩，需要则执行压缩
        
        返回: 压缩后的 session_history（可能和原来一样）
        """
        if len(session_history) <= self.keep_recent + 1:
            # 历史太短，不需要压缩
            return session_history

        # 计算当前 token
        history_text = '\n'.join([
            f"用户: {t['user']}\n助手: {t['assistant']}" for t in session_history
        ])
        tokens = count_tokens(history_text)

        if tokens <= self.threshold_tokens:
            return session_history

        # 需要压缩: 把前面的轮次摘要，保留最近 keep_recent 轮
        early_history = session_history[:-self.keep_recent]
        recent_history = session_history[-self.keep_recent:]

        print(f'[Compressor] 历史 {len(session_history)} 轮 ({tokens} token) 超限，压缩前 {len(early_history)} 轮')

        # 用 LLM 摘要
        summary = self._summarize(early_history)

        if summary:
            # 摘要作为一条特殊历史记录插入
            compressed = [{'user': '[历史摘要]', 'assistant': summary}] + recent_history
            new_tokens = count_tokens('\n'.join([f"用户: {t['user']}\n助手: {t['assistant']}" for t in compressed]))
            print(f'[Compressor] 压缩完成: {tokens} → {new_tokens} token (节省 {tokens - new_tokens})')
            return compressed
        else:
            # LLM 不可用时，简单丢弃早期历史
            print(f'[Compressor] LLM 不可用，直接丢弃早期历史')
            return recent_history

    def _summarize(self, early_history: list) -> str:
        """用 LLM 将多轮对话摘要为一段话"""
        conversation = '\n'.join([
            f"用户: {t['user']}\n助手: {t['assistant'][:150]}" for t in early_history
        ])

        prompt = f"""请将以下多轮对话摘要为一段简短的总结（不超过 100 字），保留关键信息（查了什么项目、什么版本、发现了什么问题）。

对话内容:
{conversation}

请直接输出摘要，不要加任何前缀。"""

        summary = call_llm(prompt, temperature=0.1)
        return summary.strip() if summary else ''

    def force_compress(self, session_history: list) -> list:
        """强制压缩（不管是否超限）"""
        if len(session_history) <= self.keep_recent:
            return session_history
        
        early_history = session_history[:-self.keep_recent]
        recent_history = session_history[-self.keep_recent:]
        summary = self._summarize(early_history)
        
        if summary:
            return [{'user': '[历史摘要]', 'assistant': summary}] + recent_history
        return recent_history
