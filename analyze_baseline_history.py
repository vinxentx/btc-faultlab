#!/usr/bin/env python3
"""
Analysiert alle Baseline-Results chronologisch und identifiziert ab wann Probleme auftraten.
"""
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

def parse_timestamp_from_dirname(dirname: str) -> Optional[datetime]:
    """Extrahiert Timestamp aus Verzeichnisnamen wie 'baseline-20251209T173058Z'"""
    match = re.search(r'baseline-(\d{8}T\d{6}Z)', dirname)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ')
        except ValueError:
            return None
    return None

def count_forks(chaintips_path: Path) -> Dict[str, int]:
    """Zählt Forks in chaintips.json"""
    if not chaintips_path.exists():
        return {"total": 0, "valid_forks": 0, "active": 0, "max_branchlen": 0}
    
    try:
        with open(chaintips_path, 'r') as f:
            chaintips = json.load(f)
        
        total = len(chaintips)
        valid_forks = sum(1 for tip in chaintips if tip.get('status') == 'valid-fork')
        active = sum(1 for tip in chaintips if tip.get('status') == 'active')
        max_branchlen = max((tip.get('branchlen', 0) for tip in chaintips), default=0)
        
        return {
            "total": total,
            "valid_forks": valid_forks,
            "active": active,
            "max_branchlen": max_branchlen
        }
    except Exception as e:
        return {"error": str(e)}

def get_metrics(metrics_path: Path) -> Dict:
    """Liest metrics.json"""
    if not metrics_path.exists():
        return {}
    
    try:
        with open(metrics_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}

def get_block_scheduler_config(log_path: Path) -> Dict:
    """Extrahiert Konfiguration aus block_scheduler.log"""
    if not log_path.exists():
        return {}
    
    config = {}
    try:
        with open(log_path, 'r') as f:
            first_line = f.readline()
            # Suche nach "Config geladen: X Miner"
            match = re.search(r'Config geladen: (\d+) Miner', first_line)
            if match:
                config['miner_count'] = int(match.group(1))
            
            # Suche nach "mit Exponentialverteilung" oder "festes Intervall"
            if 'mit Exponentialverteilung' in first_line:
                config['use_variance'] = True
            elif 'festes Intervall' in first_line:
                config['use_variance'] = False
            
            # Suche nach "adaptives Intervall aktiv"
            if 'adaptives Intervall aktiv' in first_line:
                config['adaptive_interval'] = True
            elif 'adaptives Intervall deaktiviert' in first_line:
                config['adaptive_interval'] = False
    except Exception as e:
        config['error'] = str(e)
    
    return config

def get_metadata(metadata_path: Path) -> Dict:
    """Liest metadata.yml (als YAML, aber wir parsen es einfach)"""
    if not metadata_path.exists():
        return {}
    
    metadata = {}
    try:
        with open(metadata_path, 'r') as f:
            for line in f:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    # Versuche Zahlen zu parsen
                    try:
                        if '.' in value:
                            metadata[key] = float(value)
                        else:
                            metadata[key] = int(value)
                    except ValueError:
                        metadata[key] = value
    except Exception as e:
        metadata['error'] = str(e)
    
    return metadata

def analyze_baseline_results(results_dir: Path) -> List[Dict]:
    """Analysiert alle Baseline-Results"""
    results = []
    
    # Finde alle Baseline-Verzeichnisse
    for item in results_dir.iterdir():
        if not item.is_dir():
            continue
        
        if not item.name.startswith('baseline-'):
            continue
        
        timestamp = parse_timestamp_from_dirname(item.name)
        if not timestamp:
            continue
        
        result_dir = item
        
        # Analysiere die verschiedenen Dateien
        analysis = {
            'dirname': item.name,
            'timestamp': timestamp,
            'chaintips': count_forks(result_dir / 'chaintips.json'),
            'metrics': get_metrics(result_dir / 'metrics.json'),
            'scheduler_config': get_block_scheduler_config(result_dir / 'block_scheduler.log'),
            'metadata': get_metadata(result_dir / 'metadata.yml'),
        }
        
        results.append(analysis)
    
    # Sortiere chronologisch
    results.sort(key=lambda x: x['timestamp'])
    
    return results

def print_analysis(results: List[Dict]):
    """Druckt die Analyse-Ergebnisse"""
    print("=" * 120)
    print("BASELINE RESULTS ANALYSE - Chronologische Übersicht")
    print("=" * 120)
    print()
    
    # Header
    print(f"{'Timestamp':<20} {'Node Count':<12} {'Miner':<8} {'Variance':<10} {'Forks':<8} {'Max Branch':<12} {'Availability':<12} {'Prop Mean':<12}")
    print("-" * 120)
    
    problem_started = None
    prev_was_good = True
    
    for r in results:
        ts = r['timestamp'].strftime('%Y-%m-%d %H:%M')
        node_count = r['metadata'].get('node_count', '?')
        miner_count = r['scheduler_config'].get('miner_count', '?')
        use_variance = 'Yes' if r['scheduler_config'].get('use_variance') else 'No'
        
        forks = r['chaintips'].get('valid_forks', 0)
        max_branch = r['chaintips'].get('max_branchlen', 0)
        
        metrics = r['metrics']
        availability = metrics.get('availability', 0)
        prop_mean = metrics.get('block_propagation', {}).get('summary', {}).get('mean_seconds', 0)
        
        # Identifiziere Probleme
        has_problem = False
        problem_reasons = []
        
        # Problem 1: Zu wenige Miner
        if isinstance(miner_count, int) and isinstance(node_count, int):
            if miner_count < node_count * 0.9:  # Weniger als 90% der Nodes
                has_problem = True
                problem_reasons.append(f"Only {miner_count}/{node_count} miners")
        
        # Problem 2: Viele Forks
        if forks > 5:
            has_problem = True
            problem_reasons.append(f"{forks} forks")
        
        # Problem 3: Hohe Branchlen
        if max_branch > 10:
            has_problem = True
            problem_reasons.append(f"branchlen {max_branch}")
        
        # Problem 4: Niedrige Availability
        if availability < 0.95:
            has_problem = True
            problem_reasons.append(f"availability {availability:.2%}")
        
        # Problem 5: Langsame Propagation
        if prop_mean > 15:
            has_problem = True
            problem_reasons.append(f"prop {prop_mean:.1f}s")
        
        # Problem 6: Variance aktiv (bei Baseline)
        if r['scheduler_config'].get('use_variance'):
            has_problem = True
            problem_reasons.append("variance ON")
        
        # Markiere wenn Problem beginnt
        if has_problem and prev_was_good and problem_started is None:
            problem_started = r['timestamp']
            marker = " ⚠️  PROBLEM START"
        elif has_problem:
            marker = " ⚠️"
        else:
            marker = ""
            prev_was_good = True
        
        if has_problem:
            prev_was_good = False
        
        # Formatierte Ausgabe
        print(f"{ts:<20} {str(node_count):<12} {str(miner_count):<8} {use_variance:<10} "
              f"{forks:<8} {max_branch:<12} {availability*100 if availability else 0:.1f}%{'':<7} "
              f"{prop_mean:.1f}s{'':<8} {marker}")
        
        if problem_reasons:
            print(f"{'':>20} {'REASONS:':<12} {', '.join(problem_reasons)}")
    
    print()
    print("=" * 120)
    if problem_started:
        print(f"⚠️  PROBLEME BEGANNEN: {problem_started.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("✅ Keine Probleme gefunden in den analysierten Runs")
    print("=" * 120)

def main():
    results_dir = Path('/Users/vincenttietze/btc-faultlab/results')
    results = analyze_baseline_results(results_dir)
    print_analysis(results)
    
    # Detaillierte Analyse der problematischen Runs
    print("\n" + "=" * 120)
    print("DETAILLIERTE ANALYSE DER PROBLEMATISCHEN RUNS")
    print("=" * 120)
    print()
    
    for r in results:
        has_problem = (
            r['chaintips'].get('valid_forks', 0) > 5 or
            r['chaintips'].get('max_branchlen', 0) > 10 or
            r['metrics'].get('availability', 1.0) < 0.95 or
            r['scheduler_config'].get('miner_count', 128) < 100 or
            r['scheduler_config'].get('use_variance', False)
        )
        
        if has_problem:
            print(f"\n{'='*80}")
            print(f"RUN: {r['dirname']}")
            print(f"Timestamp: {r['timestamp']}")
            print(f"{'='*80}")
            print(f"Metadata: {json.dumps(r['metadata'], indent=2)}")
            print(f"Scheduler Config: {json.dumps(r['scheduler_config'], indent=2)}")
            print(f"Chaintips: {json.dumps(r['chaintips'], indent=2)}")
            metrics_summary = {
                'availability': r['metrics'].get('availability'),
                'avg_throughput': r['metrics'].get('avg_throughput'),
                'block_propagation_mean': r['metrics'].get('block_propagation', {}).get('summary', {}).get('mean_seconds'),
                'block_propagation_max': r['metrics'].get('block_propagation', {}).get('summary', {}).get('max_seconds'),
            }
            print(f"Metrics Summary: {json.dumps(metrics_summary, indent=2)}")

if __name__ == '__main__':
    main()
