"""llm_client 单元测试 — 重试机制、异常处理"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError


class TestCallLLM:
    """call_llm 函数测试"""

    @patch('crashsight_agent.llm_client.LLM_API_KEY', 'test-key')
    @patch('crashsight_agent.llm_client.get_client')
    def test_success(self, mock_get_client):
        """正常调用成功"""
        from crashsight_agent.llm_client import call_llm

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = 'YES: 堆栈一致'
        mock_client.chat.completions.create.return_value = mock_resp
        mock_get_client.return_value = mock_client

        result = call_llm('test prompt')
        assert result == 'YES: 堆栈一致'
        mock_client.chat.completions.create.assert_called_once()

    @patch('crashsight_agent.llm_client.LLM_API_KEY', '')
    def test_no_api_key_returns_empty(self):
        """未配置 API Key 返回空字符串"""
        from crashsight_agent.llm_client import call_llm
        result = call_llm('test')
        assert result == ''

    @patch('crashsight_agent.llm_client.LLM_API_KEY', 'test-key')
    @patch('crashsight_agent.llm_client.get_client')
    @patch('crashsight_agent.llm_client.time.sleep')
    def test_retry_on_timeout(self, mock_sleep, mock_get_client):
        """超时时自动重试"""
        from crashsight_agent.llm_client import call_llm

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            APITimeoutError(request=MagicMock()),
            APITimeoutError(request=MagicMock()),
            MagicMock(choices=[MagicMock(message=MagicMock(content='ok'))]),
        ]
        mock_get_client.return_value = mock_client

        result = call_llm('test', max_retries=3)
        assert result == 'ok'
        assert mock_client.chat.completions.create.call_count == 3
        assert mock_sleep.call_count == 2  # 两次重试等待

    @patch('crashsight_agent.llm_client.LLM_API_KEY', 'test-key')
    @patch('crashsight_agent.llm_client.get_client')
    @patch('crashsight_agent.llm_client.time.sleep')
    def test_retry_exhausted_returns_empty(self, mock_sleep, mock_get_client):
        """重试耗尽后返回空字符串"""
        from crashsight_agent.llm_client import call_llm

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
        mock_get_client.return_value = mock_client

        result = call_llm('test', max_retries=2)
        assert result == ''
        assert mock_client.chat.completions.create.call_count == 3  # 1 + 2 retries

    @patch('crashsight_agent.llm_client.LLM_API_KEY', 'test-key')
    @patch('crashsight_agent.llm_client.get_client')
    def test_non_retryable_error_returns_immediately(self, mock_get_client):
        """不可重试错误立即返回"""
        from crashsight_agent.llm_client import call_llm

        mock_client = MagicMock()
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {'error': {'message': 'bad request'}}
        mock_client.chat.completions.create.side_effect = APIStatusError(
            message='bad request', response=error_response, body={'error': {'message': 'bad'}}
        )
        mock_get_client.return_value = mock_client

        result = call_llm('test', max_retries=3)
        assert result == ''
        assert mock_client.chat.completions.create.call_count == 1  # 不重试


class TestCallLLMWithTools:
    """call_llm_with_tools 函数测试"""

    @patch('crashsight_agent.llm_client.LLM_API_KEY', 'test-key')
    @patch('crashsight_agent.llm_client.get_client')
    def test_success(self, mock_get_client):
        """正常 tool call 成功"""
        from crashsight_agent.llm_client import call_llm_with_tools

        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=mock_message)]
        mock_client.chat.completions.create.return_value = mock_resp
        mock_get_client.return_value = mock_client

        result = call_llm_with_tools([{'role': 'user', 'content': 'hi'}], [])
        assert result == mock_message

    @patch('crashsight_agent.llm_client.LLM_API_KEY', '')
    def test_no_api_key_returns_none(self):
        """未配置 API Key 返回 None"""
        from crashsight_agent.llm_client import call_llm_with_tools
        result = call_llm_with_tools([], [])
        assert result is None
