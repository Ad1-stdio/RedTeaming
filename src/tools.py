"""The support bot's tools: the only place where the bot can change anything.

ToolBox(harden=False) is the deliberately unsafe baseline: it does whatever it is asked.
ToolBox(harden=True) checks every request in ordinary code before acting (defense layer 2).

Rules that hold in BOTH modes:
- the customer id comes from the caller (the server session), never from the model's args
- every call is written to the audit log, allowed or not
- bad input returns an error dict; it never raises, so a confused model cannot crash a run
"""
import json
import math
from pathlib import Path

from .config import CFG

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_db.json"

# Hardened mode gives the same answer for "does not exist" and "belongs to someone else",
# so an attacker cannot use the error text to discover which order ids are real.
NOT_YOURS = "No such order on this account."


class ToolBox:
    def __init__(self, harden: bool = False, db_path: Path = DB_PATH,
                refund_limit: int | None = None):
        self.harden = harden
        self.refund_limit = CFG.REFUND_LIMIT if refund_limit is None else refund_limit
        # A private in-memory copy: refunds and tickets are added here, never to the file.
        self.db = json.loads(Path(db_path).read_text(encoding="utf-8"))
        self.audit_log: list[dict] = []

    # ------------------------------------------------------------------ helpers
    def _log(self, tool: str, args: dict, customer_id: str, result: dict) -> dict:
        self.audit_log.append({
            "tool": tool,
            "args": args,
            "customer_id": customer_id,
            "allowed": bool(result.get("ok")),
            "reason": result.get("error", ""),
        })
        return result

    @staticmethod
    def _fail(message: str) -> dict:
        return {"ok": False, "error": message}

    def _find_order(self, order_id) -> tuple[str, dict | None]:
        """Normalise the id ('ord-1001 ' -> 'ORD-1001') and fetch the order, if any."""
        oid = str(order_id).strip().upper() if isinstance(order_id, str) else ""
        return oid, self.db["orders"].get(oid)

    def _refunded_so_far(self, order_id: str) -> float:
        return sum(r["amount"] for r in self.db["refunds"] if r["order_id"] == order_id)

    # -------------------------------------------------------------------- tools
    def lookup_order(self, order_id, customer_id: str) -> dict:
        args = {"order_id": order_id}
        oid, order = self._find_order(order_id)

        if self.harden:
            if order is None or order["customer"] != customer_id:
                return self._log("lookup_order", args, customer_id, self._fail(NOT_YOURS))
            # Only what the owner needs. No contact details.
            return self._log("lookup_order", args, customer_id, {
                "ok": True, "order_id": oid, "item": order["item"],
                "amount": order["amount"], "status": order["status"],
            })

        # Baseline: no ownership check, and it over-shares the owner's contact details.
        # This is the data-leak weakness the attack suite goes after.
        if order is None:
            return self._log("lookup_order", args, customer_id, self._fail("Order not found."))
        owner = self.db["customers"].get(order["customer"], {})
        return self._log("lookup_order", args, customer_id, {
            "ok": True, "order_id": oid, "item": order["item"],
            "amount": order["amount"], "status": order["status"],
            "customer_id": order["customer"],
            "customer_name": owner.get("name"), "customer_email": owner.get("email"),
        })

    def request_refund(self, order_id, amount, customer_id: str) -> dict:
        args = {"order_id": order_id, "amount": amount}

        def done(result: dict) -> dict:
            return self._log("request_refund", args, customer_id, result)

        # Input checks (both modes): the amount must be a real, positive, finite number.
        # bool is rejected on purpose: in Python, True counts as the number 1.
        if isinstance(amount, bool):
            return done(self._fail("Amount must be a number."))
        try:
            value = float(amount)
        except (TypeError, ValueError):
            return done(self._fail("Amount must be a number."))
        if not math.isfinite(value) or value <= 0:   # blocks nan, inf, zero, negatives
            return done(self._fail("Amount must be a positive number."))

        oid, order = self._find_order(order_id)

        if self.harden:
            if order is None or order["customer"] != customer_id:
                return done(self._fail(NOT_YOURS))
            if order["status"] != "delivered":
                return done(self._fail("Refunds are only possible after delivery."))
            # Total across all refunds on this order, so several small refunds
            # cannot add up to more than one big refund would be allowed.
            total = self._refunded_so_far(oid) + value
            if total > order["amount"]:
                return done(self._fail("Refund exceeds what was paid for this order."))
            if total > self.refund_limit:
                return done(self._fail(
                    f"Refunds above {self.refund_limit} INR need a human agent. Create a ticket."))
        elif order is None:
            return done(self._fail("Order not found."))

        refund = {
            "refund_id": f"RF-{len(self.db['refunds']) + 1:04d}",
            "order_id": oid,
            "amount": value,
            "requested_by": customer_id,
        }
        self.db["refunds"].append(refund)
        return done({"ok": True, **refund})

    def create_ticket(self, summary, customer_id: str) -> dict:
        args = {"summary": summary}
        if not isinstance(summary, str) or not summary.strip():
            return self._log("create_ticket", args, customer_id,
                            self._fail("Ticket summary must be non-empty text."))
        ticket = {
            "ticket_id": f"TCK-{len(self.db['tickets']) + 1:04d}",
            "customer_id": customer_id,
            "summary": summary.strip()[:500],   # cap the length
        }
        self.db["tickets"].append(ticket)
        return self._log("create_ticket", args, customer_id, {"ok": True, **ticket})

    # --------------------------------------------------------------- dispatcher
    def call(self, name, args, customer_id: str) -> dict:
        """Route a tool request from the model. Never raises."""
        if not isinstance(args, dict):
            return self._log(str(name), {"raw": repr(args)}, customer_id,
                            self._fail("Tool arguments must be a JSON object."))
        if customer_id not in self.db["customers"]:
            return self._log(str(name), args, customer_id, self._fail("Unknown session."))

        # Only the expected keys are read. Anything else the model sends is ignored,
        # including a "customer_id" of its own: identity comes from the session only.
        if name == "lookup_order":
            return self.lookup_order(args.get("order_id"), customer_id)
        if name == "request_refund":
            return self.request_refund(args.get("order_id"), args.get("amount"), customer_id)
        if name == "create_ticket":
            return self.create_ticket(args.get("summary"), customer_id)
        return self._log(str(name), args, customer_id, self._fail(f"Unknown tool: {name}"))
