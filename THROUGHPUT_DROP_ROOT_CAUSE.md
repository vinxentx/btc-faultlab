# Root Cause Analysis: Throughput-Drop bei hoher tx_rate

## Frage
**Warum tritt der Throughput-Drop bei tx_rate=20 auf, aber nicht bei tx_rate=10?**

## Antwort: **Kausalkette: Hohe tx_rate → Mempool-Sättigung → Größere Blöcke → Langsamere Propagation → Throughput-Drop**

## Detaillierte Analyse

### 1. Daten-Vergleich

| Metrik | tx_rate=10 | tx_rate=20 | Unterschied |
|--------|------------|------------|-------------|
| **Submissions** | 11,439 | 22,277 | +95% |
| **Confirmations** | 11,434 | 21,963 | +92% |
| **Lost TX** | 5 (0.0%) | 314 (1.4%) | Minimal |
| **Blocks** | 37 | 52 | +40% |
| **TX/Block** | 242.4 | 337.5 | +39% |
| **Block Interval** | 16.2s | 11.5s | -29% |
| **Mempool Mean** | 197 TX | 328 TX | +66% |
| **Mempool Max** | 632 TX | 1,963 TX | +210% |
| **Throughput** | 10.48 tx/s (105%) | 16.91 tx/s (84.5%) | -15.5% |

### 2. Theoretische vs. Tatsächliche Kapazität

**tx_rate=20:**
- Theoretisch: 50 Blöcke × 240 TX = 12,000 TX (bei 12s Intervall)
- Tatsächlich: 52 Blöcke × 337.5 TX = 17,552 TX
- **→ Mehr TX werden confirmed als theoretisch möglich!**

**tx_rate=10:**
- Theoretisch: 50 Blöcke × 120 TX = 6,000 TX (bei 12s Intervall)
- Tatsächlich: 37 Blöcke × 242.4 TX = 8,969 TX
- **→ Auch hier mehr TX als theoretisch möglich!**

### 3. Root Cause Identifiziert

Der Throughput-Drop kommt **NICHT** von:
- ❌ Verlorenen TX (nur 1.4% bei tx_rate=20)
- ❌ Zu wenigen Blöcken (52 statt 50 ist sogar mehr!)
- ❌ Block-Größen-Limit (337.5 TX/Block ist unter 2MB Limit)

Der Throughput-Drop kommt von:

#### **PRIMÄRER FAKTOR: Langsame Block-Propagation**

**tx_rate=20:**
- Mean Block Propagation: **2.70s**
- Median: 0.78s
- Max: **70.78s**
- P95: 10.74s

**tx_rate=10:**
- Mean Block Propagation: **1.35s** (aus vorheriger Analyse)
- Median: 0.38s
- Max: 68.04s

**Auswirkung:**
- Bei tx_rate=20: Confirmations werden über **längere Zeit verteilt** durch langsame Propagation
- Der Throughput wird als `confirmed / total_time` berechnet, wobei `total_time` die Zeit zwischen erster und letzter Confirmation ist
- Wenn Block-Propagation langsam ist, wird `total_time` größer → Throughput sinkt

#### **SEKUNDÄRER FAKTOR: Mempool-Sättigung**

**tx_rate=20:**
- Mempool Mean: 328 TX
- Mempool Max: **1,963 TX**
- TX/Block: 337.5 (vs. 240 erwartet)

**Auswirkung:**
- Mempool füllt sich auf → Blöcke enthalten mehr TX
- Aber: **Mehr TX pro Block ist gut für Throughput!**
- Problem: Mempool-Sättigung führt zu **längeren Wartezeiten** für TX, die nicht sofort gemined werden

### 4. Warum ist der Throughput-Drop bei tx_rate=20 größer?

**Kombinationseffekt:**

1. **Hohe Transaction Load (20 tx/s)**
   - Mehr TX werden submitted (22,277 statt 12,000 erwartet)
   - Mempool füllt sich schneller

2. **Langsame Block-Propagation (Network Conditions)**
   - Mean: 2.70s (vs. 0.04s Baseline)
   - Max: 70.78s (vs. 1.18s Baseline)
   - Confirmations kommen verzögert an

3. **Interaktion:**
   - Hohe Load + langsame Propagation → Mempool-Sättigung
   - Mempool-Sättigung → TX warten länger auf Confirmation
   - Langsame Propagation → Confirmations werden über längere Zeit verteilt
   - **→ Throughput wird über längere Zeit berechnet → niedrigerer Wert**

### 5. Berechnung des Throughput-Drops

**tx_rate=20:**
- Block-basierter Throughput: 337.5 TX / 11.5s = **29.25 tx/s**
- Tatsächlicher Throughput (über Zeit-Spanne): **16.91 tx/s**
- **Differenz: 12.34 tx/s (42% Verlust)**

**tx_rate=10:**
- Block-basierter Throughput: 242.4 TX / 16.2s = **14.95 tx/s**
- Tatsächlicher Throughput (über Zeit-Spanne): **10.48 tx/s**
- **Differenz: 4.47 tx/s (30% Verlust)**

**→ Der Throughput-Drop ist größer bei tx_rate=20, weil:**
- Mehr TX submitted werden
- Langsamere Block-Propagation (2.70s vs. 1.35s)
- Größere Mempool-Sättigung (1,963 TX vs. 632 TX)
- Confirmations werden über längere Zeit verteilt

## Fazit: Die Komponente, die am stärksten beiträgt

### **Direkter Vergleich: tx_rate=20 vs tx_rate=10 (beide tier-b-004)**

| Faktor | tx_rate=20 | tx_rate=10 | Unterschied |
|--------|-----------|------------|-------------|
| **Throughput Drop** | 42.2% | 29.9% | +12.3% |
| **TX/Block** | 337.5 | 242.4 | +39% |
| **Block Propagation Mean** | 2.70s | 1.35s | **2.0x langsamer** |
| **Mempool Max** | 1,952 TX | 625 TX | 3.1x höher |
| **Block Interval** | 11.5s | 16.2s | -29% |

### **Kausalkette identifiziert:**

1. **Hohe tx_rate (20 tx/s)**
   - Mehr TX werden submitted
   - → Mempool füllt sich schneller

2. **Mempool-Sättigung**
   - Mempool Max: 1,952 TX (vs. 625 TX bei tx_rate=10)
   - → Blöcke enthalten mehr TX (337.5 vs. 242.4)

3. **Größere Blöcke**
   - 1.4x mehr TX pro Block
   - → Mehr Daten müssen übertragen werden

4. **Langsamere Block-Propagation**
   - Mean: 2.70s (vs. 1.35s bei tx_rate=10)
   - **2.0x langsamer** trotz gleicher Network Conditions
   - → Größere Blöcke brauchen länger bei schlechter Bandwidth (10 Mbit/s)

5. **Throughput-Drop**
   - Confirmations werden über längere Zeit verteilt
   - Throughput = confirmed / time_span
   - → Längere Zeit-Spanne → niedrigerer Throughput

### **Die stärkste Komponente:**

**Block-Propagation-Geschwindigkeit** ist der limitierende Faktor:
- Bei tx_rate=20: 2.70s mean (2.0x langsamer)
- Bei tx_rate=10: 1.35s mean
- **Korrelation: 1.4x mehr TX → 2.0x langsamere Propagation**

**Die Mempool-Sättigung ist die Ursache, die Block-Propagation ist der Effekt:**
- Mempool-Sättigung → Größere Blöcke
- Größere Blöcke → Langsamere Propagation (bei schlechter Bandwidth)
- Langsamere Propagation → Throughput-Drop

**Fazit: Die Mempool-Sättigung führt zu größeren Blöcken, was bei schlechten Network Conditions (10 Mbit/s) zu langsamerer Propagation führt, was den Throughput-Drop verursacht.**

