#!/usr/bin/env python3
"""CrashSight Agent CLI — 命令行交互入口（LangGraph 版）"""
from crashsight_agent.orchestration.agent import CrashSightAgent


def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   CrashSight 崩溃分析 Agent (LangGraph)  ║")
    print("╠══════════════════════════════════════════╣")
    print("║  示例:                                   ║")
    print("║    安卓体验服 3.7 昨天的崩溃             ║")
    print("║    Top1 正式服有没有                     ║")
    print("║    iOS 最近一周崩溃率趋势                ║")
    print("║                                          ║")
    print("║  输入 quit 退出 | reset 重置对话         ║")
    print("╚══════════════════════════════════════════╝")
    print()

    agent = CrashSightAgent()

    while True:
        try:
            query = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not query:
            continue
        if query.lower() in ('quit', 'exit', 'q'):
            print("再见！")
            break
        if query.lower() == 'reset':
            agent.reset()
            print("[已重置对话]\n")
            continue

        print()
        answer = agent.chat(query)
        print(f"助手: {answer}\n")


if __name__ == '__main__':
    main()
