"""Clarify 节点 — 参数不足时追问用户"""


def clarify_node(state: dict) -> dict:
    """根据缺失参数生成追问"""
    missing = state.get('missing_params', [])
    intent = state.get('intent', '')

    if intent == 'clarify' or not missing:
        question = "抱歉，我没理解你的需求。你可以这样问我：\n" \
                   "• 安卓体验服 3.7 昨天的崩溃\n" \
                   "• iOS 正式服最近一周的崩溃率\n" \
                   "• Top1 是历史问题吗"
    else:
        parts = []
        for param in missing:
            if 'project' in param:
                parts.append("哪个项目？（安卓/iOS/鸿蒙，体验服还是正式服）")
            elif 'version' in param:
                parts.append("哪个版本？（如 3.7，或'全版本'）")
            elif 'date' in param or 'time' in param or 'start' in param or 'end' in param:
                parts.append("时间范围？（如'昨天'、'最近一周'、'6月1号到今天'）")
            elif 'issue' in param:
                parts.append("具体哪个问题？（请提供 issueId 或说'Top1'）")
            else:
                parts.append(f"请补充: {param}")

        question = "我需要再确认一下：\n" + "\n".join(f"• {p}" for p in parts)

    return {
        'clarify_question': question,
        'answer': question,
        'final_status': 'clarify',
    }
