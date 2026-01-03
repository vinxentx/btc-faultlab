#!/usr/bin/env python3
"""
FaultLab Transaction Generator - Asynchronous Strict-Rate Version

Guarantees constant TX injection rate by using:
1. A strict scheduler thread that fires at exact intervals
2. A pool of parallel sender workers
3. A queue buffer to absorb RPC latency spikes

This ensures the network receives a constant stream of transactions
regardless of RPC response times.
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
from typing import Deque, Dict, Tuple, Callable, Optional, List
from concurrent.futures import ThreadPoolExecutor


JSONRPC_HEADERS = {"Content-Type": "application/json"}


@dataclass
class TxRequest:
    """A scheduled transaction request."""
    seq_num: int          # Global sequence number
    scheduled_time: float  # When this TX was supposed to be sent
    dest_address: str      # Target address


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
    
    def __init__(self, rpc_url: str, auth: Optional[str] = None):
        proto, rest = rpc_url.split("://", 1)
        if proto != "http":
            raise ValueError("Only http RPC endpoints are supported")
        self.host = rest
        self.auth = auth
        self._local = threading.local()  # Thread-local storage for connections
    
    def _get_connection(self, timeout: int) -> HTTPConnection:
        """Get or create a connection for the current thread."""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = HTTPConnection(self.host, timeout=timeout)
            self._local.conn = conn
        return conn
    
    def _close_connection(self):
        """Close the connection for the current thread."""
        conn = getattr(self._local, 'conn', None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
    
    def call(self, method: str, params=None, *, wallet: Optional[str] = None, timeout: int = 10):
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


def rpc_timeout_for(node_count: int) -> int:
    if node_count >= 128:
        return 20
    if node_count >= 64:
        return 15
    return 10


def rpc_call_with_retry(client: PersistentRPCClient, method: str, params=None, *,
                        wallet: Optional[str], timeout: int, retries: int = 3,
                        delay_s: float = 1.5):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return client.call(method, params, wallet=wallet, timeout=timeout)
        except Exception as exc:
            last_err = exc
            if attempt == retries:
                raise
            time.sleep(delay_s)
    raise last_err


def wait_for_rpc(client: PersistentRPCClient, node_count: int) -> None:
    base_timeout = 300 if node_count >= 128 else 180 if node_count >= 64 else 120
    deadline = time.time() + base_timeout
    print(f"⏳ Warte auf RPC-Endpunkt {client.host} (Timeout {base_timeout}s)...")
    last_err = None
    while time.time() < deadline:
        try:
            client.call("getblockchaininfo", timeout=5)
            print("✅ RPC erreichbar")
            return
        except Exception as exc:
            last_err = exc
            time.sleep(2.0)
    raise RuntimeError(f"RPC nicht erreichbar: {last_err}")


def decode_rpc_error(err: RuntimeError) -> Tuple[Optional[int], str]:
    if not err.args:
        return None, ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("code"), payload.get("message", "")
    return None, str(payload)


def ensure_wallet_loaded(client: PersistentRPCClient, wallet: str, timeout: int) -> None:
    max_wait = 600
    wait_start = time.time()
    last_error = None
    
    while time.time() - wait_start < max_wait:
        try:
            client.call("loadwallet", [wallet], timeout=timeout)
            print(f"🔐 Wallet '{wallet}' geladen")
            return
        except RuntimeError as err:
            code, message = decode_rpc_error(err)
            message_lower = message.lower()
            if code in (-35, -4) or "already loaded" in message_lower:
                print(f"ℹ️ Wallet '{wallet}' bereits geladen")
                return
            elif code == -18 or "not found" in message_lower or "path does not exist" in message_lower:
                last_error = err
                elapsed = int(time.time() - wait_start)
                if elapsed % 30 == 0:
                    print(f"⏳ Warte auf Wallet '{wallet}'... ({elapsed}s)")
                time.sleep(5)
                continue
            else:
                raise
    
    raise RuntimeError(f"Wallet '{wallet}' nicht gefunden nach {max_wait}s") from last_error


def fetch_wallet_utxos(client: PersistentRPCClient, wallet: str, timeout: int,
                       min_conf: int = 0) -> Dict[str, float]:
    utxos = rpc_call_with_retry(
        client, "listunspent", [min_conf, 9999999], wallet=wallet, timeout=timeout
    )
    total = len(utxos)
    confirmed = sum(1 for u in utxos if u.get("confirmations", 0) >= 1)
    unconfirmed = total - confirmed
    balance = sum(u.get("amount", 0.0) for u in utxos)
    return {
        "total": total,
        "confirmed": confirmed,
        "unconfirmed": unconfirmed,
        "balance": balance,
    }


class AddressPool:
    """Thread-safe rotating address pool."""
    
    def __init__(self, initial: List[str], fetcher: Callable[[], str], reuse_window: int):
        if not initial:
            raise ValueError("Adress-Pool darf nicht leer sein")
        self._addresses = list(initial)
        self._fetcher = fetcher
        self._reuse_window = max(1, reuse_window)
        self._index = 0
        self._use_count = 0
        self._lock = threading.Lock()
    
    def get_next(self) -> str:
        """Get next address in rotation (thread-safe)."""
        with self._lock:
            addr = self._addresses[self._index]
            self._index = (self._index + 1) % len(self._addresses)
            self._use_count += 1
            
            # Periodically refresh addresses
            if self._use_count >= self._reuse_window * len(self._addresses):
                try:
                    new_addr = self._fetcher()
                    replace_idx = self._index
                    self._addresses[replace_idx] = new_addr
                    self._use_count = 0
                except Exception:
                    pass  # Keep using old addresses on failure
            
            return addr
    
    def __len__(self) -> int:
        return len(self._addresses)


class StrictRateTxGenerator:
    """
    Asynchronous TX generator with strict rate control.
    
    Uses a producer-consumer pattern:
    - Scheduler (producer): Fires at exact intervals, adds TX requests to queue
    - Workers (consumers): Pull from queue and send TXs in parallel
    
    This decouples TX scheduling from RPC latency, ensuring constant rate.
    """
    
    def __init__(
        self,
        shard_id: str,
        rate: float,
        client: PersistentRPCClient,
        wallet: str,
        address_pool: AddressPool,
        amount_btc: float,
        timeout: int,
        num_workers: int = 4,
        queue_size: int = 200,
    ):
        self.shard_id = shard_id
        self.rate = rate
        self.interval = 1.0 / rate
        self.client = client
        self.wallet = wallet
        self.address_pool = address_pool
        self.amount_btc = amount_btc
        self.timeout = timeout
        self.num_workers = num_workers
        
        # Queue for TX requests
        self.tx_queue: queue.Queue[Optional[TxRequest]] = queue.Queue(maxsize=queue_size)
        
        # Results queue for logging
        self.result_queue: queue.Queue[TxResult] = queue.Queue()
        
        # Counters (thread-safe)
        self._seq_counter = 0
        self._seq_lock = threading.Lock()
        self._sent_count = 0
        self._sent_lock = threading.Lock()
        self._error_count = 0
        self._skipped_count = 0
        
        # Control
        self.running = False
        self.start_time = 0.0
        
        # Workers
        self.executor: Optional[ThreadPoolExecutor] = None
        self.scheduler_thread: Optional[threading.Thread] = None
        self.logger_thread: Optional[threading.Thread] = None
    
    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq_counter += 1
            return self._seq_counter
    
    def _inc_sent(self) -> int:
        with self._sent_lock:
            self._sent_count += 1
            return self._sent_count
    
    @property
    def sent_count(self) -> int:
        with self._sent_lock:
            return self._sent_count
    
    def scheduler_loop(self):
        """
        Strict scheduler: Adds TX requests to queue at exact intervals.
        This is the "metronome" that guarantees constant rate.
        """
        next_tick = time.perf_counter()
        
        while self.running:
            now = time.perf_counter()
            
            # Wait until next tick
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.001))
                continue
            
            # Time to schedule a TX
            seq = self._next_seq()
            addr = self.address_pool.get_next()
            
            request = TxRequest(
                seq_num=seq,
                scheduled_time=time.time(),
                dest_address=addr,
            )
            
            try:
                # Non-blocking put - if queue is full, we skip this slot
                self.tx_queue.put_nowait(request)
            except queue.Full:
                self._skipped_count += 1
                if self._skipped_count % 10 == 1:
                    print(f"⚠️  Shard {self.shard_id}: Queue voll, TX #{seq} übersprungen "
                          f"(bisher {self._skipped_count} übersprungen)")
            
            next_tick += self.interval
    
    def sender_worker(self, worker_id: int):
        """
        Worker: Pulls TX requests from queue and sends them.
        Multiple workers run in parallel to absorb RPC latency.
        """
        while self.running:
            try:
                request = self.tx_queue.get(timeout=0.5)
                if request is None:  # Shutdown signal
                    break
                
                # Send the transaction
                start = time.perf_counter()
                sent_time = time.time()
                
                try:
                    txid = self.client.call(
                        "sendtoaddress",
                        [request.dest_address, self.amount_btc],
                        wallet=self.wallet,
                        timeout=self.timeout,
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
                    
                    # Handle insufficient funds
                    if code == -6 or (message and "insufficient funds" in message.lower()):
                        time.sleep(1.0)
                
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
        """
        Logger: Processes results from workers and writes to files.
        Runs in a separate thread to avoid blocking workers.
        """
        throughput_window: Deque[float] = deque()
        window_duration = 10  # seconds
        last_log_count = 0
        
        while self.running or not self.result_queue.empty():
            try:
                result = self.result_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            
            now = time.time()
            
            # Update throughput window
            if result.success:
                throughput_window.append(result.sent_time)
            while throughput_window and now - throughput_window[0] > window_duration:
                throughput_window.popleft()
            
            # Calculate rolling throughput
            if len(throughput_window) > 1:
                span = throughput_window[-1] - throughput_window[0]
                rtps = (len(throughput_window) - 1) / span if span > 0 else 0.0
            else:
                rtps = 0.0
            
            timestamp = datetime.now(timezone.utc).isoformat()
            
            if result.success:
                # Write to txlog
                txlog.write(f"{result.seq_num},{self.shard_id},{timestamp},{result.txid}\n")
                txlog.flush()
                
                # Write to perflog
                perflog.write(
                    f"{result.seq_num},{timestamp},{result.latency_ms:.2f},{rtps:.4f},"
                    f"0,0,0,0.0,ok\n"
                )
                perflog.flush()
                
                # Periodic status log
                current_sent = self.sent_count
                if current_sent >= last_log_count + 50:
                    last_log_count = (current_sent // 50) * 50
                    elapsed = now - self.start_time
                    expected = elapsed * self.rate
                    achieved_pct = (current_sent / expected * 100) if expected > 0 else 100.0
                    queue_size = self.tx_queue.qsize()
                    
                    print(f"📤 Shard {self.shard_id}: {current_sent} TX, "
                          f"Latenz {result.latency_ms:.1f}ms, "
                          f"Rolling TPS {rtps:.4f} (Ziel: {self.rate:.4f}), "
                          f"Erreicht: {achieved_pct:.1f}%, Queue: {queue_size}")
            else:
                # Write to error log
                safe_message = (result.error_message or "").replace('"', '""')
                errlog.write(
                    f"{timestamp},{result.error_code if result.error_code else ''},"
                    f'"{safe_message}",seq={result.seq_num}\n'
                )
                errlog.flush()
                
                # Write to perflog
                perflog.write(
                    f"{result.seq_num},{timestamp},{result.latency_ms:.2f},{rtps:.4f},"
                    f"0,0,0,0.0,error\n"
                )
                perflog.flush()
    
    def start(self, txlog, perflog, errlog):
        """Start the generator with all threads."""
        self.running = True
        self.start_time = time.time()
        
        # Start worker pool
        self.executor = ThreadPoolExecutor(max_workers=self.num_workers)
        for i in range(self.num_workers):
            self.executor.submit(self.sender_worker, i)
        
        # Start scheduler
        self.scheduler_thread = threading.Thread(target=self.scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        
        # Start logger
        self.logger_thread = threading.Thread(
            target=self.logger_loop, 
            args=(txlog, perflog, errlog),
            daemon=True
        )
        self.logger_thread.start()
        
        print(f"🚀 Shard {self.shard_id}: Gestartet mit {self.num_workers} Workers, "
              f"Zielrate {self.rate:.4f} tx/s")
    
    def stop(self):
        """Stop all threads gracefully."""
        self.running = False
        
        # Send shutdown signals to workers
        for _ in range(self.num_workers):
            try:
                self.tx_queue.put_nowait(None)
            except queue.Full:
                pass
        
        # Wait for threads
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=2.0)
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
        if self.logger_thread:
            self.logger_thread.join(timeout=5.0)
        
        # Final stats
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


def prime_address_pool(size: int, fetcher: Callable[[], str]) -> List[str]:
    addresses = []
    for idx in range(size):
        if idx and idx % 200 == 0:
            print(f"   … {idx} Adressen vorbereitet")
        addr = fetcher()
        addresses.append(addr)
    return addresses


def main() -> int:
    parser = argparse.ArgumentParser(description="FaultLab Tx Generator - Async Strict-Rate")
    parser.add_argument("--shard-id", required=True, help="Shard-Kennung (z.B. a, b)")
    parser.add_argument("--rate", type=float, required=True, help="Transaktionen pro Sekunde")
    parser.add_argument("--rpc", required=True, help="RPC URL inkl. Credentials")
    parser.add_argument("--wallet", required=True, help="Wallet-Name")
    parser.add_argument("--log", required=True, help="Pfad zur Transaktions-Logdatei")
    parser.add_argument("--node-count", type=int, required=True, help="Anzahl Knoten im Netz")
    parser.add_argument("--address-pool-size", type=int, default=1024, help="Anzahl Adressen")
    parser.add_argument("--address-reuse-window", type=int, default=100, help="Adress-Rotation")
    parser.add_argument("--amount-btc", type=float, default=0.0001, help="Betrag pro TX")
    parser.add_argument("--utxo-target", type=int, default=800, help="Min. bestätigte UTXOs")
    parser.add_argument("--num-workers", type=int, default=4, help="Anzahl paralleler Sender")
    parser.add_argument("--queue-size", type=int, default=200, help="TX-Queue Größe")
    parser.add_argument("--stats-interval", type=int, default=200, help="(unused)")
    parser.add_argument("--throughput-window", type=int, default=10, help="(unused)")
    args = parser.parse_args()

    if args.rate <= 0:
        raise ValueError("Rate muss > 0 sein")

    # Parse RPC URL
    proto, rest = args.rpc.split("://", 1)
    if proto != "http":
        raise ValueError("Nur http:// Endpunkte werden unterstützt")
    credential, host = rest.split("@", 1)
    rpc_url = f"http://{host}"
    auth = credential

    ensure_log_directory(args.log)
    performance_log = args.log.replace(".csv", "_performance.csv")
    error_log = args.log.replace(".csv", "_errors.csv")

    client = PersistentRPCClient(rpc_url, auth)

    wait_for_rpc(client, args.node_count)
    timeout = rpc_timeout_for(args.node_count)
    ensure_wallet_loaded(client, args.wallet, timeout)

    # Wait for UTXOs
    print(f"⏳ Warte auf {args.utxo_target} bestätigte UTXOs...")
    max_utxo_wait = 600
    utxo_wait_start = time.time()
    
    while time.time() - utxo_wait_start < max_utxo_wait:
        utxo_stats = fetch_wallet_utxos(client, args.wallet, timeout, min_conf=1)
        print(f"💰 Wallet '{args.wallet}': {utxo_stats['confirmed']} bestätigte / "
              f"{utxo_stats['total']} gesamt – {utxo_stats['balance']:.8f} BTC")
        
        if utxo_stats["confirmed"] >= args.utxo_target:
            print(f"✅ Genug UTXOs ({utxo_stats['confirmed']} ≥ {args.utxo_target})")
            break
        
        time.sleep(5)
    else:
        raise RuntimeError(f"Nicht genug UTXOs nach {max_utxo_wait}s")

    # Prime address pool
    print(f"🎯 Starte Adress-Pooling ({args.address_pool_size})…")
    
    def fetch_address() -> str:
        return rpc_call_with_retry(client, "getnewaddress", [], wallet=args.wallet, timeout=timeout)
    
    initial_addresses = prime_address_pool(args.address_pool_size, fetch_address)
    address_pool = AddressPool(initial_addresses, fetch_address, args.address_reuse_window)
    print(f"✅ Adress-Pool initialisiert ({len(address_pool)} Einträge)")

    # Create generator
    generator = StrictRateTxGenerator(
        shard_id=args.shard_id,
        rate=args.rate,
        client=client,
        wallet=args.wallet,
        address_pool=address_pool,
        amount_btc=args.amount_btc,
        timeout=timeout,
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

    print(f"⚡ Shard {args.shard_id}: Zielrate {args.rate:.4f} tx/s (async, {args.num_workers} workers)")

    try:
        with open(args.log, "w", encoding="utf-8") as txlog, \
             open(performance_log, "w", encoding="utf-8") as perflog, \
             open(error_log, "w", encoding="utf-8") as errlog:
            
            txlog.write(txlog_header)
            perflog.write(perflog_header)
            errlog.write(errorlog_header)
            
            generator.start(txlog, perflog, errlog)
            
            # Keep main thread alive
            while True:
                time.sleep(1.0)
                
    except KeyboardInterrupt:
        generator.stop()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("⏹️  Txgen beendet")
        sys.exit(0)
