"""LLM 调用客户端 — 统一封装 OpenAI 兼容接口"""
from openai import OpenAI
from .config import LLM_MODEL, LLM_API_KEY, LLM_BASE_URL


_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


def call_llm(prompt: str, system: str = None, temperature: float = 0.3) -> str:
    """简单文本生成调用"""
    if not LLM_API_KEY:
        print('[LLM] 未配置 API Key，跳过 LLM 调用')
        return ''

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=2000,
        )
        return resp.choices[0].message.content or ''
    except Exception as e:
        print(f'[LLM] 调用失败: {e}')
        return ''


def call_llm_with_tools(messages: list, tools: list, temperature: float = 0.3):
    """带 Function Calling 的调用，返回完整 response message"""
    if not LLM_API_KEY:
        print('[LLM] 未配置 API Key')
        return None

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=2000,
        )
        return resp.choices[0].message
    except Exception as e:
        print(f'[LLM] Function Calling 调用失败: {e}')
        return None
