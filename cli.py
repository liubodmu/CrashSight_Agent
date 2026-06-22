#!/usr/bin/env python3
"""CrashSight Agent CLI — 命令行交互入口"""
from crashsight_agent.orchestration.agent import CrashSightAgent


def main():
    print("=" * 50)
    print("  CrashSight 崩溃分析 Agent")
    print("  输入自然语言查询，如:")
    print("    安卓体验服 3.7 昨天的崩溃")
    print("    Top1 正式服有没有")
    print("    对比这周和上周的崩溃率")
    print("  输入 quit 退出，输入 reset 重置对话")
    print("=" * 50)
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
        print(f"\n助手: {answer}\n")


if __name__ == '__main__':
    main()
