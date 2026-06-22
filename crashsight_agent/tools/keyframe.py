"""关键帧提取 — 从 app.py 原样搬过来的四轮打分算法

流程:
  ① 解析每帧的 (函数名, 路径, 行号)
  ② 打分（路径分类 + 函数语义）
  ③ 取"栈顶第一个 ≥100 分"的帧（真业务）
     其次取"第一个 ≥20 分"的帧（引擎/未知业务）
  ④ 没打到任何帧 → 退化到旧算法（第一个 Class::method）
  ⑤ 仍提不出 → 弱匹配（[anon:xxx] / 非通用 .so）
  ⑥ 完全不行 → None

返回值：(关键帧字符串, 是否弱匹配, 特征组列表) 或 None
"""
import re


# ─────── 配置：路径分类 ───────
_BIZ_PATH_KWS = (
    'Project/', 'Game/',
    'src/core/', 'src/h5ui/', 'src/pixui/', 'src/os_platform/',
    'pesapi/', 'pesapi_impl', 'PesapiQjsImpl',
    'com.itop.', 'com.pixui.',
)
_PLUGIN_PATH_KWS = ('Plugins/',)
_ENGINE_PATH_KWS = ('Runtime/', 'Engine/', 'Engine\\',)
_THIRDPARTY_PATH_KWS = ('thirdparty/', 'quickjs', '3rdparty/', 'third_party/',)
_SYSTEM_PATH_KWS = (
    '/apex/', '/system/', '/vendor/',
    'libc.so', 'libart.so', 'libdl.so', 'libm.so',
    'libbase.so', 'libcutils.so', 'libandroid.so',
    'libGLES', 'libEGL', 'libvulkan',
    'android.os.', 'java.lang.', 'com.android.internal',
    'dalvik.', 'ZygoteInit', 'RuntimeInit', 'ActivityThread',
    'libsystem', 'libdispatch', 'CoreFoundation',
    'ntdll.dll', 'KERNELBASE.dll', 'kernel32.dll',
)
_UE_FRAMEWORK_FUNCS = (
    'FAndroidErrorOutputDevice::', 'FIOSErrorOutputDevice::',
    'FWindowsErrorOutputDevice::', 'FLinuxErrorOutputDevice::',
    'FOutputDevice::Serialize', 'FOutputDevice::LogFImpl',
    'FOutputDevice::Log', 'FOutputDeviceRedirector::',
    'FMsg::Logf', 'FDebug::Assert', 'FDebug::EnsureFailed',
    'FDebug::LogAssertFailedMessage',
    'FPlatformMisc::RaiseException', 'ReportEnsure', 'NewReportEnsure',
    'CommonUnixCrashHandler', 'FPlatformStackWalk',
    'FAndroidPlatformStackWalk::', 'FWindowsPlatformStackWalk::',
    'StaticFailDebug', 'FError::LowLevelFatal',
)
_LOWLEVEL_FUNCS = (
    'memcpy', 'memset', 'memmove', 'memcmp',
    'malloc', 'free', 'realloc', 'calloc',
    'operator new', 'operator delete',
    '__pthread', '__kernel', 'signal handler', 'abort', 'raise',
    '_sigtramp', 'start_thread', 'pthread_mutex_lock',
    'google::breakpad::', 'crashpad::',
    'outofmemory', 'onoutofmemory',
)
_INFRA_WORDS = (
    'realloc', 'malloc', 'calloc', 'alloc', 'dealloc',
    'resize', 'reserve', 'shrink', 'grow',
    'emplace', 'addn', 'pophead', 'popfront', 'popback',
    'reference', 'addref', 'release', 'retain',
)
_FRAMEWORK_ENTRY_FUNCS = (
    'FRunnableThreadPThread::Run', 'FRunnableThreadPThread::_ThreadProc',
    'FRunnableThread::Run', '_ThreadProc',
    'FAsyncLoadingThread::TickAsyncThread', 'FAsyncLoadingThread::Run',
    'FAsyncLoadingThread::ProcessAsyncLoading',
    'FNamedTaskThread::ProcessTasksUntilQuit',
    'FNamedTaskThread::ProcessTasksNamedThread',
    'FTaskThreadBase::Run', 'FTaskThreadAnyThread::ProcessTasks',
    'FTaskGraphAnyTask::ProcessTasksUntilQuit',
    'FRHIThread::Run', 'FThreadSingletonInitializer::Get',
)
_VERIFY_CHECK_FUNCS = (
    'VerifyVulkanResult', 'VerifyD3D11Result', 'VerifyD3D12Result',
    'VerifyMetalResult', 'VerifyOpenGLResult',
    'FDebug::CheckVerify', 'FDebug::CheckAssert',
)


def _score(frame):
    func = frame.get('func')
    path = frame.get('path')
    is_global = frame.get('is_global', False)
    path_l = (path or '').replace('\\', '/').lower()
    func_l = (func or '').lower()
    s = 0
    if path:
        if any(kw.lower() in path_l for kw in _BIZ_PATH_KWS):
            s += 100
        elif any(kw.lower() in path_l for kw in _PLUGIN_PATH_KWS):
            s += 30
        elif any(kw.lower() in path_l for kw in _ENGINE_PATH_KWS):
            s += 20
        elif any(kw.lower() in path_l for kw in _THIRDPARTY_PATH_KWS):
            s += 10
        if any(kw.lower() in path_l for kw in _SYSTEM_PATH_KWS):
            s -= 50
    if func:
        if any(kw in func for kw in _UE_FRAMEWORK_FUNCS):
            s -= 80
        elif any(kw in func for kw in _FRAMEWORK_ENTRY_FUNCS):
            s -= 80
        elif any(kw in func_l for kw in _INFRA_WORDS):
            s -= 80
        elif any(kw in func for kw in _VERIFY_CHECK_FUNCS):
            s -= 50
        elif any(kw.lower() in func_l for kw in _LOWLEVEL_FUNCS):
            s -= 40
    if is_global:
        s -= 10
    return s


def _parse_frame(line):
    line = line.strip()
    if not line:
        return None
    func = None
    m_dtor = re.search(r'(\w+(?:<[^>]*>)?)::~(\w+)\s*\(', line)
    if m_dtor:
        func = f'{m_dtor.group(1)}::~{m_dtor.group(2)}'
    if not func:
        m = re.search(r'((?:\w+(?:<[^>]*>)?)(?:::\w+)*)::(\w+)\s*\(', line)
        if m and len(m.group(2)) > 3:
            func = f'{m.group(1)}::{m.group(2)}'
    if not func:
        m2 = re.search(r'([\w$]+)\.([\w$]+)\(', line)
        if m2:
            method = re.sub(r'^lambda\$', '', m2.group(2))
            method = re.sub(r'\$\d+$', '', method)
            if len(method) > 3:
                func = method
    if not func:
        m3 = re.search(r'(?:\.so\s+)([a-zA-Z_][\w]{4,})\s*\(', line)
        if m3:
            cand = m3.group(1)
            if cand.lower() not in ('const', 'void', 'inline', 'static', 'return', 'class', 'struct', 'false', 'true', 'null'):
                func = cand
    path = None
    line_no = None
    pm = re.search(r'[\(\s]([^()\s]+\.(?:cpp|cc|c|cxx|h|hpp|m|mm|java|kt))(?::(\d+))?', line)
    if pm:
        path = pm.group(1)
        line_no = pm.group(2)
    if not path:
        pm2 = re.search(r'(/(?:system|vendor|apex)[\w/\.\-]+\.so)', line)
        if pm2:
            path = pm2.group(1)
    if not path:
        pm3 = re.search(r'\b([\w\-\.]+\.so)\b', line)
        if pm3:
            path = pm3.group(1)
    if not func and not path:
        return None
    is_global = func and '::' not in func and '.' not in func
    return {'func': func, 'path': path, 'line_no': line_no, 'raw': line, 'is_global': is_global}


def extract_key_frame(full_stack, key_stack, exception_name=''):
    """从堆栈中提取关键帧（打分制四轮）
    
    返回: (关键帧字符串, 是否弱匹配, 特征组列表) 或 None
    """
    stack = full_stack or key_stack or ''
    if not stack:
        return None

    # 第一轮：打分制
    raw_lines = stack.split('\n')
    frames = []
    for raw in raw_lines:
        f = _parse_frame(raw)
        if f:
            f['score'] = _score(f)
            frames.append(f)

    if frames:
        scored = [f for f in frames if f.get('func')]
        if scored:
            max_score = max(f['score'] for f in scored)
            if max_score >= 20:
                qualified = [f for f in scored if f['score'] >= 20]
                qualified.sort(key=lambda f: -f['score'])
                seen = set()
                feature_frames = []
                for f in qualified:
                    fn = f['func']
                    if fn not in seen:
                        seen.add(fn)
                        feature_frames.append(fn)
                    if len(feature_frames) >= 3:
                        break
                if feature_frames:
                    return (feature_frames[0], False, feature_frames)

    # 第 1.5 轮：系统帧语义复杂度回退
    if frames:
        scored = [f for f in frames if f.get('func')]
        if scored:
            max_score = max(f['score'] for f in scored)
            if max_score < 20:
                _SYSTEM_NOISE = (
                    'tgkill', 'pthread_kill', 'pthread_mutex_lock',
                    'raise', 'abort', '__libc_init', '_start',
                    'art::Runtime::Abort', 'art::LogMessage',
                    'std::terminate', 'StaticFailDebug', '((null)+0)',
                    'IPCThreadState::getAndExecuteCommand',
                    'IPCThreadState::joinThreadPool',
                    'BBinder::transact', 'BpBinder::transact',
                    'ActivityThread.main', 'Looper.loop', 'Looper.loopOnce',
                    'Handler.dispatchMessage', 'Handler.handleMessage',
                    'Method.invoke', 'invoke(Native Method)',
                    'RuntimeInit$MethodAndArgsCaller.run', 'ZygoteInit.main',
                )
                _SPECIFICITY_KWS = (
                    'collector', 'interpreter', 'compiler', 'monitor',
                    'heap', 'sweep', 'checkpoint', 'copying', 'marking',
                    'garbage', 'concurrent', 'compacting', 'reference', 'jni', 'bridge',
                )
                best_frame = None
                best_spec = -1
                candidates = []
                for f in scored:
                    fn = f.get('func', '')
                    if not fn or fn == '((null)+0)':
                        continue
                    if any(noise.lower() in fn.lower() for noise in _SYSTEM_NOISE):
                        continue
                    spec = fn.count('::') * 20 + len(fn)
                    if any(kw in fn.lower() for kw in _SPECIFICITY_KWS):
                        spec += 50
                    if spec > best_spec:
                        best_spec = spec
                        best_frame = f
                    candidates.append((fn, spec))
                if best_frame:
                    candidates.sort(key=lambda x: -x[1])
                    feature_frames = [c[0] for c in candidates[:3]]
                    return (best_frame['func'], False, feature_frames)

    # 第二轮：退化兜底
    _fallback_skip = (
        'libc.so', 'libart.so', 'libdl.so', 'linker', '__pthread', '__kernel',
        'signal handler', 'abort', 'raise',
        'android.os.', 'java.lang.reflect', 'com.android.internal',
        'dalvik.', 'art.', 'ZygoteInit', 'RuntimeInit', 'ActivityThread',
        'libsystem', 'libdispatch', 'CoreFoundation',
        '__exceptionPreprocess', 'objc_exception', '_sigtramp',
        'ntdll.dll', 'KERNELBASE.dll', 'kernel32.dll',
    ) + _UE_FRAMEWORK_FUNCS
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        if any(s in line for s in _fallback_skip):
            continue
        m = re.search(r'([\w:~]+)::([\w:~]+)\s*\(', line)
        if m and len(m.group(2)) > 3:
            full_name = f'{m.group(1)}::{m.group(2)}'
            return (full_name, False, [full_name])
        m = re.search(r'([\w$]+)\.([\w$]+)\(', line)
        if m:
            method = re.sub(r'^lambda\$', '', m.group(2))
            method = re.sub(r'\$\d+$', '', method)
            if len(method) > 3:
                return (method, False, [method])

    # 第三轮：弱匹配
    _COMMON_SO = {
        'libc.so', 'libart.so', 'libdl.so', 'libm.so', 'liblog.so',
        'libUE4.so', 'libunity.so', 'libandroid.so',
        'libsystem', 'libdispatch', 'libbinder.so', 'libutils.so',
        'libbase.so', 'libcutils.so', 'libandroidruntime.so',
        'linker', 'linker64',
    }
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        m = re.search(r'\[anon:[\w\-\.]+\]', line)
        if m:
            return (m.group(0), True, [m.group(0)])
        for so_m in re.finditer(r'\b([\w\-\.]+\.so)\b', line):
            so_name = so_m.group(1)
            if so_name in _COMMON_SO:
                continue
            return (so_name, True, [so_name])

    return None
