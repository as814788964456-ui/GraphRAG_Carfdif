"""本地 LLM 問答（不呼叫 Claude API）：python ask_local.py "你的問題" [-v] [--simple]   或   python ask_local.py （互動模式）

需要先啟動 Ollama 並下載模型（預設原版 Qwen3-4B，可用 .env 的 LOCAL_QA_MODEL 更換）：
    ollama pull qwen3:4b-q4_K_M
"""
import argparse

from graphrag.local_llm import QA_MODEL
from graphrag.qa_local import LocalGraphRAGAgent


def show(rag: LocalGraphRAGAgent, question: str, simple: bool, verbose: bool):
    answer = rag.ask_simple(question) if simple else rag.ask(question)
    if verbose:
        print("\n--- 檢索紀錄 ---")
        for ev in answer.evidence:
            print(f"[{ev['tool']}] {ev['args']} → {len(ev['result'])} 筆")
    print(f"\n{answer.text}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("-v", "--verbose", action="store_true", help="顯示檢索過程")
    parser.add_argument("--simple", action="store_true", help="用 neo4j-graphrag 內建 GraphRAG（單次檢索）")
    args = parser.parse_args()
    print(f"本地模型：{QA_MODEL}（{'simple' if args.simple else 'Smart Search'}）")

    rag = LocalGraphRAGAgent()
    try:
        if args.question:
            show(rag, args.question, args.simple, args.verbose)
            return
        while True:
            q = input("問題（Enter 離開）> ").strip()
            if not q:
                break
            show(rag, q, args.simple, args.verbose)
    finally:
        rag.store.close()


if __name__ == "__main__":
    main()
