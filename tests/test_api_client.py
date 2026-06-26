"""api_client 单元测试 — 重试逻辑、错误处理"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
import requests


class TestOpenAPIGet:
    """openapi_get 测试"""

    @patch('crashsight_agent.api_client.requests.get')
    @patch('crashsight_agent.api_client.build_auth_params')
    def test_success(self, mock_auth, mock_get):
        """正常 GET 请求"""
        from crashsight_agent.api_client import openapi_get

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'ret': {'data': 'ok'}}
        mock_get.return_value = mock_resp

        result = openapi_get('https://example.com/api/test')
        assert result == {'ret': {'data': 'ok'}}

    @patch('crashsight_agent.api_client.requests.get')
    @patch('crashsight_agent.api_client.build_auth_params')
    @patch('crashsight_agent.api_client.time.sleep')
    def test_retry_on_429(self, mock_sleep, mock_auth, mock_get):
        """429 限流时自动重试"""
        from crashsight_agent.api_client import openapi_get

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        resp_429 = MagicMock(status_code=429, text='rate limited')
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {'data': 'ok'}
        mock_get.side_effect = [resp_429, resp_200]

        result = openapi_get('https://example.com/api/test', max_retries=2)
        assert result == {'data': 'ok'}
        assert mock_get.call_count == 2

    @patch('crashsight_agent.api_client.requests.get')
    @patch('crashsight_agent.api_client.build_auth_params')
    def test_raises_on_404(self, mock_auth, mock_get):
        """404 不重试，直接抛异常"""
        from crashsight_agent.api_client import openapi_get, CrashSightAPIError

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        mock_resp = MagicMock(status_code=404, text='not found')
        mock_get.return_value = mock_resp

        with pytest.raises(CrashSightAPIError) as exc_info:
            openapi_get('https://example.com/api/test')
        assert exc_info.value.status_code == 404

    @patch('crashsight_agent.api_client.requests.get')
    @patch('crashsight_agent.api_client.build_auth_params')
    @patch('crashsight_agent.api_client.time.sleep')
    def test_retry_on_timeout(self, mock_sleep, mock_auth, mock_get):
        """超时自动重试"""
        from crashsight_agent.api_client import openapi_get, CrashSightAPIError

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        mock_get.side_effect = requests.exceptions.Timeout('timed out')

        with pytest.raises(CrashSightAPIError) as exc_info:
            openapi_get('https://example.com/api/test', max_retries=2)
        assert exc_info.value.status_code == 408
        assert mock_get.call_count == 3  # 1 initial + 2 retries


class TestOpenAPIPost:
    """openapi_post 测试"""

    @patch('crashsight_agent.api_client.requests.post')
    @patch('crashsight_agent.api_client.build_auth_params')
    def test_success(self, mock_auth, mock_post):
        """正常 POST 请求"""
        from crashsight_agent.api_client import openapi_post

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'ret': {'issueList': []}}
        mock_post.return_value = mock_resp

        result = openapi_post('https://example.com/api/search', {'appId': 'test'})
        assert result == {'ret': {'issueList': []}}

    @patch('crashsight_agent.api_client.requests.post')
    @patch('crashsight_agent.api_client.build_auth_params')
    @patch('crashsight_agent.api_client.time.sleep')
    def test_retry_on_502(self, mock_sleep, mock_auth, mock_post):
        """502 网关错误自动重试"""
        from crashsight_agent.api_client import openapi_post

        mock_auth.return_value = {'userId': '123', 'sign': 'abc'}
        resp_502 = MagicMock(status_code=502, text='bad gateway')
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {'data': 'recovered'}
        mock_post.side_effect = [resp_502, resp_502, resp_200]

        result = openapi_post('https://example.com/api/test', {}, max_retries=3)
        assert result == {'data': 'recovered'}
        assert mock_post.call_count == 3


class TestCrashSightAPIError:
    """异常类测试"""

    def test_is_retryable(self):
        from crashsight_agent.api_client import CrashSightAPIError
        assert CrashSightAPIError(429, 'rate limit').is_retryable is True
        assert CrashSightAPIError(502, 'bad gateway').is_retryable is True
        assert CrashSightAPIError(404, 'not found').is_retryable is False

    def test_is_auth_error(self):
        from crashsight_agent.api_client import CrashSightAPIError
        assert CrashSightAPIError(401, 'unauthorized').is_auth_error is True
        assert CrashSightAPIError(403, 'forbidden').is_auth_error is True
        assert CrashSightAPIError(500, 'server error').is_auth_error is False
