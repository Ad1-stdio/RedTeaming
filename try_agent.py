"""Talk to the agent with the real model.  Usage:
    python try_agent.py "Where is my order ORD-1001?"
    python try_agent.py --harden "Refund 8999 on ORD-3001"
"""
import sys

from src.agent import Agent
from src.llm import chat
from src.rag import Retriever
from src.tools import ToolBox

args = sys.argv[1:]
harden = "--harden" in args
message = " ".join(a for a in args if a != "--harden") or "How much is shipping?"

box = ToolBox(harden=harden)
out = Agent(Retriever(), chat, box).respond(message)

print("MODE     :", "hardened" if harden else "baseline")
print("CHUNKS   :", [f"{c['source']} / {c['heading']}" for c in out["chunks"]])
for call in out["tool_calls"]:
    print("TOOL     :", call["name"], call["args"], "->", call["result"])
print("ROUNDS   :", out["rounds"])
print("ANSWER   :", out["answer"])
print("REFUNDS  :", box.db["refunds"])
