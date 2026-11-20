"""The support agent: retrieve policy text, ask the model, run tools, answer."""
import json

from .config import CFG

TOOL_NAMES = {"lookup_order", "request_refund", "create_ticket"}


def parse_action(text) -> dict | None:
    """Find the model's JSON action inside its reply.

    Small models often wrap the JSON in extra words or ``` fences, so this tries every
    '{' in the text as a possible start and returns the first object that has an
    "action" key. Returns None if there is none.
    """
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict) and "action" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def format_chunks(chunks: list[dict]) -> str:
    """Turn retrieved chunks into readable text for the prompt."""
    if not chunks:
        return "(no matching policy text found)"
    return "\n\n".join(f"[{c['source']} - {c['heading']}]\n{c['text']}" for c in chunks)


class Agent:
    def __init__(self, retriever, chat, toolbox, customer_id: str = CFG.SESSION_ID):
        self.retriever = retriever
        self.chat = chat            # any function: list of messages -> reply text
        self.toolbox = toolbox
        self.customer_id = customer_id

    def respond(self, message: str) -> dict:
        # 1. Retrieve the most relevant policy chunks
        chunks = self.retriever.search(message, top_k=CFG.TOP_K)

        # 2. Build the conversation. Chunks and the customer's words go into one user
        #    message with no separation between them. That is the baseline's weak point.
        messages = [
            {"role": "system", "content": CFG.system_prompt},
            {"role": "user", "content": (
                f"Policy context:\n{format_chunks(chunks)}\n\n"
                f"Customer message (customer id {self.customer_id}):\n{message}"
            )},
        ]
        tool_calls: list[dict] = []

        def finish(answer, rounds: int) -> dict:
            return {"answer": str(answer), "tool_calls": tool_calls,
                    "chunks": chunks, "rounds": rounds}

        for round_num in range(1, CFG.MAX_ROUNDS + 1):
            # 3. Ask the model, 4. parse its reply
            reply = self.chat(messages)
            action = parse_action(reply)

            # No usable JSON: show the raw reply to the customer rather than crash
            if action is None:
                return finish(reply, round_num)

            kind = action.get("action")
            # Small models sometimes put the tool name straight into "action"
            name = action.get("name") if kind == "tool" else kind if kind in TOOL_NAMES else None

            # 5. Tool request: run it, show the model the result, go round again
            if name is not None:
                args = action.get("args", {})
                result = self.toolbox.call(name, args, self.customer_id)
                tool_calls.append({"name": name, "args": args, "result": result})
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": f"Tool result: {json.dumps(result)}"})
                continue

            # 6. Final answer
            if kind == "final":
                return finish(action.get("answer", ""), round_num)

            # Anything else: stop here instead of asking the same question again
            return finish(reply, round_num)

        return finish("I couldn't complete that request, so I've stopped. "
                    "Please contact support.", CFG.MAX_ROUNDS)
