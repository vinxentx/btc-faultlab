import os
from run_mining_demo import RPCClient, build_coinbase, build_block

rpc_user = os.getenv("RPC_USER", "user")
rpc_pass = os.getenv("RPC_PASS", "pass")
rpc_host = os.getenv("RPC_HOST", "localhost")
rpc_port = int(os.getenv("RPC_PORT", "18443"))

rpc = RPCClient(f"http://{rpc_host}:{rpc_port}", rpc_user, rpc_pass)
template = rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
print("rules", template.get("rules"))
print("transactions", len(template.get("transactions", [])))
print("height", template["height"])
print("default commitment", template.get("default_witness_commitment"))

witness_commitment = None
if "default_witness_commitment" in template:
    rules = template.get("rules", [])
    if "segwit" in rules and "!segwit" not in rules:
        witness_commitment = template["default_witness_commitment"]
    elif any(tx.get("has_witness") for tx in template.get("transactions", [])):
        witness_commitment = template["default_witness_commitment"]

coinbase_tx, coinbase_txid = build_coinbase(
    template["height"],
    template["coinbasevalue"],
    "76a914000000000000000000000000000000000000000088ac",  # dummy P2PKH
    extra_nonce=0,
    witness_commitment=witness_commitment,
)
print("coinbase txid", coinbase_txid[::-1].hex())
print("coinbase hex", coinbase_tx.hex())

block = build_block(
    template,
    coinbase_tx,
    coinbase_txid,
    extra_nonce=0,
    include_witness=witness_commitment is not None,
)
print("block", block)
if block is None:
    raise SystemExit("no solution")

block_hex, block_hash, nonce, extra_nonce, timestamp = block
print("block hash", block_hash)
print("nonce", nonce)
print("submit =>", rpc.call("submitblock", [block_hex]))
