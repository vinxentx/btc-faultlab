import argparse
import base64
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from http.client import HTTPConnection
from typing import Deque, Dict, Tuple, Callable, Optional


JSONRPC_HEADERS = {"Content-Type": "application/json"}


def rpc_timeout_for(node_count: int) -> int:
    if node_count >= 128:
        return 20
    if node_count >= 64:
        return 15
    return 10


def encode_basic_auth(credential: str) -> str:
    return base64.b64encode(credential.encode("utf-8")).decode("ascii")


def rpc_call(rpc_url: str, method: str, params=None, *, auth: Optional[str] = None,
             wallet: Optional[str] = None, timeout: int = 10):
    proto, rest = rpc_url.split("://", 1)
    if proto != "http":
        raise ValueError("Only http RPC endpoints are supported")
    conn = HTTPConnection(rest, timeout=timeout)
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "txgen",
        "method": method,
        "params": params or []
    })
    headers = dict(JSONRPC_HEADERS)
    if auth:
        headers["Authorization"] = "Basic " + encode_basic_auth(auth)
    path = f"/wallet/{wallet}" if wallet else "/"
    try:
        conn.request("POST", path, payload, headers)
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def rpc_call_with_retry(rpc_url: str, method: str, params=None, *, auth: Optional[str],
                        wallet: Optional[str], timeout: int, retries: int = 3,
                        delay_s: float = 1.5):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return rpc_call(rpc_url, method, params, auth=auth, wallet=wallet, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - want to capture RuntimeError payload
            last_err = exc
            if attempt == retries:
                raise
            time.sleep(delay_s)
    raise last_err  # pragma: no cover


def wait_for_rpc(rpc_url: str, auth: str, node_count: int) -> None:
    base_timeout = 300 if node_count >= 128 else 180 if node_count >= 64 else 120
    deadline = time.time() + base_timeout
    print(f"⏳ Warte auf RPC-Endpunkt {rpc_url} (Timeout {base_timeout}s)...")
    last_err = None
    while time.time() < deadline:
        try:
            rpc_call(rpc_url, "getblockchaininfo", auth=auth, timeout=5)
            print("✅ RPC erreichbar")
            return
        except Exception as exc:  # noqa: BLE001
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


def ensure_wallet_loaded(rpc_url: str, auth: str, wallet: str, timeout: int) -> None:
    """Warte auf Wallet-Erstellung durch Funding-Setup mit Retry-Logik"""
    max_wait = 600  # Maximal 10 Minuten warten
    wait_start = time.time()
    last_error = None
    
    while time.time() - wait_start < max_wait:
        try:
            rpc_call(rpc_url, "loadwallet", [wallet], auth=auth, timeout=timeout)
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
                if elapsed % 30 == 0:  # Alle 30 Sekunden loggen
                    print(f"⏳ Warte auf Wallet '{wallet}'... ({elapsed}s) – Funding-Setup läuft noch")
                time.sleep(5)  # 5 Sekunden warten vor Retry
                continue
            else:
                raise
    
    # Wenn wir hier ankommen, hat das Warten nicht geholfen
    raise RuntimeError(
        f"Wallet '{wallet}' nicht gefunden nach {max_wait}s Wartezeit – "
        f"Funding-Setup wurde vermutlich nicht ausgeführt oder fehlgeschlagen."
    ) from last_error


def fetch_wallet_utxos(rpc_url: str, auth: str, wallet: str, timeout: int,
                       min_conf: int = 0) -> Dict[str, float]:
    utxos = rpc_call_with_retry(
        rpc_url, "listunspent", [min_conf, 9999999], auth=auth, wallet=wallet, timeout=timeout
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
    def __init__(self, initial: Deque[str], fetcher: Callable[[], str], reuse_window: int):
        if not initial:
            raise ValueError("Adress-Pool darf nicht leer sein")
        self._queue: Deque[str] = deque(initial)
        self._fetcher = fetcher
        self._reuse_window = max(1, reuse_window)
        self._success_counter = 0

    def current(self) -> str:
        if not self._queue:
            self._queue.append(self._fetcher())
        return self._queue[0]

    def mark_success(self) -> None:
        addr = self._queue.popleft()
        self._success_counter += 1
        if self._reuse_window > 0 and self._success_counter >= self._reuse_window:
            fresh = self._fetcher()
            self._queue.append(fresh)
            self._success_counter = 0
        else:
            self._queue.append(addr)

    def mark_failure(self) -> None:
        # Adresse verbleibt vorne in der Queue für den nächsten Versuch
        pass

    def __len__(self) -> int:
        return len(self._queue)


def prime_address_pool(size: int, fetcher: Callable[[], str]) -> Deque[str]:
    addresses: Deque[str] = deque()
    for idx in range(size):
        if idx and idx % 200 == 0:
            print(f"   … {idx} Adressen vorbereitet")
        addr = fetcher()
        addresses.append(addr)
    return addresses


def ensure_log_directory(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def rolling_throughput(window: Deque[float], now: float, duration_s: int) -> float:
    window.append(now)
    while window and now - window[0] > duration_s:
        window.popleft()
    if len(window) <= 1:
        return 0.0
    span = window[-1] - window[0]
    if span <= 0:
        return 0.0
    return (len(window) - 1) / span


def main() -> int:
    parser = argparse.ArgumentParser(description="FaultLab Tx Generator Shard")
    parser.add_argument("--shard-id", required=True, help="Shard-Kennung (z.B. a, b)")
    parser.add_argument("--rate", type=float, required=True, help="Transaktionen pro Sekunde")
    parser.add_argument("--rpc", required=True, help="RPC URL inkl. Credentials (http://user:pass@host:port)")
    parser.add_argument("--wallet", required=True, help="Wallet-Name innerhalb des RPC-Endpunkts")
    parser.add_argument("--log", required=True, help="Pfad zur Transaktions-Logdatei (*.csv)")
    parser.add_argument("--node-count", type=int, required=True, help="Gesamtzahl der Knoten im Netz")
    parser.add_argument("--address-pool-size", type=int, default=1024, help="Anzahl vorbereiteter Adressen")
    parser.add_argument("--address-reuse-window", type=int, default=100,
                        help="Nach wie vielen TX eine Adresse durch eine neue ersetzt wird")
    parser.add_argument("--amount-btc", type=float, default=0.0001, help="Betrag pro Transaktion in BTC")
    parser.add_argument("--utxo-target", type=int, default=800,
                        help="Erwartete Mindestanzahl bestätigter UTXOs vor Start")
    parser.add_argument("--stats-interval", type=int, default=200,
                        help="Alle X erfolgreichen TX werden UTXO-Statistiken erhoben")
    parser.add_argument("--throughput-window", type=int, default=30,
                        help="Fenstergröße für Rolling Throughput in Sekunden")
    args = parser.parse_args()

    if args.rate <= 0:
        raise ValueError("Rate muss > 0 sein")
    if args.address_pool_size <= 0:
        raise ValueError("address-pool-size muss > 0 sein")

    proto, rest = args.rpc.split("://", 1)
    if proto != "http":
        raise ValueError("Nur http:// Endpunkte werden unterstützt")
    credential, host = rest.split("@", 1)
    rpc_url = f"http://{host}"
    auth = credential

    ensure_log_directory(args.log)
    performance_log = args.log.replace(".csv", "_performance.csv")
    error_log = args.log.replace(".csv", "_errors.csv")

    wait_for_rpc(rpc_url, auth, args.node_count)
    timeout = rpc_timeout_for(args.node_count)
    ensure_wallet_loaded(rpc_url, auth, args.wallet, timeout)

    # Warte auf genug UTXOs (Funding-Setup läuft möglicherweise noch)
    print(f"⏳ Warte auf {args.utxo_target} bestätigte UTXOs in Wallet '{args.wallet}'...")
    max_utxo_wait = 600  # Maximal 10 Minuten warten
    utxo_wait_start = time.time()
    
    while time.time() - utxo_wait_start < max_utxo_wait:
        utxo_stats = fetch_wallet_utxos(rpc_url, auth, args.wallet, timeout, min_conf=1)
        print(f"💰 Wallet '{args.wallet}': {utxo_stats['confirmed']} bestätigte / "
              f"{utxo_stats['total']} gesamt – Kontostand {utxo_stats['balance']:.8f} BTC")
        
        if utxo_stats["confirmed"] >= args.utxo_target:
            print(f"✅ Genug UTXOs vorhanden ({utxo_stats['confirmed']} ≥ {args.utxo_target})")
            break
        
        elapsed = int(time.time() - utxo_wait_start)
        if elapsed % 30 == 0:  # Alle 30 Sekunden loggen
            print(f"⏳ Warte auf UTXOs... ({elapsed}s) – Funding-Setup synchronisiert noch")
        time.sleep(5)  # 5 Sekunden warten vor Retry
    else:
        # Timeout erreicht
        raise RuntimeError(
            f"Wallet '{args.wallet}' hat nur {utxo_stats['confirmed']} bestätigte UTXOs "
            f"nach {max_utxo_wait}s Wartezeit (erwartet ≥ {args.utxo_target}). "
            f"Funding-Setup prüfen."
        )

    print(f"🎯 Starte Adress-Pooling (Poolgröße {args.address_pool_size})…")

    def fetch_address() -> str:
        return rpc_call_with_retry(
            rpc_url, "getnewaddress", [], auth=auth, wallet=args.wallet, timeout=timeout
        )

    initial_addresses = prime_address_pool(args.address_pool_size, fetch_address)
    address_pool = AddressPool(initial_addresses, fetch_address, args.address_reuse_window)
    print(f"✅ Adress-Pool initialisiert ({len(address_pool)} Einträge)")

    interval = 1.0 / args.rate
    txlog_header = "tx_index,shard_id,submit_ts_utc,txid\n"
    perflog_header = (
        "tx_index,timestamp_utc,latency_ms,rolling_throughput_tx_s,"
        "utxos_confirmed,utxos_unconfirmed,utxos_total,balance_btc,status\n"
    )
    errorlog_header = "timestamp_utc,error_code,error_message,context\n"

    throughput_window: Deque[float] = deque()
    tx_index = 0
    last_stats = utxo_stats

    next_tick = time.perf_counter()
    print(f"⚡ Shard {args.shard_id}: Zielrate {args.rate:.4f} tx/s, Betrag {args.amount_btc} BTC")

    with open(args.log, "w", encoding="utf-8") as txlog, \
            open(performance_log, "w", encoding="utf-8") as perflog, \
            open(error_log, "w", encoding="utf-8") as errlog:
        txlog.write(txlog_header)
        perflog.write(perflog_header)
        errlog.write(errorlog_header)

        while True:
            now = time.perf_counter()
            sleep_for = next_tick - now
            if sleep_for > 0:
                time.sleep(min(sleep_for, 0.05))
                continue
            next_tick += interval

            dest_address = address_pool.current()

            start = time.perf_counter()
            wall_clock = time.time()
            try:
                txid = rpc_call_with_retry(
                    rpc_url,
                    "sendtoaddress",
                    [dest_address, args.amount_btc],
                    auth=auth,
                    wallet=args.wallet,
                    timeout=timeout,
                )
                latency_ms = (time.perf_counter() - start) * 1000
                tx_index += 1
                address_pool.mark_success()

                # Rolling throughput
                rtps = rolling_throughput(throughput_window, wall_clock, args.throughput_window)

                if args.stats_interval > 0 and tx_index % args.stats_interval == 0:
                    last_stats = fetch_wallet_utxos(rpc_url, auth, args.wallet, timeout, min_conf=0)

                timestamp = datetime.now(timezone.utc).isoformat()
                txlog.write(f"{tx_index},{args.shard_id},{timestamp},{txid}\n")
                txlog.flush()

                perflog.write(
                    f"{tx_index},{timestamp},{latency_ms:.2f},{rtps:.4f},"
                    f"{last_stats['confirmed']},{last_stats['unconfirmed']},"
                    f"{last_stats['total']},{last_stats['balance']:.8f},ok\n"
                )
                perflog.flush()

                if tx_index % 200 == 0:
                    print(f"📤 Shard {args.shard_id}: {tx_index} TX, Latenz {latency_ms:.1f} ms, "
                          f"Rolling TPS {rtps:.2f}, UTXOs {last_stats['confirmed']}/{last_stats['total']}")

            except RuntimeError as err:
                address_pool.mark_failure()
                code, message = decode_rpc_error(err)
                timestamp = datetime.now(timezone.utc).isoformat()
                safe_message = (message or "").replace('"', '""')
                errlog.write(f"{timestamp},{code if code is not None else ''},\"{safe_message}\","
                             f"tx_index={tx_index}\n")
                errlog.flush()

                rtps = rolling_throughput(throughput_window, wall_clock, args.throughput_window)
                perflog.write(
                    f"{tx_index},{timestamp},0.00,{rtps:.4f},"
                    f"{last_stats['confirmed']},{last_stats['unconfirmed']},"
                    f"{last_stats['total']},{last_stats['balance']:.8f},error\n"
                )
                perflog.flush()

                msg_lower = message.lower() if message else ""
                if code == -6 or "insufficient funds" in msg_lower:
                    print(f"⚠️  Shard {args.shard_id}: unzureichende Mittel – warte auf nächste Blöcke")
                    time.sleep(2.5)
                    last_stats = fetch_wallet_utxos(rpc_url, auth, args.wallet, timeout, min_conf=0)
                else:
                    print(f"⚠️  Shard {args.shard_id}: RPC-Fehler ({code}): {message}")
                    time.sleep(1.0)

                # Reset Zeitplanung, damit keine Altlasten aufholen müssen
                next_tick = max(next_tick, time.perf_counter() + interval)
            except KeyboardInterrupt:
                print("⏹️  Abbruchsignal erhalten – stoppe Shard sauber.")
                break

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("⏹️  Txgen beendet (KeyboardInterrupt)")
        sys.exit(0)
