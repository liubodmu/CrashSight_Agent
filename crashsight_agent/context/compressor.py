"""历史压缩器 — 对话历史超限时结构化摘要压缩（保留关键数字）"""
import re
from .token_counter import count_tokens
from ..llm_client import call_llm


class HistoryCompressor:
    """对话历史压缩器
    
    当对话历史 token 超过阈值时:
    1. 先提取每轮对话的关键数据（数字、百分比、版本号、Issue ID）
    2. 用结构化摘要压缩（而非粗暴截断）
    3. 保留最近 3 轮完整对话（不压缩）
    
    改进点（vs 旧版直接截断150字符）:
    - 不丢失关键数字（崩溃率、用户数、版本号）
    - 结构化提取后再摘要，信息密度更高
    """

    def __init__(self, threshold_tokens: int = 3000, keep_recent: int = 3):
        self.threshold_tokens = threshold_tokens
        self.keep_recent = keep_recent

    def maybe_compress(self, session_history: list) -> list:
        """检查是否需要压缩，需要则执行压缩"""
        if len(session_history) <= self.keep_recent + 1:
            return session_history

        history_text = '\n'.join([
            f"用户: {t['user']}\n助手: {t['assistant']}" for t in session_history
        ])
        tokens = count_tokens(history_text)

        if tokens <= self.threshold_tokens:
            return session_history

        early_history = session_history[:-self.keep_recent]
        recent_history = session_history[-self.keep_recent:]

        print(f'[Compressor] 历史 {len(session_history)} 轮 ({tokens} token) 超限，压缩前 {len(early_history)} 轮')

        summary = self._structured_summarize(early_history)

        if summary:
            compressed = [{'user': '[历史摘要]', 'assistant': summary}] + recent_history
            new_tokens = count_tokens('\n'.join([f"用户: {t['user']}\n助手: {t['assistant']}" for t in compressed]))
            print(f'[Compressor] 压缩完成: {tokens} → {new_tokens} token (节省 {tokens - new_tokens})')
            return compressed
        else:
            # LLM 不可用时，用本地结构化提取兜底
            fallback = self._local_extract(early_history)
            compressed = [{'user': '[历史摘要]', 'assistant': fallback}] + recent_history
            print(f'[Compressor] LLM 不可用，使用本地提取兜底')
            return compressed

    def _structured_summarize(self, early_history: list) -> str:
        """结构化摘要：先提取关键数据，再让 LLM 压缩"""
        # 每轮对话先做本地关键信息提取（保留数字）
        extracted_rounds = []
        for t in early_history:
            user_msg = t['user']
            assistant_msg = t['assistant']
            # 提取 assistant 回复中的关键数据
            key_data = _extract_key_data(assistant_msg)
            # 保留用户完整 query + 提取后的关键数据
            extracted_rounds.append(f"用户: {user_msg}\n结果: {key_data}")

        conversation = '\n'.join(extracted_rounds)

        prompt = f"""请将以下对话历史压缩为结构化摘要。

要求：
1. 必须保留所有数字数据（崩溃率、用户数、崩溃次数、版本号）
2. 必须保留项目名、Issue ID、TAPD 状态
3. 用"项目-版本-时间→关键发现"的格式
4. 不超过 200 字

对话内容:
{conversation}

直接输出摘要："""

        summary = call_llm(prompt, temperature=0.1)
        return summary.strip() if summary else ''

    def _local_extract(self, early_history: list) -> str:
        """本地兜底：不调 LLM，直接提取关键数据拼接"""
        parts = []
        for t in early_history:
            user_msg = t['user'][:60]
            key_data = _extract_key_data(t['assistant'])
            if key_data:
                parts.append(f"Q:{user_msg} → {key_data}")
        return '\n'.join(parts[-5:])  # 最多保留最近5轮的提取结果

    def force_compress(self, session_history: list) -> list:
        """强制压缩（不管是否超限）"""
        if len(session_history) <= self.keep_recent:
            return session_history
        
        early_history = session_history[:-self.keep_recent]
        recent_history = session_history[-self.keep_recent:]
        summary = self._structured_summarize(early_history)
        
        if summary:
            return [{'user': '[历史摘要]', 'assistant': summary}] + recent_history
        fallback = self._local_extract(early_history)
        return [{'user': '[历史摘要]', 'assistant': fallback}] + recent_history


def _extract_key_data(text: str) -> str:
    """从回复文本中提取关键数据（数字/百分比/ID/状态），不超过 300 字符
    
    保留:
    - 百分比 (0.373%)
    - 大数字 (31,869 / 965458)
    - 版本号 (3.7.376.376.376)
    - Issue ID
    - 状态关键词（已关闭/新建/已修复/历史问题/新问题）
    """
    if not text:
        return ''

    extracted = []

    # 提取百分比
    pcts = re.findall(r'\d+\.?\d*%', text)
    if pcts:
        extracted.append(f"率:{','.join(pcts[:5])}")

    # 提取大数字（≥100，带逗号或不带）
    numbers = re.findall(r'(?<!\.)(?:\d{1,3}(?:,\d{3})+|\d{3,})(?!\.?\d)', text)
    if numbers:
        extracted.append(f"数:{','.join(numbers[:5])}")

    # 提取版本号
    versions = re.findall(r'\d+\.\d+(?:\.\d+)+', text)
    if versions:
        unique_vers = list(dict.fromkeys(versions))[:3]
        extracted.append(f"版本:{','.join(unique_vers)}")

    # 提取状态关键词
    status_keywords = ['已关闭', '已修复', '新建', '已拒绝', '历史问题', '新问题', '高风险', '中风险', '低风险']
    found_status = [k for k in status_keywords if k in text]
    if found_status:
        extracted.append(f"状态:{','.join(found_status)}")

    # 提取 Issue ID（十六进制格式）
    issue_ids = re.findall(r'[A-F0-9]{8,12}', text)
    if issue_ids:
        extracted.append(f"Issue:{','.join(issue_ids[:3])}")

    # 提取异常名
    exc_names = re.findall(r'(?:SIGSEGV|SIGABRT|SIGBUS|NullPointer|OOM|OnDump\w+|LogUnLua\w*)', text)
    if exc_names:
        unique_exc = list(dict.fromkeys(exc_names))[:3]
        extracted.append(f"异常:{','.join(unique_exc)}")

    result = '; '.join(extracted)
    return result[:300] if result else text[:200]
