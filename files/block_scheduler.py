import argparse
import base64
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from http.client import HTTPConnection
from typing import Iterable, List, Optional, Set, Tuple


JSONRPC_HEADERS = {"Content-Type": "application/json"}


def encode_basic_auth(credential: str) -> str:
    return base64.b64encode(credential.encode("utf-8")).decode("ascii")


def rpc_timeout_for(node_count: int) -> int:
    if node_count >= 128:
        return 20
    if node_count >= 64:
        return 15
    return 10


def rpc_call(rpc_url: str, method: str, params=None, *, auth: Optional[str], timeout: int = 10):
    proto, rest = rpc_url.split("://", 1)
    if proto != "http":
        raise ValueError("Only http RPC endpoints are supported")
    conn = HTTPConnection(rest, timeout=timeout)
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "scheduler",
        "method": method,
        "params": params or []
    })
    headers = dict(JSONRPC_HEADERS)
    if auth:
        headers["Authorization"] = "Basic " + encode_basic_auth(auth)
    try:
        conn.request("POST", "/", payload, headers)
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def decode_rpc_error(err: RuntimeError) -> Tuple[Optional[int], str]:
    if not err.args:
        return None, ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("code"), payload.get("message", "")
    return None, str(payload)


def wait_for_rpc(rpc_url: str, auth: str, timeout_s: int = 120) -> None:
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


def compute_miners(node_count: int, percentage: float) -> List[str]:
    """
    Berechnet die Liste der Mining-Nodes.
    
    Wenn percentage >= 1.0, werden alle Nodes als Miner verwendet.
    Bei percentage < 1.0 wird der Anteil berechnet, mit einer Mindestanzahl von 2.
    """
    if percentage >= 1.0:
        miner_count = node_count
    else:
        miner_count = max(2, int(node_count * percentage))
    return [f"node{i:02d}:18443" for i in range(1, miner_count + 1)]


def check_miners_health(miners: List[str], auth: str, timeout: int = 2) -> Set[str]:
    """
    Prüft alle konfigurierten Miner via RPC-Ping und gibt die erreichbaren zurück.
    
    Args:
        miners: Liste aller Miner-Hosts (z.B. ["node01:18443", "node02:18443"])
        auth: RPC-Authentifizierung (user:pass)
        timeout: Timeout pro Miner in Sekunden
    
    Returns:
        Set der erreichbaren Miner-Hosts
    """
    active: Set[str] = set()
    for miner in miners:
        try:
            rpc_call(f"http://{miner}", "getblockcount", auth=auth, timeout=timeout)
            active.add(miner)
        except Exception:  # noqa: BLE001
            # Miner nicht erreichbar - ignorieren
            pass
    return active


def read_crashed_nodes(filepath: str = "/state/crashed_nodes.txt") -> Set[str]:
    """
    Liest die Liste der gecrachten Nodes aus einer Datei.

    Die Datei wird vom Ansible-Playbook geschrieben BEVOR Nodes gestoppt werden.
    Format: Ein Node-Name pro Zeile (z.B. "node01", "node02")

    Returns:
        Set der gecrachten Node-Namen (z.B. {"node01:18443", "node02:18443"})
    """
    crashed: Set[str] = set()
    try:
        with open(filepath, "r") as f:
            for line in f:
                node = line.strip()
                if node:
                    # Konvertiere "node01" zu "node01:18443"
                    if ":" not in node:
                        node = f"{node}:18443"
                    crashed.add(node)
    except FileNotFoundError:
        pass  # Keine gecrachten Nodes
    except Exception:
        pass  # Bei Fehlern ignorieren
    return crashed


def miner_cycle(miners: List[str], seed: int) -> Iterable[str]:
    order = miners[:]
    rng = random.Random(seed)
    rng.shuffle(order)
    index = 0
    while True:
        yield order[index % len(order)]
        index += 1


def exponential_interval(target_avg: float, rng: random.Random, min_interval: float = 0.0) -> float:
    """
    Generiert eine SHIFTED exponentiell verteilte Wartezeit.
    
    Basiert auf der "0-30s Anomalie" im Bitcoin-Netzwerk:
    - Extrem kurze Block-Intervalle (<1-2s) sind unrealistisch
    - Mining-Pools brauchen Zeit für Template-Switching
    - Netzwerk-Propagation braucht Zeit
    
    Bei min_interval > 0 wird eine verschobene Exponentialverteilung verwendet:
        T_total = min_interval + Exp(λ_eff)
    
    wobei λ_eff so gewählt wird, dass E[T_total] = target_avg:
    - E[T_total] = min_interval + E[Exp(λ_eff)] = min_interval + 1/λ_eff
    - Für E[T_total] = target_avg: 1/λ_eff = target_avg - min_interval
    
    Der Shift verhindert Race-Conditions bei kurzen Intervallen und
    eliminiert Fork-verursachende Sub-Sekunden-Blöcke.
    
    Args:
        target_avg: Ziel-Mittelwert der Blockzeit (z.B. 16s)
        rng: Random Number Generator (für Determinismus)
        min_interval: Minimale Blockzeit / Shift der Verteilung (z.B. 2.0s)
    
    Returns:
        Blockzeit T >= min_interval mit E[T] = target_avg
    """
    # Fallback wenn min_interval >= target_avg (ungültige Konfiguration)
    if min_interval >= target_avg:
        return target_avg
    
    # Effektive Mining-Zeit = Ziel - Minimum
    effective_mining_avg = target_avg - min_interval
    
    # Exponentialverteilung: X = -ln(1-U) / λ = -ln(1-U) * (1/λ)
    # mit λ = 1/effective_mining_avg
    u = rng.random()
    # Verhindere log(0) bei u=1
    if u >= 1.0:
        u = 1.0 - 1e-10
    t_mining = -math.log(1.0 - u) * effective_mining_avg
    
    # Shifted Exponential: T_total = min_interval + t_mining
    return min_interval + t_mining


def load_config(path: str, default_interval: float, miners: List[str], seed: int) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    config.setdefault("interval_s", default_interval)
    config.setdefault("miner_hosts", miners)
    config.setdefault("seed", seed)
    if "mining_address" not in config:
        raise KeyError("Config file missing 'mining_address'")
    return config


def format_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic block scheduler")
    parser.add_argument("--interval", type=float, required=True,
                        help="Sollintervall zwischen Blöcken (Sekunden)")
    parser.add_argument("--node-count", type=int, required=True,
                        help="Gesamtzahl der Knoten im Netzwerk")
    parser.add_argument("--mining-percentage", type=float, required=True,
                        help="Anteil der Knoten, die minen dürfen (0-1)")
    parser.add_argument("--rpc-user", required=True, help="RPC Benutzername")
    parser.add_argument("--rpc-pass", required=True, help="RPC Passwort")
    parser.add_argument("--seed", type=int, default=1337, help="Deterministischer Seed")
    parser.add_argument("--config", default="/state/mining_targets.json",
                        help="Pfad zur Scheduler-Konfigurationsdatei")
    parser.add_argument("--grace-period", type=float, default=600.0,
                        help="Wartezeit (Sekunden) auf Konfigurationsdatei; <=0 bedeutet unendlich warten")
    parser.add_argument("--use-variance", action="store_true", default=False,
                        help="Exponentialverteilung für Block-Intervalle (wie im echten Netzwerk)")
    parser.add_argument("--min-interval", type=float, default=None,
                        help="Minimales Block-Intervall in Sekunden (Shifted Exponential). "
                             "Verhindert Race-Conditions durch zu kurze Intervalle. "
                             "Empfohlen: 2.0s für 16s Blockzeit. Default: 2.0s wenn variance aktiv, sonst 0.")
    parser.add_argument("--adaptive-interval", action="store_true", default=False,
                        help="Dynamische Blockzeit-Anpassung basierend auf verfügbaren Minern")
    parser.add_argument("--health-check-interval", type=float, default=30.0,
                        help="Intervall für Health-Checks der Miner (Sekunden)")
    args = parser.parse_args()

    if args.interval <= 0:
        raise ValueError("Interval muss > 0 sein")

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    # Berechne Miner-Liste als Fallback (wird überschrieben, wenn Config existiert)
    fallback_miners = compute_miners(args.node_count, args.mining_percentage)

    if args.grace_period <= 0:
        while not os.path.exists(args.config):
            print(f"{format_ts()} ⏳ warte auf Konfigurationsdatei {args.config} …")
            time.sleep(2.0)
    else:
        deadline = time.time() + args.grace_period
        while time.time() < deadline and not os.path.exists(args.config):
            print(f"{format_ts()} ⏳ warte auf Konfigurationsdatei {args.config} …")
            time.sleep(2.0)

    # Lade Config - miner_hosts aus Config hat Vorrang (wird von funding_setup.py mit korrekter node_count erstellt)
    config = load_config(args.config, args.interval, fallback_miners, args.seed)
    mining_address = config["mining_address"]
    miner_hosts_from_config: List[str] = config["miner_hosts"]
    
    # WICHTIG: Begrenze Miner-Liste auf tatsächlich existierende Nodes
    # Dies verhindert Probleme bei node_count_override, wo die Config-Datei möglicherweise
    # von einem vorherigen Run mit mehr Nodes stammt
    # Berechne maximale Node-Nummer basierend auf args.node_count
    max_node_num = args.node_count
    miner_hosts = [m for m in miner_hosts_from_config if int(m.split(":")[0].replace("node", "")) <= max_node_num]
    
    # Falls gefilterte Liste leer ist (z.B. Config-Datei hatte falsche Formatierung), verwende Fallback
    if not miner_hosts:
        print(f"{format_ts()} ⚠️  Gefilterte Miner-Liste ist leer, verwende Fallback-Liste basierend auf args.node_count")
        miner_hosts = fallback_miners
    
    # Warnung wenn Miner-Liste gekürzt wurde
    if len(miner_hosts) < len(miner_hosts_from_config):
        print(f"{format_ts()} ⚠️  Miner-Liste gekürzt: Config hatte {len(miner_hosts_from_config)} Miner, "
              f"aber nur {args.node_count} Nodes laufen. Verwende {len(miner_hosts)} Miner.")
    
    # Verwende tatsächliche Anzahl der Miner für node_count-abhängige Berechnungen
    effective_node_count = len(miner_hosts)
    # CLI --interval (from experiment config) takes precedence over config file
    interval_s = float(args.interval)
    seed = int(config.get("seed", args.seed))
    # use_variance kann via CLI oder Config gesetzt werden (CLI hat Vorrang wenn True)
    use_variance = args.use_variance or config.get("use_variance", False)
    
    # min_interval Priorität:
    # 1) CLI --min-interval (auch wenn explizit 0.0)
    # 2) Config "min_interval"
    # 3) Default: 2.0s wenn variance aktiv, sonst 0.0
    config_min_interval = config.get("min_interval", None)
    if args.min_interval is not None:
        min_interval = float(args.min_interval)
    elif config_min_interval is not None:
        min_interval = float(config_min_interval)
    elif use_variance:
        # Sinnvoller Default für Shifted Exponential bei aktivierter Varianz
        min_interval = 2.0
    else:
        min_interval = 0.0
    
    # adaptive_interval kann via CLI oder Config gesetzt werden
    adaptive_interval = args.adaptive_interval or config.get("adaptive_interval", False)
    health_check_interval = args.health_check_interval
    
    # Separater RNG für Varianz (deterministisch, aber unabhängig von Miner-Rotation)
    variance_rng = random.Random(seed + 999)
    
    # Status für dynamische Blockzeit
    total_miners = len(miner_hosts)
    active_miners: Set[str] = set(miner_hosts)  # Initial alle als aktiv annehmen
    last_health_check = 0.0  # Erzwingt initialen Health-Check
    last_active_count = total_miners
    
    if use_variance:
        if min_interval > 0:
            variance_mode = f"(Shifted Exponential, min={min_interval}s)"
        else:
            variance_mode = "(Exponentialverteilung)"
    else:
        variance_mode = "(festes Intervall)"
    adaptive_mode = ", adaptives Intervall aktiv" if adaptive_interval else ""
    print(f"{format_ts()} ✅ Config geladen: {total_miners} Miner, "
          f"Ziel-Intervall {interval_s}s {variance_mode}{adaptive_mode}, Mining-Adresse {mining_address}")
    
    # Warnung wenn node_count nicht mit tatsächlicher Miner-Anzahl übereinstimmt
    # (kann bei node_count_override oder wenn Config-Datei von vorherigem Run stammt)
    if effective_node_count != args.node_count:
        print(f"{format_ts()} ⚠️  Node count mismatch: CLI arg={args.node_count}, actual miners={effective_node_count} "
              f"(using actual count for RPC timeout)")

    rpc_timeout = rpc_timeout_for(effective_node_count)

    for miner in miner_hosts:
        try:
            wait_for_rpc(f"http://{miner}", auth, timeout_s=120)
            print(f"{format_ts()} 🔄 Miner {miner} erreichbar")
        except RuntimeError as err:
            print(f"{format_ts()} ⚠️ Miner {miner} nicht erreichbar: {err}")

    rotation = miner_cycle(miner_hosts, seed)
    produced_blocks = 0
    fail_counts = {miner: 0 for miner in miner_hosts}
    
    # Initiales Intervall berechnen (wird unten verfeinert)
    current_interval = interval_s
    next_tick = time.perf_counter()
    
    def perform_health_check() -> None:
        """Führt Health-Check durch und aktualisiert active_miners."""
        nonlocal active_miners, last_health_check, last_active_count
        
        new_active = check_miners_health(miner_hosts, auth, timeout=2)
        now = time.time()
        last_health_check = now
        
        # Nur loggen wenn sich die Anzahl geändert hat
        if len(new_active) != last_active_count:
            ratio = len(new_active) / total_miners if total_miners > 0 else 0.0
            ts = format_ts()
            print(f"{ts} 🔍 Health-Check: {len(new_active)}/{total_miners} Miner aktiv "
                  f"(Ratio: {ratio:.2f})")
            print(f"HASHPOWER_EVENT,{ts},{len(new_active)},{total_miners},{ratio:.4f}")
            last_active_count = len(new_active)
        
        active_miners = new_active
    
    def get_effective_interval() -> float:
        """
        Berechnet das effektive Block-Intervall.
        
        Bei aktiviertem adaptive_interval:
        effective_interval = base_interval × (total_miners / active_miners)
        
        Dies simuliert den Effekt von Miner-Crashes auf die Blockzeit wie im echten
        Bitcoin-Netzwerk (vor einem Difficulty Adjustment). Weniger Hash-Power 
        führt zu längeren Block-Intervallen.
        """
        base = interval_s
        
        if adaptive_interval and len(active_miners) > 0:
            # Skaliere Intervall basierend auf verfügbarer Hash-Power
            hash_power_ratio = len(active_miners) / total_miners
            # effective_interval = base / ratio (Simulation von Hashrate-Verlust wie im echten Netzwerk)
            base = interval_s / hash_power_ratio
        
        if use_variance:
            return exponential_interval(base, variance_rng, min_interval)
        return base

    # Initialer Health-Check
    if adaptive_interval:
        perform_health_check()
        ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
        print(f"{format_ts()} 🔍 Initialer Health-Check: {len(active_miners)}/{total_miners} Miner "
              f"(Ratio: {ratio:.2f})")

    # Erstes Intervall festlegen
    current_interval = get_effective_interval()
    next_tick = time.perf_counter() + current_interval

    while True:
        now_perf = time.perf_counter()
        now_time = time.time()
        
        # Periodischer Health-Check
        if adaptive_interval and (now_time - last_health_check) >= health_check_interval:
            perform_health_check()

        # Prüfe auf extern signalisierte Crashes (vom Ansible-Playbook)
        if adaptive_interval:
            crashed_nodes = read_crashed_nodes()
            if crashed_nodes:
                newly_crashed = crashed_nodes & active_miners
                if newly_crashed:
                    active_miners -= newly_crashed
                    ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
                    ts = format_ts()
                    print(f"{ts} 📢 Crash-Signal empfangen: {len(newly_crashed)} Nodes entfernt - "
                          f"{len(active_miners)}/{total_miners} Miner aktiv (Ratio: {ratio:.2f})")
                    print(f"HASHPOWER_EVENT,{ts},{len(active_miners)},{total_miners},{ratio:.4f}")
                    last_active_count = len(active_miners)

        sleep_for = next_tick - now_perf
        if sleep_for > 0:
            time.sleep(min(sleep_for, 0.1))
            continue
        
        # HIER: Wir berechnen kein neues Intervall am Anfang, sondern verwenden das bestehende.
        # Ein neues Intervall wird erst NACH einem erfolgreichen Block berechnet.
        
        # Versuche einen aktiven Miner zu finden
        attempts = 0
        max_attempts = len(miner_hosts)  # Verhindere Endlosschleife
        miner = None
        
        while attempts < max_attempts:
            candidate = next(rotation)
            # Wenn adaptive_interval aktiv ist, prüfe ob Miner aktiv ist
            if adaptive_interval:
                if candidate in active_miners:
                    miner = candidate
                    break
            else:
                # Ohne adaptive_interval: verwende jeden Miner
                miner = candidate
                break
            attempts += 1
        
        # Falls kein aktiver Miner gefunden wurde, verwende den nächsten aus der Rotation
        if miner is None:
            miner = next(rotation)
            if adaptive_interval:
                # Logge Warnung wenn kein aktiver Miner verfügbar ist
                print(f"{format_ts()} ⚠️ Kein aktiver Miner verfügbar, versuche {miner} trotzdem")
        
        rpc_url = f"http://{miner}"

        try:
            # Verwende kürzeren Timeout für schnelleres Failover bei nicht erreichbaren Nodes
            call_timeout = min(rpc_timeout, 5)  # Maximal 5 Sekunden pro Versuch
            result = rpc_call(
                rpc_url,
                "generatetoaddress",
                [1, mining_address],
                auth=auth,
                timeout=call_timeout,
            )
            block_hash = result[0] if result else "unknown"
            produced_blocks += 1
            fail_counts[miner] = 0
            ts = format_ts()
            
            # Erweiterte Intervall-Info mit Hash-Power-Ratio
            if adaptive_interval:
                ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
                interval_info = f" (effektives Intervall: {current_interval:.2f}s, Ratio: {ratio:.2f})"
            elif use_variance:
                interval_info = f" (nächstes Intervall: {current_interval:.2f}s)"
            else:
                interval_info = ""
            
            print(f"{ts} ⛏️  Block #{produced_blocks} auf {miner} "
                  f"(hash {block_hash[:16]}...){interval_info}")
            print(f"BLOCK_EVENT,{ts},{produced_blocks},{miner},{block_hash}")
            
            # Miner war erfolgreich - als aktiv markieren
            if adaptive_interval and miner not in active_miners:
                active_miners.add(miner)
                # Logge Hash-Power-Änderung wenn Miner wieder online kommt
                if len(active_miners) != last_active_count:
                    ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
                    print(f"{ts} 🔍 Miner {miner} wieder online - {len(active_miners)}/{total_miners} "
                          f"Miner aktiv (Ratio: {ratio:.2f})")
                    print(f"HASHPOWER_EVENT,{ts},{len(active_miners)},{total_miners},{ratio:.4f}")
                    last_active_count = len(active_miners)

            # JETZT erst das nächste Intervall berechnen
            current_interval = get_effective_interval()
            next_tick = time.perf_counter() + current_interval
                
        except RuntimeError as err:
            code, message = decode_rpc_error(err)
            fail_counts[miner] = fail_counts.get(miner, 0) + 1
            print(f"{format_ts()} ❌ Miner {miner} Fehler (code={code}): {message}")
            
            # Bei Fehler: Miner als inaktiv markieren und sofortigen Health-Check auslösen
            if adaptive_interval:
                if miner in active_miners:
                    active_miners.discard(miner)
                    ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
                    ts = format_ts()
                    print(f"{ts} 🔍 Miner {miner} inaktiv - {len(active_miners)}/{total_miners} "
                          f"Miner aktiv (Ratio: {ratio:.2f})")
                    print(f"HASHPOWER_EVENT,{ts},{len(active_miners)},{total_miners},{ratio:.4f}")
                    last_active_count = len(active_miners)
            
            if fail_counts[miner] >= 3:
                print(f"{format_ts()} 🔁 Warte zusätzliche 3s wegen wiederholter Fehler")
                time.sleep(3.0)
                next_tick = time.perf_counter() + 3.0
            else:
                next_tick = time.perf_counter() + 1.0
        except Exception as err:  # noqa: BLE001
            # Prüfe ob es ein Verbindungsfehler ist (erwartet bei Crash-Experimenten)
            err_str = str(err).lower()
            is_connection_error = any(keyword in err_str for keyword in [
                "name or service not known",
                "connection refused",
                "connection reset",
                "timeout",
                "timed out",
                "errno -2",
                "errno 111",
                "errno 110"
            ])

            # Nur unerwartete Fehler loggen (Verbindungsfehler sind bei Crash-Experimenten normal)
            if not is_connection_error:
                print(f"{format_ts()} ❌ Unerwarteter Fehler bei Miner {miner}: {err}")

            # Bei Fehler: Miner als inaktiv markieren
            if adaptive_interval:
                if miner in active_miners:
                    active_miners.discard(miner)
                    ratio = len(active_miners) / total_miners if total_miners > 0 else 0.0
                    ts = format_ts()
                    print(f"{ts} 🔍 Miner {miner} inaktiv (Verbindungsfehler) - "
                          f"{len(active_miners)}/{total_miners} Miner aktiv (Ratio: {ratio:.2f})")
                    print(f"HASHPOWER_EVENT,{ts},{len(active_miners)},{total_miners},{ratio:.4f}")
                    last_active_count = len(active_miners)

            if is_connection_error:
                # Bei Verbindungsfehlern: sofort Health-Check um alle ausgefallenen Nodes zu finden
                # Dies verhindert wiederholte Fehlversuche bei Burst-Crashes
                if adaptive_interval:
                    perform_health_check()
                # Kurz warten, dann sofort nächsten Miner versuchen
                next_tick = time.perf_counter() + 0.1
            else:
                # Bei anderen Fehlern: normale Behandlung
                next_tick = time.perf_counter() + 2.0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"{format_ts()} ⏹️ Scheduler beendet (KeyboardInterrupt)")
        sys.exit(0)
