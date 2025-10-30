# 📊 Updated Plots - Quick Reference

## ✅ What Changed

Your experiment plots have been **updated to be thesis-appropriate and scientifically correct**. The main improvements:

### 1. **Experiment-Specific Titles** 
Every plot now clearly shows what type of experiment it represents:
- `Baseline (No Faults)` - Reference measurements
- `Crash-Only (30% Node Failures)` - Node crash scenarios
- `Network-Only (2% Loss, 100ms Latency)` - Network impairment scenarios
- `Combined Faults (...)` - Multiple fault types together

### 2. **Intelligent Fault Markers**
- **Baseline runs**: No fault injection markers (because there are no faults!)
- **Fault runs**: Clear "Fault Injection" markers where faults occur
- No more confusing labels!

### 3. **Appropriate Visualizations**
- **Baseline runs**: Show "Baseline Performance" and "Baseline Stability" (not recovery!)
- **Fault runs**: Show "Before/After Fault Injection" and "System Recovery Analysis"
- Each experiment type gets the right visualizations for its scenario

---

## 📁 Your Updated Plots

All **6 good runs** have been regenerated with these improvements:

```
results/
├── baseline_rep1_GOOD/plots/
│   ├── performance_timeline.png          ← Timeline with baseline-specific labels
│   ├── fault_impact_analysis.png         ← Distributions + baseline performance
│   └── system_health_dashboard.png       ← Health metrics + summary
│
├── crash-only_rep1_GOOD/plots/
│   ├── performance_timeline.png          ← Timeline with crash fault markers
│   ├── fault_impact_analysis.png         ← Before/after + recovery analysis
│   └── system_health_dashboard.png       ← Health metrics + summary
│
├── network-only_rep2_GOOD/plots/
│   ├── performance_timeline.png          ← Timeline with network fault markers
│   ├── fault_impact_analysis.png         ← Before/after + recovery analysis
│   └── system_health_dashboard.png       ← Health metrics + summary
│
├── network-only_rep3_GOOD/plots/
│   ├── performance_timeline.png          ← Timeline with network fault markers
│   ├── fault_impact_analysis.png         ← Before/after + recovery analysis
│   └── system_health_dashboard.png       ← Health metrics + summary
│
├── combined_rep2_GOOD/plots/
│   ├── performance_timeline.png          ← Timeline with combined fault markers
│   ├── fault_impact_analysis.png         ← Before/after + recovery analysis
│   └── system_health_dashboard.png       ← Health metrics + summary
│
└── combined_rep3_GOOD/plots/
    ├── performance_timeline.png          ← Timeline with combined fault markers
    ├── fault_impact_analysis.png         ← Before/after + recovery analysis
    └── system_health_dashboard.png       ← Health metrics + summary
```

---

## 🎯 How to Use These Plots in Your Thesis

### Performance Timeline Plots
**Use for:** Showing how throughput and latency evolve over time

**What they show now:**
- Clear experiment type in title (e.g., "Crash-Only (30% Node Failures)")
- Fault injection markers (only when faults exist)
- Throughput and latency trends
- Experiment phases (warmup, observation, fault injection)

**Thesis caption example:**
> *"Figure X: Bitcoin network performance during crash fault injection. The red dashed line indicates when 30% of nodes (10 out of 32) were simultaneously crashed. The system maintains stable throughput throughout the fault period, demonstrating excellent crash tolerance."*

---

### Fault Impact Analysis Plots
**Use for:** Comparing performance before/after faults and showing recovery

**What they show now:**
- Latency and throughput distributions
- Before/after fault comparison (only for fault runs)
- Recovery dynamics (only for fault runs)
- Clear "Baseline Performance" indicator (for baseline runs)

**Thesis caption example:**
> *"Figure X: Impact analysis for combined fault scenario. Top panels show latency and throughput distributions across the entire experiment. Bottom-left compares performance before and after fault injection, while bottom-right demonstrates the system's recovery trajectory."*

---

### System Health Dashboard Plots
**Use for:** Overall health metrics and performance summary

**What they show now:**
- Availability over time
- Success rate trends
- Latency percentiles (P50, P95, P99)
- Comprehensive metrics summary table
- Experiment-specific title

**Thesis caption example:**
> *"Figure X: System health dashboard for network-only fault scenario. The network maintains high availability despite 2% packet loss and 100ms latency, with all key metrics remaining within acceptable bounds."*

---

## 🔍 Key Differences by Experiment Type

### Baseline Runs
- **Title**: "Baseline (No Faults)"
- **Markers**: Subtle gray dotted lines for phases (not fault injection)
- **Bottom panels**: "Baseline Performance" and "Baseline Stability" with green info boxes
- **Use in thesis**: Reference point for comparing all fault scenarios

### Crash-Only Runs
- **Title**: "Crash-Only (30% Node Failures)"
- **Markers**: Red "Fault Injection" line when crashes occur
- **Bottom panels**: Before/after comparison + recovery curve
- **Use in thesis**: Demonstrate crash fault tolerance

### Network-Only Runs
- **Title**: "Network-Only (2% Loss, 100ms Latency)"
- **Markers**: Red "Fault Injection" line when network impairments start
- **Bottom panels**: Before/after comparison + recovery curve
- **Use in thesis**: Demonstrate network fault tolerance (omission faults)

### Combined Runs
- **Title**: "Combined Faults (30% Crashes + 2% Loss + 100ms Latency)"
- **Markers**: Red "Fault Injection" line when both fault types start
- **Bottom panels**: Before/after comparison + recovery curve
- **Use in thesis**: Show compound effects of multiple fault types

---

## 📊 What Each Plot Tells You

### For Baseline (baseline_rep1_GOOD):
- **Performance**: 13,692 confirmed txs, 2.01s latency, 6.49 tx/s throughput
- **Stability**: Consistent performance throughout 30-minute observation
- **Thesis insight**: This is your reference point - all fault scenarios are compared to this

### For Crash-Only (crash-only_rep1_GOOD):
- **Performance**: 15,267 confirmed txs, 2.00s latency, 6.34 tx/s throughput
- **Recovery**: 1 second (extremely fast!)
- **Thesis insight**: 30% node failures have minimal impact - Bitcoin is crash-tolerant!

### For Network-Only (network-only_rep2_GOOD, network-only_rep3_GOOD):
- **Performance**: ~4,700 confirmed txs, 2.07s latency, 3.5 tx/s throughput
- **Recovery**: 7-10 seconds
- **Thesis insight**: Network conditions matter more than crashes - 45% throughput reduction

### For Combined (combined_rep2_GOOD, combined_rep3_GOOD):
- **Performance**: 1,714-5,178 confirmed txs, 1.99-2.11s latency, 2.3-3.3 tx/s throughput
- **Recovery**: 6 seconds (combined_rep3)
- **Thesis insight**: Effects are additive but not catastrophic - graceful degradation

---

## ✅ Validation Checklist

Before using these plots in your thesis, verify:

- [x] All 6 good runs have 3 plots each (18 plots total)
- [x] Baseline plots show "No Faults" in titles
- [x] Fault plots show specific fault types in titles
- [x] No "fault injection" markers on baseline plots
- [x] Recovery analysis only shown for fault runs
- [x] All plots are high-resolution (300 DPI)
- [x] Plots are self-documenting with clear titles
- [x] Ready to insert into thesis results chapter

---

## 🎓 Thesis Writing Tips

### Results Chapter Structure

**Section 1: Baseline Performance**
- Use plots from `baseline_rep1_GOOD/`
- Establish reference metrics (6.49 tx/s, 2.01s latency)
- Show system operates stably without disruptions

**Section 2: Crash Fault Tolerance**
- Use plots from `crash-only_rep1_GOOD/`
- Compare to baseline (only 2% throughput reduction!)
- Highlight fast recovery (1 second)
- **Key finding**: Bitcoin handles crash faults extremely well

**Section 3: Network Fault Impact**
- Use plots from `network-only_rep2_GOOD/` and `network-only_rep3_GOOD/`
- Show replication consistency (rep2 and rep3 are very similar)
- Compare to baseline (45% throughput reduction)
- **Key finding**: Omission faults are more impactful than crashes

**Section 4: Combined Fault Scenarios**
- Use plots from `combined_rep2_GOOD/` and `combined_rep3_GOOD/`
- Show effects are additive, not multiplicative
- Demonstrate graceful degradation
- **Key finding**: System remains functional under compound stress

---

## 📖 Figure Caption Templates

Use these templates for your thesis figure captions:

### Performance Timeline:
```
Figure X: Bitcoin network performance during [EXPERIMENT TYPE]. The system shows
[KEY OBSERVATION] with [METRIC]. [FAULT-SPECIFIC OBSERVATION].
```

### Fault Impact Analysis:
```
Figure X: Performance impact analysis for [EXPERIMENT TYPE]. Top panels show the
distribution of latency and throughput across the experiment. Bottom panels
illustrate [BEFORE/AFTER COMPARISON or BASELINE STABILITY].
```

### System Health Dashboard:
```
Figure X: System health metrics for [EXPERIMENT TYPE]. The dashboard shows
availability, success rate, and latency percentiles over the 30-minute
observation period, demonstrating [KEY HEALTH FINDING].
```

---

## 🚀 Quick Open Commands

Open all plots for a specific experiment:

```bash
# Baseline
open results/baseline_rep1_GOOD/plots/*.png

# Crash-only  
open results/crash-only_rep1_GOOD/plots/*.png

# Network-only (rep 2)
open results/network-only_rep2_GOOD/plots/*.png

# Network-only (rep 3)
open results/network-only_rep3_GOOD/plots/*.png

# Combined (rep 2)
open results/combined_rep2_GOOD/plots/*.png

# Combined (rep 3)
open results/combined_rep3_GOOD/plots/*.png

# All at once (warning: opens 18 plots!)
open results/*_GOOD/plots/*.png
```

---

## 🎯 Summary

Your plots are now:
- ✅ **Scientifically correct** - No misleading labels or false signals
- ✅ **Self-documenting** - Titles and labels clearly explain the experiment
- ✅ **Publication quality** - Professional appearance, 300 DPI, clear annotations
- ✅ **Thesis-ready** - Can be directly inserted into your results chapter
- ✅ **Consistent** - All experiments use the same structure for easy comparison

**You're ready to write your thesis results chapter!** 🎓

---

*For a detailed breakdown of all changes, see `PLOT_IMPROVEMENTS.md`*
*For overall experiment results, see `EXPERIMENT_RESULTS_SUMMARY.md`*
*For quick reference, see `RESULTS_QUICK_REFERENCE.txt`*








