# Bitcoin Node Recovery Tracking Dokumentation

Diese Dokumentation beschreibt die Architektur und Funktionsweise des Recovery-Trackings im `btc-faultlab`. Das System ist darauf ausgelegt, die Wiederherstellung von Bitcoin-Knoten nach Fehlern mit wissenschaftlicher Präzision zu erfassen und Messartefakte (z. B. durch gestaffelte Ausfälle) zu eliminieren.

## 1. Phasen der Fehlerbehandlung

### Phase A: Fault Injection (Crash)
- **Selektion**: Knoten werden deterministisch basierend auf einem `seed` ausgewählt, um Reproduzierbarkeit zu gewährleisten.
- **Modi**:
    - **Burst**: Alle Zielknoten werden gleichzeitig gestoppt.
    - **Staggered**: Knoten werden nacheinander mit einem Intervall (z. B. alle 6s) gestoppt, um rollierende Ausfälle zu simulieren.
- **Logging**: Jede Node schreibt bei Ausfall ein `node_crash` Event mit Zeitstempel und Index in das `events.log`.

### Phase B: Technische Wiederherstellung (Boot)
- **Individuelle Downtime**: Jede Node wartet exakt die in `crash_duration_s` definierte Zeit ab ihrem eigenen Crash-Zeitpunkt.
- **Initialisierung**: Der Container wird gestartet. Das System wartet auf die Bitcoin-Core Meldung `init message: Done loading`.
- **Event `recovery_node_start`**: Markiert den Moment, in dem der Bitcoin-Prozess bereit ist, P2P-Verbindungen aufzubauen und RPC-Befehle entgegenzunehmen.

### Phase C: Netzwerk-Synchronisation (Sync)
Die Überwachung erfolgt durch das Python-Skript `files/wait_for_sync.py`. Es vermeidet Race-Conditions, indem es jede Node individuell trackt:

1. **Start-Verifizierung**: Ein Knoten wird erst dann geprüft, wenn sein spezifisches `recovery_node_start` Event im Log existiert. Dies verhindert Fehlmessungen bei gestaffelten Starts.
2. **Referenz-Messung**: Die aktuelle maximale Blockhöhe des Netzwerks (`NET_H`) wird durch Stichproben bei einer Gruppe gesunder Knoten ("Reference Nodes") ermittelt.
3. **Drei-Säulen-Kriterium für "Synced"**:
    - **Blockhöhe**: `node_height >= NET_H - 1` (Berücksichtigung der P2P-Propagationsverzögerung).
    - **IBD-Status**: `initialblockdownload: false` (Interne Datenbank-Validierung abgeschlossen).
    - **Konnektivität**: `peers > 0` (Aktive Teilnahme am P2P-Netzwerk).

## 2. Event-Hierarchie im events.log

| Event | Bedeutung |
|-------|-----------|
| `node_crash` | Physischer Stopp des Containers / Prozesses. |
| `recovery_node_start` | Bitcoin Core hat den Ladevorgang abgeschlossen (`Done loading`). |
| `node_sync_complete` | Knoten erfüllt alle drei Sync-Kriterien (Höhe, IBD, Peers). |
| `recovery_complete` | Der **letzte** betroffene Knoten hat den Sync-Status erreicht. |

## 3. Wissenschaftliche Analysepotenziale

Durch diese granulare Erfassung lässt sich die gesamte **Recovery Time** mathematisch exakt zerlegen:

1. **Boot-Latenz**: `recovery_node_start` - Container-Startzeit (Zeit für das Laden der Datenbank).
2. **Catch-up Performance**: `node_sync_complete` - `recovery_node_start` (Effizienz des P2P-Abgleichs der verpassten Blöcke).
3. **Netzwerk-Stabilisierungszeit**: Zeit zwischen dem ersten `node_sync_complete` und dem finalen `recovery_complete`.

Diese Trennung ermöglicht es zu unterscheiden, ob Verzögerungen durch lokale Hardware-Ressourcen (Boot) oder durch Netzwerk-Engpässe (Sync) verursacht werden.

