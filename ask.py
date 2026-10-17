"""問答：python ask.py "你的問題" [-v] [--simple]   或   python ask.py （互動模式）"""
import argparse

from graphrag import config
from graphrag.qa import GraphRAGAgent


def show(rag: GraphRAGAgent, question: str, simple: bool, verbose: bool):
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
    if config.QA_PROVIDER == "ollama" and not args.simple:
        print("QA_PROVIDER=ollama：Smart Search 需要 Claude，改用 --simple 模式。")
        args.simple = True

    rag = GraphRAGAgent()
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
