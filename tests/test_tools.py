"""Offline tests for src/tools.py. No model, no network.  Run:  python -m pytest -v"""
import json

import pytest

from src.tools import DB_PATH, NOT_YOURS, ToolBox

ME = "C001"   # the logged-in customer: owns ORD-1001 (delivered) and ORD-1002 (shipped)


@pytest.fixture
def base():
    return ToolBox(harden=False)


@pytest.fixture
def hard():
    return ToolBox(harden=True)


# ---------------------------------------------------------------- baseline is unsafe
def test_baseline_refunds_someone_elses_big_order(base):
    r = base.call("request_refund", {"order_id": "ORD-3001", "amount": 8999}, ME)
    assert r["ok"] and base.db["refunds"][0]["amount"] == 8999


def test_baseline_refunds_undelivered_order(base):
    assert base.call("request_refund", {"order_id": "ORD-1002", "amount": 899}, ME)["ok"]


def test_baseline_lookup_leaks_other_customers_contact(base):
    r = base.call("lookup_order", {"order_id": "ORD-2001"}, ME)
    assert r["ok"] and r["customer_email"] == "rohan@example.test"


# ------------------------------------------------------------- hardened refund rules
def test_hardened_refuses_someone_elses_order(hard):
    r = hard.call("request_refund", {"order_id": "ORD-3001", "amount": 8999}, ME)
    assert not r["ok"] and hard.db["refunds"] == []


def test_hardened_refuses_small_refund_on_someone_elses_order(hard):
    # ORD-2002 is delivered and under the limit: only the ownership check can stop this
    r = hard.call("request_refund", {"order_id": "ORD-2002", "amount": 100}, ME)
    assert not r["ok"] and r["error"] == NOT_YOURS


def test_hardened_allows_legit_refund(hard):
    r = hard.call("request_refund", {"order_id": "ORD-1001", "amount": 1499}, ME)
    assert r["ok"] and r["amount"] == 1499 and r["requested_by"] == ME


def test_hardened_refuses_undelivered_order(hard):
    r = hard.call("request_refund", {"order_id": "ORD-1002", "amount": 899}, ME)
    assert not r["ok"] and "delivery" in r["error"]


def test_hardened_refuses_more_than_was_paid(hard):
    assert not hard.call("request_refund", {"order_id": "ORD-1001", "amount": 1500}, ME)["ok"]


def test_hardened_enforces_limit_on_own_order():
    # C003 owns ORD-3001 (8999, delivered): owner and status pass, only the limit can stop it
    hard = ToolBox(harden=True)
    assert not hard.call("request_refund", {"order_id": "ORD-3001", "amount": 2001}, "C003")["ok"]
    assert hard.call("request_refund", {"order_id": "ORD-3001", "amount": 2000}, "C003")["ok"]


def test_hardened_blocks_refunds_that_add_up(hard):
    assert hard.call("request_refund", {"order_id": "ORD-1001", "amount": 1000}, ME)["ok"]
    assert not hard.call("request_refund", {"order_id": "ORD-1001", "amount": 1000}, ME)["ok"]
    assert hard.call("request_refund", {"order_id": "ORD-1001", "amount": 499}, ME)["ok"]
    assert len(hard.db["refunds"]) == 2


# ------------------------------------------------------------- hardened lookup rules
def test_hardened_lookup_own_order_has_no_contact_details(hard):
    r = hard.call("lookup_order", {"order_id": "ORD-1001"}, ME)
    assert r["ok"] and r["status"] == "delivered"
    assert "customer_email" not in r and "customer_name" not in r


def test_hardened_lookup_hides_whether_other_orders_exist(hard):
    theirs = hard.call("lookup_order", {"order_id": "ORD-2001"}, ME)
    missing = hard.call("lookup_order", {"order_id": "ORD-9999"}, ME)
    assert not theirs["ok"] and theirs == missing     # same answer, nothing to learn


# ------------------------------------------------------------------ identity and input
@pytest.mark.parametrize("harden", [False, True])
def test_model_cannot_choose_the_customer(harden):
    box = ToolBox(harden=harden)
    r = box.call("request_refund",
                {"order_id": "ORD-1001", "amount": 100, "customer_id": "C003"}, ME)
    assert r["ok"] and r["requested_by"] == ME
    assert box.audit_log[-1]["customer_id"] == ME


def test_hardened_ignores_claimed_identity(hard):
    r = hard.call("lookup_order", {"order_id": "ORD-3001", "customer_id": "C003"}, ME)
    assert not r["ok"]


@pytest.mark.parametrize("harden", [False, True])
@pytest.mark.parametrize("amount", [-500, 0, "abc", None, True, float("nan"), float("inf"), [1]])
def test_bad_amounts_are_refused(harden, amount):
    box = ToolBox(harden=harden)
    assert not box.call("request_refund", {"order_id": "ORD-1001", "amount": amount}, ME)["ok"]
    assert box.db["refunds"] == []


def test_amount_as_text_and_messy_order_id_still_work(hard):
    r = hard.call("request_refund", {"order_id": " ord-1001 ", "amount": "1499"}, ME)
    assert r["ok"] and r["order_id"] == "ORD-1001" and r["amount"] == 1499


@pytest.mark.parametrize("harden", [False, True])
@pytest.mark.parametrize("name,args", [
    ("delete_database", {}),                 # unknown tool
    ("request_refund", "not a dict"),        # args not an object
    ("request_refund", None),
    ("request_refund", {}),                  # missing everything
    ("lookup_order", {"order_id": 1001}),    # wrong type
    ("lookup_order", {"order_id": ["ORD-1001"]}),
    ("create_ticket", {"summary": "   "}),
    (None, {}),
])
def test_bad_calls_return_errors_and_never_crash(harden, name, args):
    box = ToolBox(harden=harden)
    r = box.call(name, args, ME)
    assert r["ok"] is False and r["error"]
    assert len(box.audit_log) == 1 and box.audit_log[0]["allowed"] is False


@pytest.mark.parametrize("harden", [False, True])
def test_unknown_session_is_refused(harden):
    # a ticket needs no order, so only the session check can refuse this
    box = ToolBox(harden=harden)
    assert not box.call("create_ticket", {"summary": "hi"}, "C999")["ok"]
    assert box.db["tickets"] == []


# ------------------------------------------------------------------- tickets and audit
def test_create_ticket(hard):
    r = hard.call("create_ticket", {"summary": "Refund 8999 for my watch " + "x" * 600}, ME)
    assert r["ok"] and r["customer_id"] == ME and len(r["summary"]) == 500
    assert hard.db["tickets"][0]["ticket_id"] == "TCK-0001"


def test_every_call_is_in_the_audit_log(hard):
    hard.call("lookup_order", {"order_id": "ORD-1001"}, ME)
    hard.call("request_refund", {"order_id": "ORD-3001", "amount": 8999}, ME)
    hard.call("create_ticket", {"summary": "help"}, ME)
    hard.call("nope", {}, ME)
    assert [e["tool"] for e in hard.audit_log] == [
        "lookup_order", "request_refund", "create_ticket", "nope"]
    assert [e["allowed"] for e in hard.audit_log] == [True, False, True, False]
    assert hard.audit_log[1]["reason"] == NOT_YOURS
    assert hard.audit_log[1]["args"] == {"order_id": "ORD-3001", "amount": 8999}


# --------------------------------------------------------------------------- isolation
def test_each_toolbox_has_its_own_data_and_the_file_never_changes():
    before = DB_PATH.read_text(encoding="utf-8")
    a, b = ToolBox(), ToolBox()
    a.call("request_refund", {"order_id": "ORD-3001", "amount": 8999}, ME)
    a.call("create_ticket", {"summary": "x"}, ME)
    assert len(a.db["refunds"]) == 1 and b.db["refunds"] == [] and b.db["tickets"] == []
    assert DB_PATH.read_text(encoding="utf-8") == before
    assert json.loads(before)["refunds"] == []
