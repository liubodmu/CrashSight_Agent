"""CrashSight Agent — FastAPI Web 服务"""
import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from crashsight_agent.orchestration.agent import CrashSightAgent

app = FastAPI(title="CrashSight Analysis Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 会话管理（内存，重启丢失）
_sessions: dict[str, CrashSightAgent] = {}


def _get_agent(session_id: str) -> CrashSightAgent:
    if session_id not in _sessions:
        _sessions[session_id] = CrashSightAgent(thread_id=session_id)
    return _sessions[session_id]


@app.post("/api/chat")
async def chat(request: Request):
    """主对话接口"""
    body = await request.json()
    query = body.get('query', '').strip()
    session_id = body.get('session_id', 'default')

    if not query:
        return {'success': False, 'error': '请输入查询内容'}

    agent = _get_agent(session_id)

    # 在线程池中运行（避免阻塞）
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, agent.chat, query)

    return {
        'success': True,
        'answer': answer,
        'session_id': session_id,
        'thread_id': agent.thread_id,
    }


@app.post("/api/reset")
async def reset(request: Request):
    """重置会话"""
    body = await request.json()
    session_id = body.get('session_id', 'default')
    if session_id in _sessions:
        _sessions[session_id].reset()
    return {'success': True, 'message': '会话已重置'}


@app.get("/api/health")
async def health():
    return {'status': 'ok', 'sessions': len(_sessions)}


@app.get("/", response_class=HTMLResponse)
async def index():
    with open('static/index.html', 'r', encoding='utf-8') as f:
        return f.read()


# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == '__main__':
    import uvicorn
    print("\n  CrashSight Agent Web UI")
    print("  http://localhost:8000\n")
    uvicorn.run(app, host='0.0.0.0', port=8000)
