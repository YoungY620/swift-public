from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_trade_intents.py"
SPEC = importlib.util.spec_from_file_location("validate_trade_intents", MODULE_PATH)
assert SPEC and SPEC.loader
trade_intents = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trade_intents)


CONDITION_A = "0x" + "a" * 64
CONDITION_B = "0x" + "b" * 64


def intent(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "1.0",
        "venue": "polymarket",
        "multi_market_identifier": CONDITION_A,
        "instrument_identifier": "123456789",
        "outcome": "yes",
        "side": "buy",
        "reference_price": "0.5",
        "notional": "5",
        "quantity": "10",
        "currency": "pUSD",
    }
    value.update(overrides)
    return value


def snapshot() -> dict[str, object]:
    return {
        "collateral": {"asset": "pUSD"},
        "positions": {
            "count": 1,
            "positions": [
                {
                    "conditionId": CONDITION_B,
                    "asset": "987654321",
                    "outcome": "No",
                    "size": 4,
                }
            ],
        },
    }


class TradeIntentTests(unittest.TestCase):
    def run_validator(
        self,
        values: list[dict[str, object]],
        account_snapshot: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object] | None]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            jsonl = directory / "trade-intents.jsonl"
            jsonl.write_text(
                "".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values),
                encoding="utf-8",
            )
            arguments = [
                str(jsonl),
                "--schema",
                str(ROOT / "schemas" / "trade-intent.schema.json"),
            ]
            if account_snapshot is not None:
                snapshot_path = directory / "account.json"
                snapshot_path.write_text(json.dumps(account_snapshot), encoding="utf-8")
                arguments.extend(["--polymarket-account-snapshot", str(snapshot_path)])
            try:
                with redirect_stdout(io.StringIO()):
                    code = trade_intents.main(arguments)
            except trade_intents.TradeIntentError:
                return 2, None
            return code, {"items": len(values)}

    def test_accepts_buy_and_held_sell(self) -> None:
        values = [
            intent(),
            intent(
                multi_market_identifier=CONDITION_B,
                instrument_identifier="987654321",
                outcome="no",
                side="sell",
                reference_price="0.4",
                notional="1.2",
                quantity="3",
            ),
        ]
        code, _ = self.run_validator(values, snapshot())
        self.assertEqual(code, 0)

    def test_accepts_zero_byte_file_as_no_actions(self) -> None:
        code, _ = self.run_validator([], snapshot())
        self.assertEqual(code, 0)

    def test_rejects_schema_extra_field(self) -> None:
        code, _ = self.run_validator([intent(comment="not allowed")], snapshot())
        self.assertEqual(code, 2)

    def test_rejects_notional_mismatch(self) -> None:
        code, _ = self.run_validator([intent(notional="6")], snapshot())
        self.assertEqual(code, 2)

    def test_rejects_sell_above_real_holding(self) -> None:
        value = intent(
            multi_market_identifier=CONDITION_B,
            instrument_identifier="987654321",
            outcome="no",
            side="sell",
            reference_price="0.4",
            notional="2",
            quantity="5",
        )
        code, _ = self.run_validator([value], snapshot())
        self.assertEqual(code, 2)

    def test_rejects_wrong_active_collateral(self) -> None:
        code, _ = self.run_validator([intent(currency="USDC")], snapshot())
        self.assertEqual(code, 2)

    def test_rejects_duplicate_instrument(self) -> None:
        code, _ = self.run_validator([intent(), intent(side="sell")], snapshot())
        self.assertEqual(code, 2)

    def test_aligns_items_with_actionable_private_report_entries(self) -> None:
        report = {
            "schema_version": "3.0",
            "actions": [
                {
                    "action": "BUY",
                    "outcome": "YES",
                    "execution": {"vwap": 0.5, "gross_cash": 5, "shares": 10},
                },
                {
                    "action": "MAINTAIN",
                    "outcome": "NO",
                    "execution": None,
                },
            ],
        }
        trade_intents.validate_report_alignment([intent()], report)

        with self.assertRaisesRegex(trade_intents.TradeIntentError, "no matching JSONL item"):
            trade_intents.validate_report_alignment([], report)

    def test_empty_items_require_an_actionless_private_report(self) -> None:
        report = {
            "schema_version": "3.0",
            "actions": [{"action": "MAINTAIN", "outcome": "YES", "execution": None}],
        }
        trade_intents.validate_report_alignment([], report)


if __name__ == "__main__":
    unittest.main()
