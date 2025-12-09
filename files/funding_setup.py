import argparse
import base64
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPConnection
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


JSONRPC_HEADERS = {"Content-Type": "application/json"}


def encode_basic_auth(credential: str) -> str:
    return base64.b64encode(credential.encode("utf-8")).decode("ascii")


def rpc_timeout_for(node_count: int) -> int:
    if node_count >= 128:
        return 20
    if node_count >= 64:
        return 15
    return 10


def rpc_call(rpc_url: str, method: str, params=None, *, auth: Optional[str], wallet: Optional[str] = None,
             timeout: int = 10):
    proto, rest = rpc_url.split("://", 1)
    if proto != "http":
        raise ValueError("Only http RPC endpoints are supported")
    conn = HTTPConnection(rest, timeout=timeout)
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "funding",
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


def rpc_call_with_retry(rpc_url: str, method: str, params=None, *, auth: Optional[str], wallet: Optional[str],
                        timeout: int, retries: int = 3, delay_s: float = 1.5):
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return rpc_call(rpc_url, method, params, auth=auth, wallet=wallet, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt == retries:
                raise
            time.sleep(delay_s)
    raise last_err  # pragma: no cover


def decode_rpc_error(err: RuntimeError) -> Tuple[Optional[int], str]:
    if not err.args:
        return None, ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("code"), payload.get("message", "")
    return None, str(payload)


def wait_for_rpc(rpc_url: str, auth: str, timeout_s: int = 180) -> None:
    deadline = time.time() + timeout_s
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            rpc_call(rpc_url, "getblockcount", auth=auth, timeout=5)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2.0)
    raise RuntimeError(f"RPC {rpc_url} not reachable: {last_err}")


def format_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_miners(node_count: int, percentage: float) -> List[str]:
    """
    Berechnet die Liste der Mining-Nodes.
    
    Wenn percentage >= 1.0, werden alle Nodes als Miner verwendet.
    Bei percentage < 1.0 wird der Anteil berechnet, mit einer Mindestanzahl von 2.
    """
    if percentage >= 1.0:
        count = node_count
    else:
        count = max(2, int(node_count * percentage))
    return [f"node{i:02d}:18443" for i in range(1, count + 1)]


def chunked(seq: Sequence[str], size: int) -> Iterable[List[str]]:
    for idx in range(0, len(seq), size):
        yield list(seq[idx: idx + size])


def fetch_wallet_utxos(rpc_url: str, auth: str, wallet: str, timeout: int, min_conf: int = 0) -> Dict[str, float]:
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


def ensure_wallet(rpc_url: str, auth: str, wallet: str, timeout: int, descriptors: bool = True) -> None:
    params = [wallet, False, False, "", False, descriptors, True, False]
    try:
        rpc_call(rpc_url, "createwallet", params, auth=auth, timeout=timeout)
        print(f"{format_ts()} 🖿 Wallet '{wallet}' erstellt ({rpc_url})")
    except RuntimeError as err:
        code, message = decode_rpc_error(err)
        message_lower = message.lower()
        if code in (-4, -35) or "already exists" in message_lower:
            print(f"{format_ts()} ℹ️ Wallet '{wallet}' existiert bereits")
        else:
            raise
    try:
        rpc_call(rpc_url, "loadwallet", [wallet], auth=auth, timeout=timeout)
    except RuntimeError as err:
        code, message = decode_rpc_error(err)
        if code in (-35, -4) or "already loaded" in message.lower():
            return
        raise


def mine_blocks(miner_hosts: Sequence[str], total_blocks: int, auth: str, address: str, timeout: int) -> List[str]:
    if total_blocks <= 0 or not miner_hosts:
        return []
    blocks: List[str] = []
    per_miner = total_blocks // len(miner_hosts)
    remainder = total_blocks % len(miner_hosts)
    for idx, miner in enumerate(miner_hosts):
        count = per_miner + (1 if idx < remainder else 0)
        if count <= 0:
            continue
        rpc_url = f"http://{miner}"
        print(f"{format_ts()} ⛏️  Miner {miner} erzeugt {count} Blöcke auf Adresse {address}")
        result = rpc_call(rpc_url, "generatetoaddress", [count, address], auth=auth, timeout=timeout)
        blocks.extend(result or [])
    return blocks


@dataclass
class ShardSpec:
    shard_id: str
    host: str
    wallet: str

    @property
    def rpc_url(self) -> str:
        return f"http://{self.host}"


def parse_shard(spec: str) -> ShardSpec:
    data: Dict[str, str] = {}
    for part in spec.split(","):
        if "=" not in part:
            raise ValueError(f"Shard Angabe fehlerhaft: '{spec}'")
        key, value = part.split("=", 1)
        data[key.strip()] = value.strip()
    for key in ("id", "host", "wallet"):
        if key not in data:
            raise ValueError(f"Shard Angabe '{spec}' fehlt Feld '{key}'")
    return ShardSpec(data["id"], data["host"], data["wallet"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Bereitet Funding- und Shard-Wallets vor")
    parser.add_argument("--rpc-user", required=True, help="RPC Benutzername")
    parser.add_argument("--rpc-pass", required=True, help="RPC Passwort")
    parser.add_argument("--funding-host", default="wallet_funding:18443",
                        help="Host:Port des Funding-Wallets (default wallet_funding:18443)")
    parser.add_argument("--funding-wallet", default="funding", help="Wallet-Name im Funding-Container")
    parser.add_argument("--shard", action="append", dest="shards", default=[],
                        help="Shard Definition (id=a,host=wallet_shard_a:18443,wallet=txshard_a)")
    parser.add_argument("--node-count", type=int, required=True, help="Gesamtzahl der Knoten")
    parser.add_argument("--mining-percentage", type=float, default=0.08, help="Anteil Mining-Knoten")
    parser.add_argument("--utxo-per-shard", type=int, default=800, help="Zielanzahl UTXOs je Shard")
    parser.add_argument("--utxo-amount", type=float, default=0.0002, help="Betrag pro vorbereiteter UTXO (BTC)")
    parser.add_argument("--address-batch", type=int, default=50,
                        help="Anzahl Adressen pro sendmany Batch")
    parser.add_argument("--confirmation-blocks", type=int, default=12,
                        help="Anzahl Blöcke zum Bestätigen der Transfers")
    parser.add_argument("--state-dir", default="/state", help="Ablageort für Snapshot-/Config-Dateien")
    parser.add_argument("--scheduler-config", default="/state/mining_targets.json",
                        help="Ausgabedatei für Scheduler-Konfiguration")
    parser.add_argument("--summary-file", default="/state/funding_snapshot.json",
                        help="Zusammenfassung sämtlicher Wallet-Statistiken")
    parser.add_argument("--scheduler-interval", type=float, required=True,
                        help="Block-Intervall für den Scheduler in Sekunden")
    parser.add_argument("--scheduler-seed", type=int, default=1337,
                        help="Seed für deterministische Miner-Rotation")
    parser.add_argument("--miner-host-override", default=None,
                        help="Wenn gesetzt, nutzt der Funding-Workflow Host-Ports (z. B. 127.0.0.1)")
    parser.add_argument("--miner-port-base", type=int, default=20443,
                        help="Basisport für Miner auf dem Host (nur mit --miner-host-override)")
    parser.add_argument("--miner-port-step", type=int, default=4,
                        help="Port-Inkrement pro Miner (nur mit --miner-host-override)")
    args = parser.parse_args()

    if args.utxo_per_shard <= 0:
        raise ValueError("utxo-per-shard muss > 0 sein")
    if args.utxo_amount <= 0:
        raise ValueError("utxo-amount muss > 0 sein")
    if args.address_batch <= 0:
        raise ValueError("address-batch muss > 0 sein")
    if args.scheduler_interval <= 0:
        raise ValueError("scheduler-interval muss > 0 sein")

    if not args.shards:
        raise ValueError("Mindestens ein --shard Eintrag erforderlich")

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    timeout = rpc_timeout_for(args.node_count)

    funding_rpc = f"http://{args.funding_host}"
    shard_specs = [parse_shard(spec) for spec in args.shards]

    print(f"{format_ts()} 🚀 Funding-Setup startet – {len(shard_specs)} Shards")

    wait_for_rpc(funding_rpc, auth, timeout_s=300)
    for shard in shard_specs:
        wait_for_rpc(shard.rpc_url, auth, timeout_s=300)

    ensure_wallet(funding_rpc, auth, args.funding_wallet, timeout, descriptors=True)
    for shard in shard_specs:
        ensure_wallet(shard.rpc_url, auth, shard.wallet, timeout, descriptors=True)

    miners_container = compute_miners(args.node_count, args.mining_percentage)

    if args.miner_host_override:
        miners_rpc = []
        for idx, _ in enumerate(miners_container, start=1):
            port = args.miner_port_base + (idx - 1) * args.miner_port_step
            miners_rpc.append(f"{args.miner_host_override}:{port}")
        print(f"{format_ts()} ⚙️  Miner (Container): {', '.join(miners_container)}")
        print(f"{format_ts()} 🔌 Miner (Host-RPC): {', '.join(miners_rpc)}")
    else:
        miners_rpc = miners_container
        print(f"{format_ts()} ⚙️  Verwende Miner: {', '.join(miners_container)}")

    mining_address = rpc_call_with_retry(
        funding_rpc, "getnewaddress", [], auth=auth, wallet=args.funding_wallet, timeout=timeout
    )
    print(f"{format_ts()} 🎯 Mining-Adresse: {mining_address}")

    print(f"{format_ts()} 🪨 Mine Initialblöcke (201 + 100 zur Reife)")
    mine_blocks(miners_rpc, 201, auth, mining_address, timeout)
    mine_blocks(miners_rpc, 100, auth, mining_address, timeout)

    addresses_per_shard: Dict[str, List[str]] = {}
    for shard in shard_specs:
        print(f"{format_ts()} 📮 Erzeuge {args.utxo_per_shard} Zieladressen für Shard {shard.shard_id}")
        addr_list: List[str] = []
        for idx in range(args.utxo_per_shard):
            if idx and idx % 200 == 0:
                print(f"{format_ts()}   … {idx} Adressen erzeugt")
            addr = rpc_call_with_retry(
                shard.rpc_url, "getnewaddress", [], auth=auth, wallet=shard.wallet, timeout=timeout
            )
            addr_list.append(addr)
        addresses_per_shard[shard.shard_id] = addr_list

    print(f"{format_ts()} 🚚 Verteile Mittel auf Shards (Batches zu {args.address_batch})")
    total_batches = 0
    for shard in shard_specs:
        addr_list = addresses_per_shard[shard.shard_id]
        batches = list(chunked(addr_list, args.address_batch))
        total_batches += len(batches)
        for batch_idx, batch in enumerate(batches):
            outputs = {addr: round(args.utxo_amount, 8) for addr in batch}
            rpc_call_with_retry(
                funding_rpc, "sendmany", ["", outputs, 1],
                auth=auth, wallet=args.funding_wallet, timeout=timeout
            )
            # Mine nach jedem 4. Batch, um Mempool-Druck zu vermeiden
            if (batch_idx + 1) % 4 == 0:
                print(f"{format_ts()}   ⛏️  Mine 2 Blöcke nach Batch {batch_idx + 1}/{len(batches)}")
                mine_blocks(miners_rpc, 2, auth, mining_address, timeout)
        print(f"{format_ts()} ✅ Shard {shard.shard_id}: {len(addr_list)} Outputs queued")

    # Berechne benötigte Blöcke: mindestens so viele wie Batches, plus großzügigen Puffer
    # Für größere UTXO-Zahlen (z.B. 2400) brauchen wir mehr Blöcke
    # Formel: Batches + Puffer, wobei Puffer proportional zur UTXO-Anzahl ist
    utxo_buffer = max(20, args.utxo_per_shard // 50)  # Mindestens 20, mehr bei vielen UTXOs
    blocks_needed = max(args.confirmation_blocks, total_batches + utxo_buffer)
    print(f"{format_ts()} 🪙 Mine {blocks_needed} Blöcke zur finalen Bestätigung der Transfers "
          f"({total_batches} Batches + {utxo_buffer} Puffer)")
    mine_blocks(miners_rpc, blocks_needed, auth, mining_address, timeout)

    funding_stats = fetch_wallet_utxos(funding_rpc, auth, args.funding_wallet, timeout, min_conf=1)
    print(f"{format_ts()} 💰 Funding-Wallet nach Verteilung: Balance {funding_stats['balance']:.8f} BTC "
          f"({funding_stats['confirmed']} bestätigte UTXOs)")

    # Warte auf Synchronisation der Shard-Wallets
    print(f"{format_ts()} ⏳ Warte auf Synchronisation der Shard-Wallets...")
    # Timeout proportional zur UTXO-Anzahl UND Block-Intervall: mehr UTXOs = mehr Zeit
    # Bei 12s Block-Intervall dauert alles doppelt so lange wie bei 6s
    base_timeout = 300
    extra_utxos = max(0, args.utxo_per_shard - 800)
    extra_timeout = (extra_utxos // 400) * 60
    # Skaliere Timeout basierend auf Block-Intervall (6s = 1.0x, 12s = 2.0x, etc.)
    interval_multiplier = args.scheduler_interval / 6.0
    max_wait = int((base_timeout + extra_timeout) * interval_multiplier)
    print(f"{format_ts()}   Timeout: {max_wait}s (Basis: {base_timeout}s + {extra_timeout}s für {args.utxo_per_shard} UTXOs, "
          f"Block-Intervall: {args.scheduler_interval}s → {interval_multiplier:.1f}x Multiplikator)")
    wait_start = time.time()
    all_synced = False
    last_status = {}
    last_mined = 0
    check_interval = 10  # Alle 10 Sekunden prüfen
    
    while time.time() - wait_start < max_wait:
        all_synced = True
        min_utxos = float('inf')
        for shard in shard_specs:
            stats = fetch_wallet_utxos(shard.rpc_url, auth, shard.wallet, timeout, min_conf=1)
            last_status[shard.shard_id] = stats["confirmed"]
            min_utxos = min(min_utxos, stats["confirmed"])
            if stats["confirmed"] < args.utxo_per_shard:
                all_synced = False
        if all_synced:
            print(f"{format_ts()} ✅ Alle Shards synchronisiert!")
            break
        
        # Status alle 10 Sekunden ausgeben
        elapsed = int(time.time() - wait_start)
        if elapsed % check_interval == 0:
            status_str = ", ".join([f"{k}: {v}/{args.utxo_per_shard}" for k, v in last_status.items()])
            print(f"{format_ts()}   ⏳ Warte... ({elapsed}s) – Status: {status_str}")
            
            # Wenn nach 30 Sekunden noch nicht alle UTXOs da sind, minen wir mehr Blöcke
            if elapsed >= 30 and min_utxos < args.utxo_per_shard and elapsed - last_mined >= 30:
                missing = args.utxo_per_shard - min_utxos
                # Bei vielen fehlenden UTXOs mehr Blöcke minen
                extra_blocks = max(5, missing // 30)  # Mindestens 5 Blöcke, mehr wenn viele fehlen
                print(f"{format_ts()}   ⛏️  Minen {extra_blocks} zusätzliche Blöcke zur Synchronisation...")
                mine_blocks(miners_rpc, extra_blocks, auth, mining_address, timeout)
                last_mined = elapsed
        
        time.sleep(2)
    
    if not all_synced:
        print(f"{format_ts()} ⚠️  Wartezeit abgelaufen, prüfe aktuellen Status...")
        for shard_id, count in last_status.items():
            print(f"{format_ts()}   Shard {shard_id}: {count}/{args.utxo_per_shard} UTXOs")

    shard_stats = []
    for shard in shard_specs:
        stats = fetch_wallet_utxos(shard.rpc_url, auth, shard.wallet, timeout, min_conf=1)
        print(f"{format_ts()} 📊 Shard {shard.shard_id}: {stats['confirmed']} bestätigte "
              f"({stats['total']} gesamt) – Balance {stats['balance']:.6f} BTC")
        if stats["confirmed"] < args.utxo_per_shard:
            raise RuntimeError(
                f"Shard {shard.shard_id} hat nur {stats['confirmed']} bestätigte UTXOs "
                f"(erwartet ≥ {args.utxo_per_shard})"
            )
        stats.update({"id": shard.shard_id, "wallet": shard.wallet, "host": shard.host})
        shard_stats.append(stats)

    os.makedirs(args.state_dir, exist_ok=True)
    scheduler_payload = {
        "generated_at": format_ts(),
        "mining_address": mining_address,
        "miner_hosts": miners_container,
        "interval_s": args.scheduler_interval,
        "seed": args.scheduler_seed,
        "funding_wallet": {
            "name": args.funding_wallet,
            "balance_btc": funding_stats["balance"],
            "utxos_confirmed": funding_stats["confirmed"],
        },
        "shards": shard_stats,
    }
    # Scheduler-Config übernimmt endgültige Intervall/Seed Änderungen
    with open(args.scheduler_config, "w", encoding="utf-8") as handle:
        json.dump(scheduler_payload, handle, indent=2)
    print(f"{format_ts()} 💾 Scheduler-Konfiguration geschrieben nach {args.scheduler_config}")

    summary = {
        "generated_at": format_ts(),
        "funding_wallet": {
            "name": args.funding_wallet,
            "rpc": funding_rpc,
            **funding_stats,
        },
        "shards": shard_stats,
    }
    with open(args.summary_file, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    print(f"{format_ts()} ✅ Funding abgeschlossen – Zusammenfassung in {args.summary_file}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"{format_ts()} ⏹️ Funding abgebrochen (KeyboardInterrupt)")
        sys.exit(1)

