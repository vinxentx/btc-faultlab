#!/usr/bin/env python3
"""
FaultLab Transaction Generator - Two-Phase Architecture

Phase 1 (Preparation): Pre-create and sign transactions using wallet RPC
Phase 2 (Injection): Inject pre-signed transactions via sendrawtransaction

This decouples wallet operations from network injection, enabling:
- No wallet lock contention during observation
- Much faster injection (~50-100ms vs ~1300ms)
- True measurement of network propagation, not wallet performance
"""
import argparse
import base64
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPConnection
from typing import Deque, Dict, Tuple, Optional, List
from concurrent.futures import ThreadPoolExecutor


JSONRPC_HEADERS = {"Content-Type": "application/json"}


@dataclass
class PreparedTx:
    """A pre-signed transaction ready for injection."""
    seq_num: int
    raw_hex: str
    dest_address: str
    amount_btc: float


@dataclass
class TxRequest:
    """A scheduled transaction request."""
    seq_num: int
    scheduled_time: float
    prepared_tx: PreparedTx


@dataclass
class TxResult:
    """Result of a transaction attempt."""
    seq_num: int
    scheduled_time: float
    sent_time: float
    latency_ms: float
    txid: Optional[str]
    error_code: Optional[int]
    error_message: Optional[str]
    success: bool


class PersistentRPCClient:
    """Thread-safe RPC client with persistent HTTP connections (one per thread)."""

    def __init__(self, rpc_url: str, auth: Optional[str] = None, name: str = ""):
        proto, rest = rpc_url.split("://", 1)
        if proto != "http":
            raise ValueError("Only http RPC endpoints are supported")
        self.host = rest
        self.auth = auth
        self.name = name
        self._local = threading.local()

    def _get_connection(self, timeout: int) -> HTTPConnection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = HTTPConnection(self.host, timeout=timeout)
            self._local.conn = conn
        return conn

    def _close_connection(self):
        conn = getattr(self._local, 'conn', None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def call(self, method: str, params=None, *, wallet: Optional[str] = None, timeout: int = 30):
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "txgen",
            "method": method,
            "params": params or []
        })
        headers = dict(JSONRPC_HEADERS)
        if self.auth:
            auth_str = base64.b64encode(self.auth.encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + auth_str

        path = f"/wallet/{wallet}" if wallet else "/"

        for attempt in range(2):
            try:
                conn = self._get_connection(timeout)
                conn.request("POST", path, payload, headers)
                resp = conn.getresponse()
                body = resp.read()
                data = json.loads(body)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                return data["result"]
            except (ConnectionError, BrokenPipeError, EOFError, ConnectionResetError):
                self._close_connection()
                if attempt == 1:
                    raise
            except Exception:
                self._close_connection()
                raise


def decode_rpc_error(err: RuntimeError) -> Tuple[Optional[int], str]:
    if not err.args:
        return None, ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("code"), payload.get("message", "")
    return None, str(payload)


def wait_for_rpc(client: PersistentRPCClient, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    print(f"⏳ Warte auf RPC-Endpunkt {client.name} ({client.host})...")
    last_err = None
    while time.time() < deadline:
        try:
            client.call("getblockchaininfo", timeout=5)
            print(f"✅ {client.name} erreichbar")
            return
        except Exception as exc:
            last_err = exc
            time.sleep(2.0)
    raise RuntimeError(f"RPC {client.name} nicht erreichbar: {last_err}")


def ensure_wallet_loaded(client: PersistentRPCClient, wallet: str) -> None:
    max_wait = 600
    wait_start = time.time()

    while time.time() - wait_start < max_wait:
        try:
            client.call("loadwallet", [wallet], timeout=30)
            print(f"🔐 Wallet '{wallet}' geladen")
            return
        except RuntimeError as err:
            code, message = decode_rpc_error(err)
            message_lower = message.lower()
            if code in (-35, -4) or "already loaded" in message_lower:
                print(f"ℹ️ Wallet '{wallet}' bereits geladen")
                return
            elif code == -18 or "not found" in message_lower:
                elapsed = int(time.time() - wait_start)
                if elapsed % 30 == 0:
                    print(f"⏳ Warte auf Wallet '{wallet}'... ({elapsed}s)")
                time.sleep(5)
                continue
            else:
                raise
    raise RuntimeError(f"Wallet '{wallet}' nicht gefunden nach {max_wait}s")


def fetch_confirmed_utxo_count(client: PersistentRPCClient, wallet: str) -> int:
    utxos = client.call("listunspent", [1, 9999999], wallet=wallet, timeout=60)
    return len(utxos)


class TransactionPool:
    """Thread-safe pool of pre-signed transactions."""

    def __init__(self):
        self._transactions: List[PreparedTx] = []
        self._index = 0
        self._lock = threading.Lock()

    def add(self, tx: PreparedTx):
        with self._lock:
            self._transactions.append(tx)

    def get_next(self) -> Optional[PreparedTx]:
        with self._lock:
            if self._index >= len(self._transactions):
                return None
            tx = self._transactions[self._index]
            self._index += 1
            return tx

    def __len__(self) -> int:
        with self._lock:
            return len(self._transactions)

    def remaining(self) -> int:
        with self._lock:
            return len(self._transactions) - self._index


class TransactionPreparer:
    """
    Phase 1: Pre-create and sign transactions using wallet RPC.

    Uses createrawtransaction + fundrawtransaction + signrawtransactionwithwallet
    to prepare transactions without broadcasting them.
    """

    def __init__(
        self,
        shard_id: str,
        wallet_client: PersistentRPCClient,
        wallet: str,
        amount_btc: float,
        num_workers: int = 4,
    ):
        self.shard_id = shard_id
        self.wallet_client = wallet_client
        self.wallet = wallet
        self.amount_btc = amount_btc
        self.num_workers = num_workers
        self.pool = TransactionPool()
        self._seq_counter = 0
        self._seq_lock = threading.Lock()
        self._error_count = 0

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq_counter += 1
            return self._seq_counter

    def _get_new_address(self) -> str:
        return self.wallet_client.call("getnewaddress", [], wallet=self.wallet, timeout=30)

    def _prepare_single_tx(self) -> Optional[PreparedTx]:
        """Create and sign a single transaction without broadcasting."""
        seq = self._next_seq()
        try:
            # Get destination address
            dest_addr = self._get_new_address()

            # Create raw transaction (empty inputs, will be funded)
            outputs = {dest_addr: self.amount_btc}
            raw_tx = self.wallet_client.call(
                "createrawtransaction",
                [[], outputs],
                wallet=self.wallet,
                timeout=30
            )

            # Fund the transaction (selects UTXOs, adds change)
            # lockUnspents=True prevents UTXO reuse during parallel preparation
            funded = self.wallet_client.call(
                "fundrawtransaction",
                [raw_tx, {"changePosition": 1, "lockUnspents": True}],
                wallet=self.wallet,
                timeout=30
            )

            # Sign the transaction
            signed = self.wallet_client.call(
                "signrawtransactionwithwallet",
                [funded["hex"]],
                wallet=self.wallet,
                timeout=30
            )

            if not signed.get("complete"):
                raise RuntimeError(f"Transaction signing incomplete: {signed}")

            return PreparedTx(
                seq_num=seq,
                raw_hex=signed["hex"],
                dest_address=dest_addr,
                amount_btc=self.amount_btc,
            )

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 10:
                print(f"⚠️  Shard {self.shard_id}: Fehler beim Vorbereiten TX #{seq}: {e}")
            return None

    def prepare_batch(self, count: int, progress_interval: int = 100) -> int:
        """Prepare a batch of transactions using parallel workers."""
        print(f"🔧 Shard {self.shard_id}: Bereite {count} Transaktionen vor...")

        prepared = 0
        errors = 0

        # Use thread pool for parallel preparation
        with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
            futures = []
            for _ in range(count):
                futures.append(executor.submit(self._prepare_single_tx))

            for i, future in enumerate(futures):
                try:
                    tx = future.result(timeout=60)
                    if tx:
                        self.pool.add(tx)
                        prepared += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    if errors <= 10:
                        print(f"⚠️  Shard {self.shard_id}: Prep-Fehler: {e}")

                if (i + 1) % progress_interval == 0:
                    print(f"   … {i + 1}/{count} vorbereitet ({prepared} ok, {errors} Fehler)")

        print(f"✅ Shard {self.shard_id}: {prepared} TXs vorbereitet, {errors} Fehler")
        return prepared


class StrictRateInjector:
    """
    Phase 2: Inject pre-signed transactions at strict rate.

    Uses sendrawtransaction on wallet_shard - much faster than sendtoaddress
    because no wallet operations are needed.
    """

    def __init__(
        self,
        shard_id: str,
        rate: float,
        rpc_client: PersistentRPCClient,
        tx_pool: TransactionPool,
        num_workers: int = 4,
        queue_size: int = 200,
    ):
        self.shard_id = shard_id
        self.rate = rate
        self.interval = 1.0 / rate
        self.rpc_client = rpc_client
        self.tx_pool = tx_pool
        self.num_workers = num_workers

        self.tx_queue: queue.Queue[Optional[TxRequest]] = queue.Queue(maxsize=queue_size)
        self.result_queue: queue.Queue[TxResult] = queue.Queue()

        self._sent_count = 0
        self._sent_lock = threading.Lock()
        self._error_count = 0
        self._skipped_count = 0
        self._pool_exhausted = False

        self.running = False
        self.start_time = 0.0

        self.executor: Optional[ThreadPoolExecutor] = None
        self.scheduler_thread: Optional[threading.Thread] = None
        self.logger_thread: Optional[threading.Thread] = None

    def _inc_sent(self) -> int:
        with self._sent_lock:
            self._sent_count += 1
            return self._sent_count

    @property
    def sent_count(self) -> int:
        with self._sent_lock:
            return self._sent_count

    def scheduler_loop(self):
        """Strict scheduler: Pulls from TX pool and adds to queue at exact intervals."""
        next_tick = time.perf_counter()
        seq = 0

        while self.running:
            now = time.perf_counter()

            if now < next_tick:
                time.sleep(min(next_tick - now, 0.001))
                continue

            # Get next pre-signed transaction from pool
            prepared_tx = self.tx_pool.get_next()
            if prepared_tx is None:
                if not self._pool_exhausted:
                    self._pool_exhausted = True
                    print(f"⚠️  Shard {self.shard_id}: TX-Pool erschöpft nach {self.sent_count} TXs")
                next_tick += self.interval
                continue

            seq += 1
            request = TxRequest(
                seq_num=seq,
                scheduled_time=time.time(),
                prepared_tx=prepared_tx,
            )

            try:
                self.tx_queue.put_nowait(request)
            except queue.Full:
                self._skipped_count += 1
                if self._skipped_count % 10 == 1:
                    print(f"⚠️  Shard {self.shard_id}: Queue voll, TX #{seq} übersprungen "
                          f"(bisher {self._skipped_count} übersprungen)")

            next_tick += self.interval

    def sender_worker(self, worker_id: int):
        """Worker: Injects pre-signed transactions via sendrawtransaction."""
        while self.running:
            try:
                request = self.tx_queue.get(timeout=0.5)
                if request is None:
                    break

                start = time.perf_counter()
                sent_time = time.time()

                try:
                    # sendrawtransaction is FAST - no wallet operations!
                    txid = self.rpc_client.call(
                        "sendrawtransaction",
                        [request.prepared_tx.raw_hex],
                        timeout=10,
                    )
                    latency_ms = (time.perf_counter() - start) * 1000

                    result = TxResult(
                        seq_num=request.seq_num,
                        scheduled_time=request.scheduled_time,
                        sent_time=sent_time,
                        latency_ms=latency_ms,
                        txid=txid,
                        error_code=None,
                        error_message=None,
                        success=True,
                    )
                    self._inc_sent()

                except RuntimeError as err:
                    latency_ms = (time.perf_counter() - start) * 1000
                    code, message = decode_rpc_error(err)

                    result = TxResult(
                        seq_num=request.seq_num,
                        scheduled_time=request.scheduled_time,
                        sent_time=sent_time,
                        latency_ms=latency_ms,
                        txid=None,
                        error_code=code,
                        error_message=message,
                        success=False,
                    )
                    self._error_count += 1

                except Exception as err:
                    latency_ms = (time.perf_counter() - start) * 1000
                    result = TxResult(
                        seq_num=request.seq_num,
                        scheduled_time=request.scheduled_time,
                        sent_time=sent_time,
                        latency_ms=latency_ms,
                        txid=None,
                        error_code=None,
                        error_message=str(err),
                        success=False,
                    )
                    self._error_count += 1

                self.result_queue.put(result)

            except queue.Empty:
                continue

    def logger_loop(self, txlog, perflog, errlog):
        """Logger: Processes results and writes to files."""
        throughput_window: Deque[float] = deque()
        window_duration = 10
        last_log_count = 0

        while self.running or not self.result_queue.empty():
            try:
                result = self.result_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            now = time.time()

            if result.success:
                throughput_window.append(result.sent_time)
            while throughput_window and now - throughput_window[0] > window_duration:
                throughput_window.popleft()

            if len(throughput_window) > 1:
                span = throughput_window[-1] - throughput_window[0]
                rtps = (len(throughput_window) - 1) / span if span > 0 else 0.0
            else:
                rtps = 0.0

            timestamp = datetime.now(timezone.utc).isoformat()

            if result.success:
                txlog.write(f"{result.seq_num},{self.shard_id},{timestamp},{result.txid}\n")
                txlog.flush()

                perflog.write(
                    f"{result.seq_num},{timestamp},{result.latency_ms:.2f},{rtps:.4f},"
                    f"0,0,0,0.0,ok\n"
                )
                perflog.flush()

                current_sent = self.sent_count
                if current_sent >= last_log_count + 50:
                    last_log_count = (current_sent // 50) * 50
                    elapsed = now - self.start_time
                    expected = elapsed * self.rate
                    achieved_pct = (current_sent / expected * 100) if expected > 0 else 100.0
                    queue_size = self.tx_queue.qsize()
                    remaining = self.tx_pool.remaining()

                    print(f"📤 Shard {self.shard_id}: {current_sent} TX, "
                          f"Latenz {result.latency_ms:.1f}ms, "
                          f"Rolling TPS {rtps:.4f} (Ziel: {self.rate:.4f}), "
                          f"Erreicht: {achieved_pct:.1f}%, Queue: {queue_size}, "
                          f"Pool: {remaining}")
            else:
                safe_message = (result.error_message or "").replace('"', '""')
                errlog.write(
                    f"{timestamp},{result.error_code if result.error_code else ''},"
                    f'"{safe_message}",seq={result.seq_num}\n'
                )
                errlog.flush()

                perflog.write(
                    f"{result.seq_num},{timestamp},{result.latency_ms:.2f},{rtps:.4f},"
                    f"0,0,0,0.0,error\n"
                )
                perflog.flush()

    def start(self, txlog, perflog, errlog):
        """Start the injector with all threads."""
        self.running = True
        self.start_time = time.time()

        self.executor = ThreadPoolExecutor(max_workers=self.num_workers)
        for i in range(self.num_workers):
            self.executor.submit(self.sender_worker, i)

        self.scheduler_thread = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.scheduler_thread.start()

        self.logger_thread = threading.Thread(
            target=self.logger_loop,
            args=(txlog, perflog, errlog),
            daemon=True
        )
        self.logger_thread.start()

        print(f"🚀 Shard {self.shard_id}: Injection gestartet mit {self.num_workers} Workers, "
              f"Zielrate {self.rate:.4f} tx/s, Pool: {len(self.tx_pool)} TXs")

    def stop(self):
        """Stop all threads gracefully."""
        self.running = False

        for _ in range(self.num_workers):
            try:
                self.tx_queue.put_nowait(None)
            except queue.Full:
                pass

        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=2.0)
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
        if self.logger_thread:
            self.logger_thread.join(timeout=5.0)

        elapsed = time.time() - self.start_time
        actual_rate = self.sent_count / elapsed if elapsed > 0 else 0.0

        print(f"\n⏹️  Shard {self.shard_id} gestoppt:")
        print(f"   Gesendet: {self.sent_count} TX in {elapsed:.1f}s")
        print(f"   Fehler: {self._error_count}, Übersprungen: {self._skipped_count}")
        print(f"   Rate: {actual_rate:.4f} tx/s (Ziel: {self.rate:.4f})")
        print(f"   Effizienz: {(actual_rate / self.rate * 100):.1f}%")


def ensure_log_directory(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="FaultLab Tx Generator - Two-Phase Architecture")
    parser.add_argument("--shard-id", required=True, help="Shard-Kennung (z.B. a, b)")
    parser.add_argument("--rate", type=float, required=True, help="Transaktionen pro Sekunde")
    parser.add_argument("--rpc", required=True, help="Wallet RPC URL (für Vorbereitung und Injection)")
    parser.add_argument("--wallet", required=True, help="Wallet-Name")
    parser.add_argument("--log", required=True, help="Pfad zur Transaktions-Logdatei")
    parser.add_argument("--node-count", type=int, required=True, help="Anzahl Knoten im Netz")
    parser.add_argument("--amount-btc", type=float, default=0.0001, help="Betrag pro TX")
    parser.add_argument("--utxo-target", type=int, default=800, help="Min. bestätigte UTXOs")
    parser.add_argument("--num-workers", type=int, default=4, help="Anzahl paralleler Sender")
    parser.add_argument("--queue-size", type=int, default=200, help="TX-Queue Größe")
    parser.add_argument("--prep-multiplier", type=float, default=1.5,
                        help="Multiplier für TX-Pool (rate * observe_time * multiplier)")
    # Legacy args (ignored but accepted for compatibility)
    parser.add_argument("--address-pool-size", type=int, default=1024)
    parser.add_argument("--address-reuse-window", type=int, default=100)
    parser.add_argument("--stats-interval", type=int, default=200)
    parser.add_argument("--throughput-window", type=int, default=10)
    args = parser.parse_args()

    if args.rate <= 0:
        raise ValueError("Rate muss > 0 sein")

    # Parse wallet RPC URL
    proto, rest = args.rpc.split("://", 1)
    if proto != "http":
        raise ValueError("Nur http:// Endpunkte werden unterstützt")
    credential, host = rest.split("@", 1)
    wallet_rpc_url = f"http://{host}"
    wallet_auth = credential

    ensure_log_directory(args.log)
    performance_log = args.log.replace(".csv", "_performance.csv")
    error_log = args.log.replace(".csv", "_errors.csv")

    # Create RPC client (wallet_shard handles both preparation and injection)
    wallet_client = PersistentRPCClient(wallet_rpc_url, wallet_auth, name="wallet_shard")

    # Wait for RPC endpoint
    wait_for_rpc(wallet_client, timeout_s=300)

    ensure_wallet_loaded(wallet_client, args.wallet)

    # Wait for UTXOs
    print(f"⏳ Warte auf {args.utxo_target} bestätigte UTXOs...")
    max_utxo_wait = 600
    utxo_wait_start = time.time()

    while time.time() - utxo_wait_start < max_utxo_wait:
        confirmed = fetch_confirmed_utxo_count(wallet_client, args.wallet)
        print(f"💰 Wallet '{args.wallet}': {confirmed} bestätigte UTXOs")

        if confirmed >= args.utxo_target:
            print(f"✅ Genug UTXOs ({confirmed} ≥ {args.utxo_target})")
            break

        time.sleep(5)
    else:
        raise RuntimeError(f"Nicht genug UTXOs nach {max_utxo_wait}s")

    # Calculate how many transactions to prepare
    # Estimate: rate * 600s (10 min max observation) * multiplier
    tx_count = int(args.rate * 600 * args.prep_multiplier)
    tx_count = max(tx_count, 1000)  # Minimum 1000 TXs
    tx_count = min(tx_count, args.utxo_target)  # Can't exceed available UTXOs

    print(f"\n{'='*60}")
    print(f"PHASE 1: TRANSACTION PREPARATION")
    print(f"{'='*60}")

    # Phase 1: Prepare transactions
    preparer = TransactionPreparer(
        shard_id=args.shard_id,
        wallet_client=wallet_client,
        wallet=args.wallet,
        amount_btc=args.amount_btc,
        num_workers=args.num_workers,
    )

    prepared = preparer.prepare_batch(tx_count, progress_interval=200)

    if prepared < 100:
        raise RuntimeError(f"Zu wenige TXs vorbereitet: {prepared}")

    print(f"\n{'='*60}")
    print(f"PHASE 2: TRANSACTION INJECTION")
    print(f"{'='*60}")

    # Phase 2: Inject transactions (using same wallet_client)
    injector = StrictRateInjector(
        shard_id=args.shard_id,
        rate=args.rate,
        rpc_client=wallet_client,
        tx_pool=preparer.pool,
        num_workers=args.num_workers,
        queue_size=args.queue_size,
    )

    # Headers
    txlog_header = "tx_index,shard_id,submit_ts_utc,txid\n"
    perflog_header = (
        "tx_index,timestamp_utc,latency_ms,rolling_throughput_tx_s,"
        "utxos_confirmed,utxos_unconfirmed,utxos_total,balance_btc,status\n"
    )
    errorlog_header = "timestamp_utc,error_code,error_message,context\n"

    print(f"⚡ Shard {args.shard_id}: Zielrate {args.rate:.4f} tx/s via sendrawtransaction")

    try:
        with open(args.log, "w", encoding="utf-8") as txlog, \
             open(performance_log, "w", encoding="utf-8") as perflog, \
             open(error_log, "w", encoding="utf-8") as errlog:

            txlog.write(txlog_header)
            perflog.write(perflog_header)
            errlog.write(errorlog_header)

            injector.start(txlog, perflog, errlog)

            while True:
                time.sleep(1.0)

    except KeyboardInterrupt:
        injector.stop()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("⏹️  Txgen beendet")
        sys.exit(0)
