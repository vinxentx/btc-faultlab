import hashlib
import json
import os
import queue
import struct
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests


class RPCError(RuntimeError):
    """Ausnahme für RPC-Fehlerantworten."""


class RPCClient:
    """Simple JSON-RPC-Client mit optionalem Wallet-Kontext."""

    def __init__(self, base_url: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.auth = (user, password)
        self.session = requests.Session()
        self._lock = threading.Lock()

    def call(self, method: str, params: Optional[list] = None, wallet: Optional[str] = None):
        payload = {
            "jsonrpc": "2.0",
            "id": "real-miner",
            "method": method,
            "params": params or [],
        }
        url = self.base_url
        if wallet:
            url = f"{url}/wallet/{wallet}"

        with self._lock:
            response = self.session.post(url, json=payload, auth=self.auth, timeout=30)

        if response.status_code != 200:
            raise RPCError(f"RPC HTTP {response.status_code}: {response.text}")

        body = response.json()
        if body.get("error"):
            raise RPCError(json.dumps(body["error"]))
        return body.get("result")


def wait_for_rpc(rpc: RPCClient, retries: int = 120, delay: float = 1.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            rpc.call("getblockchaininfo")
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == retries:
                raise RuntimeError("RPC blieb unerreichbar") from exc
            time.sleep(delay)


def encode_varint(value: int) -> bytes:
    if value < 0xFD:
        return value.to_bytes(1, "little")
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    if value <= 0xFFFFFFFF:
        return b"\xfe" + value.to_bytes(4, "little")
    return b"\xff" + value.to_bytes(8, "little")


def push_data(data: bytes) -> bytes:
    length = len(data)
    if length < 0x4C:
        return length.to_bytes(1, "little") + data
    if length <= 0xFF:
        return b"\x4c" + length.to_bytes(1, "little") + data
    if length <= 0xFFFF:
        return b"\x4d" + length.to_bytes(2, "little") + data
    return b"\x4e" + length.to_bytes(4, "little") + data


def int_to_little_endian(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    result = bytearray()
    n = value
    while n > 0:
        result.append(n & 0xFF)
        n >>= 8
    return bytes(result)


def encode_script_num(value: int) -> bytes:
    """Kodiert eine Ganzzahl im minimalen Script-Format (BIP62)."""
    if value == 0:
        return b""
    neg = value < 0
    abs_value = -value if neg else value
    result = bytearray()
    while abs_value:
        result.append(abs_value & 0xFF)
        abs_value >>= 8
    if result[-1] & 0x80:
        result.append(0x80 if neg else 0x00)
    elif neg:
        result[-1] |= 0x80
    return bytes(result)


def build_coinbase(
    height: int,
    coinbase_value: int,
    script_pubkey_hex: str,
    extra_nonce: int,
    witness_commitment: Optional[str] = None,
) -> Tuple[bytes, bytes]:
    """Erzeuge Coinbase-Transaktion und liefere (vollständige TX, txid)."""
    include_witness = witness_commitment is not None

    height_field = encode_script_num(height)
    nonce_field = encode_script_num(extra_nonce)
    script_sig = push_data(height_field) + push_data(nonce_field)

    version = struct.pack("<I", 2)

    inputs = bytearray()
    inputs += encode_varint(1)
    inputs += b"\x00" * 32  # vorheriger Hash (null)
    inputs += b"\xff" * 4  # vorheriger Index (-1)
    inputs += encode_varint(len(script_sig))
    inputs += script_sig
    inputs += b"\xff" * 4  # sequence

    script_pubkey = bytes.fromhex(script_pubkey_hex)
    num_outputs = 2 if include_witness else 1

    outputs = bytearray()
    outputs += encode_varint(num_outputs)
    outputs += coinbase_value.to_bytes(8, "little")
    outputs += encode_varint(len(script_pubkey))
    outputs += script_pubkey

    witness_section = b""
    if include_witness:
        commitment_script = bytes.fromhex(witness_commitment)
        outputs += (0).to_bytes(8, "little")
        outputs += encode_varint(len(commitment_script))
        outputs += commitment_script

        reserved_value = b"\x00" * 32
        witness_section = encode_varint(1) + encode_varint(len(reserved_value)) + reserved_value

    locktime = struct.pack("<I", 0)

    legacy_tx = version + inputs + outputs + locktime

    full_tx = bytearray()
    full_tx += version
    if include_witness:
        full_tx += b"\x00\x01"
    full_tx += inputs
    full_tx += outputs
    if include_witness:
        full_tx += witness_section
    full_tx += locktime

    txid = hash256(legacy_tx)
    return bytes(full_tx), txid


def hash256(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def merkle_root(tx_hashes: List[bytes]) -> bytes:
    if not tx_hashes:
        return b"\x00" * 32

    current = tx_hashes
    while len(current) > 1:
        if len(current) % 2 == 1:
            current.append(current[-1])
        new_level = []
        for idx in range(0, len(current), 2):
            new_level.append(hash256(current[idx] + current[idx + 1]))
        current = new_level
    return current[0]


def bits_to_target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0xFFFFFF
    return mantissa * (1 << (8 * (exponent - 3)))


def build_block(
    template: dict,
    coinbase_tx: bytes,
    coinbase_txid: bytes,
    extra_nonce: int,
    max_nonce: int = 0xFFFFFFFF,
    include_witness: bool = False,
) -> Optional[Tuple[str, str, int, int, int]]:
    txs_serialized = [bytes.fromhex(tx["data"]) for tx in template.get("transactions", [])]

    txid_hashes = [coinbase_txid]
    for tx in template.get("transactions", []):
        txid_bytes = bytes.fromhex(tx["txid"])
        txid_hashes.append(txid_bytes)

    root = merkle_root(txid_hashes)
    if extra_nonce == 0:
        print(f"[debug] merkle root={root[::-1].hex()} txid0={txid_hashes[0][::-1].hex()}")

    version = template["version"]
    prev_block = bytes.fromhex(template["previousblockhash"])[::-1]
    bits_hex = template["bits"]
    bits_bytes = bytes.fromhex(bits_hex)[::-1]
    target = bits_to_target(bits_hex)
    timestamp = max(template["curtime"], int(time.time()))

    tx_count = 1 + len(txs_serialized)
    for nonce in range(max_nonce + 1):
        header = (
            struct.pack("<I", version)
            + prev_block
            + root[::-1]
            + struct.pack("<I", timestamp)
            + bits_bytes
            + struct.pack("<I", nonce)
        )
        header_hash = hash256(header)
        header_int = int.from_bytes(header_hash, "big")
        if header_int <= target:
            prefix = encode_varint(tx_count)
            block_payload = header + prefix + coinbase_tx + b"".join(txs_serialized)
            block_hash = header_hash[::-1].hex()
            return block_payload.hex(), block_hash, nonce, extra_nonce, timestamp
    return None


@dataclass
class MinerTaskResult:
    miner: str
    block_hash: str
    height: int
    nonce: int
    extra_nonce: int
    timestamp: int
    duration_s: float


class MinerWorker(threading.Thread):
    def __init__(
        self,
        name: str,
        rpc: RPCClient,
        wallet: str,
        script_pubkey: str,
        job_queue: "queue.Queue[int]",
        result_sink: List[MinerTaskResult],
        result_lock: threading.Lock,
    ):
        super().__init__(name=name)
        self.rpc = rpc
        self.wallet = wallet
        self.script_pubkey = script_pubkey
        self.jobs = job_queue
        self.results = result_sink
        self.result_lock = result_lock

    def run(self) -> None:  # noqa: D401
        while True:
            try:
                target_height = self.jobs.get_nowait()
            except queue.Empty:
                return
            start = time.time()
            try:
                result = self._mine_block(target_height)
                duration = time.time() - start
                if result:
                    result.duration_s = duration
                    with self.result_lock:
                        self.results.append(result)
                    print(f"[{self.name}] Block {result.height} in {duration:.3f}s gefunden (hash={result.block_hash}).")
            except Exception as exc:  # noqa: BLE001
                print(f"[{self.name}] Fehler: {exc}")
            finally:
                self.jobs.task_done()

    def _mine_block(self, task_id: int) -> Optional[MinerTaskResult]:
        extra_nonce = 0
        while True:
            # Bitcoin Core 27.0 erfordert SegWit-Regeln, auch wenn SegWit deaktiviert ist
            template = self.rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
            # Witness-Commitment aus Template extrahieren (bereits vollständiger Script-Hex)
            # Wichtig: Nur verwenden, wenn SegWit wirklich aktiviert ist (nicht "!segwit")
            witness_commitment = template.get("default_witness_commitment")
            coinbase_tx, coinbase_txid = build_coinbase(
                template["height"],
                template["coinbasevalue"],
                self.script_pubkey,
                extra_nonce,
                witness_commitment,
            )
            if extra_nonce == 0:
                print(
                    f"[{self.name}] coinbase txid={coinbase_txid[::-1].hex()} witness={bool(witness_commitment)} "
                    f"tx={coinbase_tx.hex()}"
                )
            solved = build_block(
                template,
                coinbase_tx,
                coinbase_txid,
                extra_nonce,
                include_witness=witness_commitment is not None,
            )
            if not solved:
                extra_nonce += 1
                continue

            block_hex, block_hash, nonce, final_extra_nonce, timestamp = solved
            submit = self.rpc.call("submitblock", [block_hex])
            if submit is None:
                return MinerTaskResult(
                    miner=self.name,
                    block_hash=block_hash,
                    height=template["height"],
                    nonce=nonce,
                    extra_nonce=final_extra_nonce,
                    timestamp=timestamp,
                    duration_s=0.0,
                )

            if submit not in ("duplicate", "duplicate-invalid", "already known"):
                print(f"[{self.name}] submitblock Antwort: {submit}")
            extra_nonce += 1


def ensure_wallet(rpc: RPCClient, wallet_name: str) -> None:
    wallets = rpc.call("listwallets")
    if wallet_name not in wallets:
        try:
            rpc.call("createwallet", [wallet_name])
        except RPCError as exc:
            if "already exists" not in str(exc):
                raise
    if wallet_name not in rpc.call("listwallets"):
        rpc.call("loadwallet", [wallet_name])


def obtain_miner_addresses(
    rpc: RPCClient,
    wallet_name: str,
    count: int,
    label_prefix: str,
) -> List[str]:
    addresses = []
    for idx in range(count):
        label = f"{label_prefix}{idx + 1:02d}"
        addr = rpc.call("getnewaddress", [label, "legacy"], wallet=wallet_name)
        info = rpc.call("getaddressinfo", [addr], wallet=wallet_name)
        script_pubkey = info["scriptPubKey"]
        addresses.append(script_pubkey)
    return addresses


def main() -> None:
    rpc_user = os.getenv("RPC_USER", "user")
    rpc_pass = os.getenv("RPC_PASS", "pass")
    rpc_host = os.getenv("RPC_HOST", "node")
    rpc_port = int(os.getenv("RPC_PORT", "18443"))
    miner_threads = int(os.getenv("MINER_THREADS", "10"))
    target_blocks = int(os.getenv("TARGET_BLOCKS", "10"))
    label = os.getenv("COINBASE_LABEL", "miner-")

    rpc_url = f"http://{rpc_host}:{rpc_port}"
    rpc = RPCClient(rpc_url, rpc_user, rpc_pass)

    print("[setup] Warte auf Bitcoin-Core RPC...")
    wait_for_rpc(rpc)
    print("[setup] RPC erreichbar.")

    wallet_name = "real-miners"
    ensure_wallet(rpc, wallet_name)
    print(f"[setup] Wallet '{wallet_name}' geladen.")

    script_pubkeys = obtain_miner_addresses(rpc, wallet_name, miner_threads, label)
    print(f"[setup] {len(script_pubkeys)} Miner-Adressen erzeugt.")

    jobs: "queue.Queue[int]" = queue.Queue()
    for job_id in range(target_blocks):
        jobs.put(job_id)

    result_lock = threading.Lock()
    results: List[MinerTaskResult] = []

    workers = [
        MinerWorker(
            name=f"miner-{idx + 1:02d}",
            rpc=rpc,
            wallet=wallet_name,
            script_pubkey=script_pubkeys[idx],
            job_queue=jobs,
            result_sink=results,
            result_lock=result_lock,
        )
        for idx in range(miner_threads)
    ]

    print("[setup] Starte Mining-Threads...")
    start_time = time.time()
    for worker in workers:
        worker.start()

    for worker in workers:
        worker.join()

    duration = time.time() - start_time
    print(f"[done] Mining abgeschlossen. Dauer: {duration:.2f}s")

    # Ausgabe sortiert nach Blockhöhe
    results_sorted = sorted(results, key=lambda r: r.height)
    for idx, res in enumerate(results_sorted, start=1):
        print(
            f"[block {idx}] hash={res.block_hash} height={res.height} "
            f"nonce={res.nonce} extra_nonce={res.extra_nonce} ts={res.timestamp} "
            f"miner={res.miner} duration={res.duration_s:.3f}s"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Abbruch durch Benutzer.")

