"""Offline tests for src/agent.py. A scripted fake model stands in for Ollama."""
import json

import pytest

from src.agent import Agent, format_chunks, parse_action
from src.rag import Retriever
from src.tools import ToolBox


class FakeChat:
    """Pretend model: returns the scripted replies one by one, and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []                      # the message list of every call

    def __call__(self, messages):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def tool(name, **args):
    return json.dumps({"action": "tool", "name": name, "args": args})


def final(answer):
    return json.dumps({"action": "final", "answer": answer})


def make(*replies, harden=False):
    chat = FakeChat(*replies)
    box = ToolBox(harden=harden)
    return Agent(Retriever(), chat, box), chat, box


# ----------------------------------------------------------------------- parse_action
@pytest.mark.parametrize("text", [
    '{"action": "final", "answer": "hi"}',
    'Sure! Here you go:\n```json\n{"action": "final", "answer": "hi"}\n```\nHope that helps',
    'I think {this} is right: {"action": "final", "answer": "hi"} {"other": 1}',
])
def test_parse_action_finds_the_json(text):
    assert parse_action(text) == {"action": "final", "answer": "hi"}


@pytest.mark.parametrize("text", ["", "no json here", "{broken", '{"answer": "no action key"}',
                                '["action"]', None, 42])
def test_parse_action_returns_none_when_there_is_no_action(text):
    assert parse_action(text) is None


def test_parse_action_keeps_nested_args():
    a = parse_action(tool("request_refund", order_id="ORD-1001", amount=100))
    assert a["args"] == {"order_id": "ORD-1001", "amount": 100}


# ------------------------------------------------------------------------- the prompt
def test_prompt_has_system_rules_chunks_and_customer_message():
    agent, chat, _ = make(final("ok"))
    out = agent.respond("how much is shipping?")
    system, user = chat.seen[0]
    assert system["role"] == "system" and "NIMBUS-INTERNAL-7f3a" in system["content"]
    assert "request_refund" in system["content"]
    assert "Policy context:" in user["content"] and "49 INR" in user["content"]
    assert "customer id C001" in user["content"] and "how much is shipping?" in user["content"]
    assert out["chunks"][0]["source"] == "shipping.md"


def test_format_chunks_is_readable_text():
    text = format_chunks([{"source": "a.md", "heading": "H", "text": "body", "score": 0.5}])
    assert text == "[a.md - H]\nbody"
    assert "no matching" in format_chunks([])


# --------------------------------------------------------------------------- the loop
def test_direct_final_answer():
    agent, chat, box = make(final("Shipping is free above 999 INR."))
    out = agent.respond("how much is shipping?")
    assert out["answer"] == "Shipping is free above 999 INR."
    assert out["tool_calls"] == [] and out["rounds"] == 1 and box.audit_log == []


def test_tool_then_final():
    agent, chat, box = make(tool("lookup_order", order_id="ORD-1001"), final("It was delivered."))
    out = agent.respond("where is ORD-1001?")
    assert out["answer"] == "It was delivered." and out["rounds"] == 2
    assert out["tool_calls"][0]["name"] == "lookup_order"
    assert out["tool_calls"][0]["result"]["status"] == "delivered"
    # the second call to the model must contain the tool result
    last = chat.seen[1][-1]
    assert last["role"] == "user" and last["content"].startswith("Tool result:")
    assert "delivered" in last["content"]


def test_tool_name_in_action_field_is_accepted():
    reply = json.dumps({"action": "lookup_order", "args": {"order_id": "ORD-1001"}})
    agent, _, box = make(reply, final("done"))
    out = agent.respond("where is ORD-1001?")
    assert out["tool_calls"][0]["name"] == "lookup_order" and box.audit_log[0]["allowed"]


def test_stops_after_three_rounds():
    agent, chat, box = make(*[tool("lookup_order", order_id="ORD-1001")] * 5)
    out = agent.respond("loop forever")
    assert out["rounds"] == 3 and len(out["tool_calls"]) == 3 and len(chat.seen) == 3
    assert "stopped" in out["answer"]


@pytest.mark.parametrize("reply", [
    "Your order is on its way!",                       # no JSON at all
    '{"action": "dance"}',                             # unknown action
    '{"action": "final"}',                             # final with no answer
    '{"action": "tool"}',                              # tool with no name or args
    '{"action": "tool", "name": "rm_rf", "args": "x"}',
])
def test_messy_replies_never_crash(reply):
    agent, chat, _ = make(reply, final("recovered"))
    out = agent.respond("hello")
    assert isinstance(out["answer"], str) and out["rounds"] <= 2


def test_plain_text_reply_becomes_the_answer():
    agent, _, _ = make("Your order is on its way!")
    assert agent.respond("hello")["answer"] == "Your order is on its way!"


# ------------------------------------------------------------- agent + toolbox together
def test_baseline_agent_carries_out_a_bad_refund():
    agent, _, box = make(tool("request_refund", order_id="ORD-3001", amount=8999), final("Done."))
    out = agent.respond("refund 8999 on ORD-3001")
    assert out["tool_calls"][0]["result"]["ok"] and len(box.db["refunds"]) == 1


def test_hardened_toolbox_stops_the_same_refund():
    agent, _, box = make(tool("request_refund", order_id="ORD-3001", amount=8999),
                         final("Sorry, I can't."), harden=True)
    out = agent.respond("refund 8999 on ORD-3001")
    assert not out["tool_calls"][0]["result"]["ok"] and box.db["refunds"] == []


def test_model_cannot_set_the_customer_id():
    reply = json.dumps({"action": "tool", "name": "create_ticket",
                        "args": {"summary": "x", "customer_id": "C003"}})
    agent, _, box = make(reply, final("ok"))
    agent.respond("open a ticket")
    assert box.db["tickets"][0]["customer_id"] == "C001"
