#!/usr/bin/env python3
import argparse
from helen_os.adapters.ollama_chat import ollama_chat
from helen_os.memory.memory_kernel import MemoryKernel

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--mem", default="memory/memory.ndjson")
    args = ap.parse_args()

    mem = MemoryKernel(args.mem)
    kv = mem.replay_kv()

    system = (
        "You are HELEN (non-sovereign). You may be reflective/proto-aware in language, "
        "but you must not claim authority (no sealed/approved/verdict/ship). "
        "If the user contradicts memory, ask to confirm."
    )

    print("HELEN (Ollama) online. /exit to quit.")
    while True:
        user = input("\nYou > ").strip()
        if user in ("/exit","/quit"):
            break
        if not user:
            continue

        mem_hint = "Known memory keys: " + ", ".join(sorted(list(kv.keys()))[:30])
        messages = [
            {"role":"system","content": system},
            {"role":"system","content": mem_hint},
            {"role":"user","content": user},
        ]
        reply = ollama_chat(args.model, messages, temperature=0.3)
        print("\nHELEN >", reply)

        mem.append("last_user_message", user, actor="user", status="OBSERVED")
        mem.append("last_helen_reply", reply, actor="assistant", status="OBSERVED")
        kv = mem.replay_kv()

if __name__ == "__main__":
    main()
