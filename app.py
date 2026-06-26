"""CrashSight Agent — FastAPI Web 服务（支持 SSE 流式推理输出）"""
import json
import asyncio
import threading
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from crashsight_agent.orchestration.agent import CrashSightAgent
from crashsight_agent.streaming.events import EventEmitter, set_emitter, bind_session, EventType
from crashsight_agent.logging.logger import bind_logger_session

app = FastAPI(title="CrashSight Analysis Agent")

# CORS 配置：默认只允许本地开发，生产环境通过环境变量 CORS_ORIGINS 配置
import os
_cors_origins = os.getenv('CORS_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# 会话管理（线程安全）
_sessions: dict[str, CrashSightAgent] = {}
_sessions_lock = threading.Lock()


def _get_agent(session_id: str) -> CrashSightAgent:
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = CrashSightAgent(thread_id=session_id)
        return _sessions[session_id]


@app.post("/api/chat")
async def chat(request: Request):
    """普通对话接口（等全部完成再返回）"""
    body = await request.json()
    query = body.get('query', '').strip()
    session_id = body.get('session_id', 'default')

    if not query:
        return {'success': False, 'error': '请输入查询内容'}

    agent = _get_agent(session_id)
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, agent.chat, query)

    return {
        'success': True,
        'answer': answer,
        'session_id': session_id,
    }


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """SSE 流式对话接口 — 实时推送 Agent 执行过程"""
    body = await request.json()
    query = body.get('query', '').strip()
    session_id = body.get('session_id', 'default')

    if not query:
        return {'success': False, 'error': '请输入查询内容'}

    agent = _get_agent(session_id)

    async def event_generator():
        # 创建事件发射器（绑定到当前 session）
        emitter = EventEmitter()
        queue = emitter.enable_async()

        # 设置当前 session 的 emitter（线程隔离）
        set_emitter(emitter, session_id=session_id)

        # 发送开始事件
        emitter.emit(EventType.AGENT_START, '开始处理...', node='agent')
        yield emitter.events[-1].to_sse()

        # 在线程池中运行 Agent（绑定 session 上下文）
        def _run_with_context():
            bind_session(session_id)
            bind_logger_session(session_id)
            return agent.chat(query)

        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, _run_with_context)

        # 边执行边消费事件队列
        while True:
            try:
                # 尝试从队列取事件（100ms 超时）
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield event.to_sse()
            except asyncio.TimeoutError:
                # 检查 Agent 是否已完成
                if task.done():
                    # 排空队列
                    while not queue.empty():
                        event = queue.get_nowait()
                        yield event.to_sse()
                    break

        # 获取最终结果
        answer = task.result()

        # 发送最终回答
        end_event = f"data: {json.dumps({'type': 'answer', 'message': answer, 'node': 'end'}, ensure_ascii=False)}\n\n"
        yield end_event

        # 发送结束标记
        yield f"data: {json.dumps({'type': 'agent_end', 'message': '完成'}, ensure_ascii=False)}\n\n"

        # 清理当前 session 的 emitter
        set_emitter(None, session_id=session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )


@app.post("/api/reset")
async def reset(request: Request):
    body = await request.json()
    session_id = body.get('session_id', 'default')
    if session_id in _sessions:
        _sessions[session_id].reset()
    return {'success': True, 'message': '会话已重置'}


@app.get("/api/versions/{project_id}")
async def get_versions(project_id: str):
    """获取指定项目的版本列表"""
    import asyncio
    from crashsight_agent.config import PROJECTS, CRASHSIGHT_BASE, USER_AUTH
    from crashsight_agent.api_client import openapi_get

    project = PROJECTS.get(project_id)
    if not project:
        return {'success': False, 'error': '项目不存在'}

    app_id = project['appId']
    pid = project['pid']

    try:
        loop = asyncio.get_event_loop()
        url = (
            f'{CRASHSIGHT_BASE}/uniform/openapi/getSelectorDatas'
            f'/appId/{app_id}/pid/{pid}?types=version'
        )
        data = await loop.run_in_executor(None, openapi_get, url)

        # 解析版本列表（兼容多种格式）
        versions = []
        ret = data.get('ret', {})
        if isinstance(ret, dict):
            d = ret.get('data', {})
            if isinstance(d, dict):
                for key in ['versionList', 'version', 'versions']:
                    if key in d and isinstance(d[key], list):
                        raw = d[key]
                        for item in raw:
                            if isinstance(item, str):
                                versions.append(item)
                            elif isinstance(item, dict):
                                for k in ['productVersion', 'version', 'name']:
                                    if k in item and item[k]:
                                        versions.append(str(item[k]))
                                        break
                        break

        # 去重 + 按版本号降序
        seen = set()
        unique = []
        for v in versions:
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                unique.append(v)

        import re
        def ver_key(v):
            parts = re.split(r'[.\-]', v)
            return [int(p) if p.isdigit() else 0 for p in parts]
        unique.sort(key=ver_key, reverse=True)

        return {'success': True, 'data': unique[:50]}
    except Exception as e:
        return {'success': False, 'error': str(e)[:100], 'data': []}


@app.post("/api/feedback")
async def feedback(request: Request):
    """用户反馈接口 — 记录错误判定 + 提炼规则"""
    from crashsight_agent.tools.feedback import record_feedback, get_feedback_stats
    body = await request.json()

    issue_id = body.get('issue_id', '')
    key_frame = body.get('key_frame', '')
    exp_stack = body.get('exp_stack', '')
    prod_stack = body.get('prod_stack', '')
    original_prediction = body.get('original_prediction', 'YES')
    ground_truth = body.get('ground_truth', 'NO')
    user_reason = body.get('user_reason', '')

    if not user_reason:
        return {'success': False, 'error': '请提供判错原因'}

    result = record_feedback(
        issue_id=issue_id, key_frame=key_frame,
        exp_stack=exp_stack, prod_stack=prod_stack,
        original_prediction=original_prediction,
        ground_truth=ground_truth, user_reason=user_reason,
    )
    return {'success': True, **result}


@app.get("/api/feedback/stats")
async def feedback_stats():
    """查看反馈统计 + 已提炼的规则"""
    from crashsight_agent.tools.feedback import get_feedback_stats
    return get_feedback_stats()


@app.get("/api/health")
async def health():
    return {'status': 'ok', 'sessions': len(_sessions)}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open('static/index.html', 'r', encoding='utf-8') as f:
        return f.read()


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == '__main__':
    import uvicorn
    print("\n  CrashSight Agent Web UI")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host='0.0.0.0', port=8000)
