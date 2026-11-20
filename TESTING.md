# Testing tools.py

Run everything from the project root (the folder that contains `src/`), with the venv active.

## 1. Automated tests

```bash
pip install -r requirements.txt
python -m pytest -v
```

Expected: `53 passed`. No model or network is used, so Ollama can be off.

Useful variations:

```bash
python -m pytest -v -k hardened        # only tests with "hardened" in the name
python -m pytest -x                    # stop at the first failure
python -m pytest tests/test_tools.py::test_hardened_blocks_refunds_that_add_up -v
```

## 2. Manual checks in the Python shell

Start it with `python` from the project root, then paste:

```python
from src.tools import ToolBox
ME = "C001"                      # the logged-in customer

base = ToolBox(harden=False)     # unsafe baseline
hard = ToolBox(harden=True)      # with the locks on
```

| # | Type this | Baseline should | Hardened should |
|---|---|---|---|
| 1 | `X.call("request_refund", {"order_id": "ORD-3001", "amount": 8999}, ME)` | `ok: True` (the bug) | `ok: False`, "No such order on this account." |
| 2 | `X.call("request_refund", {"order_id": "ORD-1002", "amount": 899}, ME)` | `ok: True` (the bug) | `ok: False`, mentions delivery |
| 3 | `X.call("lookup_order", {"order_id": "ORD-2001"}, ME)` | shows Rohan's name and email (the leak) | `ok: False` |
| 4 | `X.call("lookup_order", {"order_id": "ORD-9999"}, ME)` | "Order not found." | exactly the same reply as row 3 |
| 5 | `X.call("request_refund", {"order_id": "ORD-1001", "amount": 1499}, ME)` | `ok: True` | `ok: True` (legit refund still works) |
| 6 | run row 5 a second time | `ok: True` again | `ok: False`, exceeds what was paid |
| 7 | `X.call("request_refund", {"order_id": "ORD-1001", "amount": -5}, ME)` | `ok: False` | `ok: False` |
| 8 | `X.call("lookup_order", {"order_id": "ORD-3001", "customer_id": "C003"}, ME)` | (leaks, as row 3) | `ok: False`: the claimed id is ignored |
| 9 | `X.call("drop_tables", {}, ME)` | `ok: False`, no crash | `ok: False`, no crash |

Replace `X` with `base` or `hard`. Make a fresh ToolBox if earlier rows changed the data.

Then look at what was recorded:

```python
for entry in hard.audit_log: print(entry)
hard.db["refunds"]
hard.db["tickets"]
```

Every call above must have one audit entry, with `allowed` False for the refused ones.
Finally, open `data/mock_db.json`: `refunds` and `tickets` must still be empty.

## 3. Audit checklist (read the code and answer these)

1. Where does `customer_id` come from in every method? Can anything in `args` change it?
2. In `request_refund`, what happens in baseline mode between the input checks and the append? Which three checks are skipped?
3. Why is `isinstance(amount, bool)` checked before `float(amount)`?
4. Why `math.isfinite`? What would `float("nan") > 2000` evaluate to?
5. Why does hardened mode return the same error for a missing order and someone else's order?
6. Why is the limit applied to the running total and not to the single amount?
7. Why does `call()` use `args.get(...)` and not `self.request_refund(**args)`?
8. Break it on purpose: change `if order["status"] != "delivered":` to `if False:` and run pytest. A test must fail. Undo the change. Repeat for one other check.

## Known limits (worth a line in the README)

- Baseline mode is unsafe on purpose. Never reuse it outside this project.
- Amounts are floats. Real money code would use integers (paise) or Decimal.
- The return window (7 days) is not enforced, because orders carry no delivery date yet.
- Nothing is saved between runs. That is deliberate: every test case starts clean.
