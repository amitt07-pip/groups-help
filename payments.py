import os
import random
from decimal import Decimal

import httpx

PAY_ADDRESS = os.getenv("PAY_ADDRESS", "0x0bdd444a792c8C8880B74C9c5124770BFf8674eA")
PRICE = Decimal(os.getenv("PRICE", "5"))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

NETWORKS = {
    "bsc": {
        "name": "BEP20 (BSC)",
        "rpc": os.getenv("BSC_RPC_URLS", "https://bsc-dataseed.bnbchain.org,https://bsc-dataseed1.defibit.io,https://1rpc.io/bnb").split(","),
        "explorer": "https://bscscan.com/tx/",
        "tokens": {
            "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
            "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
        },
    },
    "polygon": {
        "name": "Polygon",
        "rpc": os.getenv("POLYGON_RPC_URLS", "https://polygon-bor-rpc.publicnode.com,https://1rpc.io/matic,https://polygon.drpc.org").split(","),
        "explorer": "https://polygonscan.com/tx/",
        "tokens": {
            "USDT": ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
            "USDC": ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
        },
    },
    "eth": {
        "name": "ERC20 (Ethereum)",
        "rpc": os.getenv("ETH_RPC_URLS", "https://ethereum-rpc.publicnode.com,https://1rpc.io/eth,https://eth.drpc.org").split(","),
        "explorer": "https://etherscan.io/tx/",
        "tokens": {
            "USDT": ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
            "USDC": ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
        },
    },
}


async def _rpc(network: str, method: str, params: list):
    """Tries each RPC in order; returns the first non-null result (a lagging node may not see a new tx yet)."""
    last_error = None
    got_null = False
    async with httpx.AsyncClient(timeout=20) as http:
        for url in NETWORKS[network]["rpc"]:
            try:
                r = await http.post(url.strip(), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise RuntimeError(data["error"])
            except Exception as e:
                last_error = e
                continue
            if data.get("result") is None:
                got_null = True
                continue
            return data["result"]
    if got_null:
        return None
    raise RuntimeError(f"all {network} RPCs failed: {last_error}")


async def block_number(network: str) -> int:
    return int(await _rpc(network, "eth_blockNumber", []), 16)


async def verify_tx(network: str, token: str, tx_hash: str, min_block: int) -> tuple[bool, str]:
    contract, decimals = NETWORKS[network]["tokens"][token]
    receipt = await _rpc(network, "eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        return False, "Transaction not found on this network yet. Wait for it to confirm and try again."
    if int(receipt.get("status", "0x0"), 16) != 1:
        return False, "That transaction failed on-chain."
    if int(receipt["blockNumber"], 16) < min_block:
        return False, "That transaction is older than this payment request."
    need = int(PRICE * (10 ** decimals))
    to_topic = "0x" + "0" * 24 + PAY_ADDRESS.lower()[2:]
    paid = sum(
        int(lg["data"], 16)
        for lg in receipt.get("logs", [])
        if lg["address"].lower() == contract.lower()
        and len(lg["topics"]) == 3
        and lg["topics"][0] == TRANSFER_TOPIC
        and lg["topics"][2].lower() == to_topic
    )
    if paid == 0:
        return False, f"No {token} transfer to the payment address in that transaction."
    if paid < need:
        return False, f"Only {paid / 10 ** decimals} {token} received, {PRICE} needed."
    return True, "ok"
