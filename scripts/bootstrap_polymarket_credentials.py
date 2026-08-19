#!/usr/bin/env python3
"""Create or derive CLOB credentials without exposing secrets on the command line."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.polymarket.sdk_adapter import (
    EVM_ADDRESS,
    PINNED_SDK_VERSION,
    SIGNATURE_TYPE_BY_WALLET,
    SUPPORTED_WALLET_TYPES,
    _contract_code,
)


class BootstrapError(RuntimeError):
    """Raised when credentials cannot be initialized safely."""


def _private_key() -> str:
    value = os.environ.get("POLYMARKET_SIGNER_PRIVATE_KEY", "").strip()
    if value:
        return value
    if not sys.stdin.isatty():
        raise BootstrapError(
            "run from an interactive terminal or set POLYMARKET_SIGNER_PRIVATE_KEY"
        )
    value = getpass.getpass("Polymarket signer private key (hidden): ").strip()
    if not value:
        raise BootstrapError("signer private key is empty")
    return value


def _write_exclusive(path: Path, value: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created and path.exists():
            path.unlink()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet", required=True, help="Public Polymarket account wallet")
    parser.add_argument(
        "--expected-wallet-type",
        default="AUTO",
        choices=["AUTO", *sorted(SUPPORTED_WALLET_TYPES)],
        help="Fail if SDK wallet classification differs (default: AUTO)",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New mode-0600 JSON file; an existing file is never overwritten",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    private_key = _private_key()
    wallet = args.wallet.strip()
    if not EVM_ADDRESS.fullmatch(wallet):
        raise BootstrapError("--wallet is not an EVM address")

    try:
        from eth_account import Account
        from polymarket import SecureClient
    except ImportError as exc:
        raise BootstrapError("polymarket-client is not installed") from exc
    if version("polymarket-client") != PINNED_SDK_VERSION:
        raise BootstrapError(
            f"credential bootstrap requires polymarket-client {PINNED_SDK_VERSION}"
        )
    try:
        signer = str(Account.from_key(private_key).address)
    except (TypeError, ValueError) as exc:
        raise BootstrapError("signer private key is not a valid EVM key") from exc
    if wallet.casefold() != signer.casefold():
        code = _contract_code(wallet)
        if code.casefold() in {"0x", "0x0", "0x00"}:
            raise BootstrapError(
                "account wallet is not deployed; bootstrap will not deploy wallets"
            )

    client = None
    try:
        # This explicit bootstrap command is the only place allowed to create or
        # derive CLOB L2 credentials.  _create avoids the SDK's wallet deployment
        # path while still performing the signed L1 credential flow.
        client = SecureClient._create(  # type: ignore[attr-defined]
            private_key=private_key,
            wallet=wallet,
            credentials=None,
        )
        detected = str(client.wallet_type)
        if detected not in SUPPORTED_WALLET_TYPES:
            raise BootstrapError(f"SDK returned unsupported wallet type {detected!r}")
        if args.expected_wallet_type != "AUTO" and detected != args.expected_wallet_type:
            raise BootstrapError(
                f"detected wallet type {detected} differs from expected "
                f"{args.expected_wallet_type}"
            )
        credentials = client.credentials
        secret_document = {
            "POLYMARKET_ACCOUNT_WALLET": str(client.wallet),
            "POLYMARKET_CLOB_API_KEY": str(credentials.key),
            "POLYMARKET_CLOB_API_PASSPHRASE": str(credentials.passphrase),
            "POLYMARKET_CLOB_API_SECRET": str(credentials.secret),
            "POLYMARKET_EXPECTED_WALLET_TYPE": detected,
            "POLYMARKET_SIGNER_PRIVATE_KEY": private_key,
        }
        _write_exclusive(args.output, secret_document)
        print(
            json.dumps(
                {
                    "wallet": str(client.wallet),
                    "signer": str(client.signer),
                    "wallet_type": detected,
                    "signature_type": SIGNATURE_TYPE_BY_WALLET[detected],
                    "sdk_version": PINNED_SDK_VERSION,
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    except FileExistsError as exc:
        raise BootstrapError(f"output file already exists: {args.output}") from exc
    except BootstrapError:
        raise
    except Exception as exc:
        raise BootstrapError(f"Polymarket credential bootstrap failed: {exc}") from exc
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BootstrapError, OSError) as exc:
        print(f"credential bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
