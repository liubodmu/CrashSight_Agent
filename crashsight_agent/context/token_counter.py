"""Token 计数器 — 精确计算文本占多少 token"""
import tiktoken

# 缓存 encoder（只初始化一次）
_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        try:
            _encoder = tiktoken.encoding_for_model('gpt-4')
        except Exception:
            # tiktoken 不认识的模型，用 cl100k_base（GPT-4/DeepSeek 通用）
            _encoder = tiktoken.get_encoding('cl100k_base')
    return _encoder


def count_tokens(text: str) -> int:
    """计算单段文本的 token 数"""
    if not text:
        return 0
    return len(_get_encoder().encode(text))


def count_messages_tokens(messages: list) -> int:
    """计算 OpenAI 格式消息列表的总 token 数
    
    每条消息额外 4 token（role/content 分隔符）
    整体额外 3 token（对话结尾标记）
    """
    total = 3  # 对话结尾
    for msg in messages:
        total += 4  # 每条消息开销
        total += count_tokens(msg.get('content', '') or '')
        total += count_tokens(msg.get('role', ''))
        # function call 的 name/arguments
        if msg.get('function_call'):
            total += count_tokens(msg['function_call'].get('name', ''))
            total += count_tokens(msg['function_call'].get('arguments', ''))
    return total


def truncate_to_tokens(text: str, max_tokens: int, keep_head: bool = True) -> str:
    """将文本截断到指定 token 数以内
    
    keep_head=True: 保留开头（截掉尾部）
    keep_head=False: 保留尾部（截掉开头）— 适合堆栈（底部通常更重要）
    """
    if not text:
        return text
    
    encoder = _get_encoder()
    tokens = encoder.encode(text)
    
    if len(tokens) <= max_tokens:
        return text
    
    if keep_head:
        truncated_tokens = tokens[:max_tokens]
    else:
        truncated_tokens = tokens[-max_tokens:]
    
    result = encoder.decode(truncated_tokens)
    indicator = f'\n... (已截断，原始 {len(tokens)} token，保留 {max_tokens} token)'
    return result + indicator
