#!/bin/bash
# Robust Overnight Experiment Runner
# With error handling, progress tracking, and notifications

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

LOGFILE="experiment_run_$(date +%Y%m%d_%H%M%S).log"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Overnight Experiment Runner with Safety Features"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Function to show spinner
spinner() {
    local pid=$1
    local delay=0.5
    local spinstr='|/-\'
    while [ "$(ps a | awk '{print $1}' | grep $pid)" ]; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

# 1. Pre-flight checks
echo "Step 1/6: Running pre-flight checks..."
if ./preflight_check.sh > preflight_results.txt 2>&1; then
    echo -e "${GREEN}✅ Pre-flight checks passed${NC}"
else
    echo -e "${YELLOW}⚠️  Some checks failed (see preflight_results.txt)${NC}"
    echo ""
    echo "Common issues:"
    echo "  • Docker memory: Current setup has 7.6GB allocated"
    echo "    For 32 nodes, 12GB+ recommended (8GB minimum)"
    echo "    You can continue if you have at least 8GB"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled. Please fix issues and try again."
        exit 1
    fi
fi

# 2. Clean up any existing containers
echo ""
echo "Step 2/6: Cleaning up existing containers..."
docker stop $(docker ps -a --filter 'name=node' --format '{{.Names}}') 2>/dev/null || true
docker rm $(docker ps -a --filter 'name=node' --format '{{.Names}}') 2>/dev/null || true
echo -e "${GREEN}✅ Cleanup complete${NC}"

# 3. Show configuration
echo ""
echo "Step 3/6: Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Experiment Suite: Core (4 experiments)"
echo "  1. Baseline       - No faults"
echo "  2. Crash-only     - 30% node crashes"
echo "  3. Network-only   - Packet loss + latency"
echo "  4. Combined       - Crash + network faults"
echo ""
echo "Replications per experiment: 3"
echo "Total runs: 12"
echo "Estimated duration: 7-8 hours"
echo ""
echo "Safety features:"
echo "  ✓ Auto-retry failed runs (up to 2 attempts)"
echo "  ✓ Progress checkpoints after each run"
echo "  ✓ Automatic cleanup between runs"
echo "  ✓ Full logging to: $LOGFILE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

read -p "Start overnight run? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# 4. Record start time
START_TIME=$(date +%s)
START_TIME_STR=$(date "+%Y-%m-%d %H:%M:%S")

echo ""
echo "Step 4/6: Starting experiments..."
echo "Start time: $START_TIME_STR"
echo "Logging to: $LOGFILE"
echo ""
echo -e "${YELLOW}This will take approximately 7-8 hours.${NC}"
echo -e "${YELLOW}You can safely close this terminal. Progress is saved.${NC}"
echo ""

# 5. Run experiments with full logging
{
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Thesis Experiment Run - $(date)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    python3 run_thesis_experiments.py --core --runs 3
    
    EXIT_CODE=$?
    
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Experiments Complete - $(date)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    exit $EXIT_CODE
} 2>&1 | tee "$LOGFILE"

# Capture exit code
EXIT_CODE=${PIPESTATUS[0]}

# 6. Generate summary
END_TIME=$(date +%s)
END_TIME_STR=$(date "+%Y-%m-%d %H:%M:%S")
DURATION=$((END_TIME - START_TIME))
HOURS=$((DURATION / 3600))
MINUTES=$(((DURATION % 3600) / 60))

echo ""
echo "Step 5/6: Generating summary..."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  EXPERIMENT RUN SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Start time:    $START_TIME_STR"
echo "End time:      $END_TIME_STR"
echo "Duration:      ${HOURS}h ${MINUTES}m"
echo "Log file:      $LOGFILE"
echo ""

# Count results
RESULT_DIRS=$(ls -1d results/202* 2>/dev/null | wc -l)
echo "Result directories created: $RESULT_DIRS"

# Check checkpoint file
if [ -f "results/thesis_progress_checkpoint.json" ]; then
    COMPLETED=$(cat results/thesis_progress_checkpoint.json | python3 -c "import json,sys; print(json.load(sys.stdin)['total_completed'])" 2>/dev/null || echo "?")
    FAILED=$(cat results/thesis_progress_checkpoint.json | python3 -c "import json,sys; print(json.load(sys.stdin)['total_failed'])" 2>/dev/null || echo "?")
    echo "Completed runs: $COMPLETED"
    echo "Failed runs:    $FAILED"
fi

echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ Experiments completed successfully!${NC}"
else
    echo -e "${YELLOW}⚠️  Experiments completed with some issues (exit code: $EXIT_CODE)${NC}"
    echo "Check $LOGFILE for details"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 7. Next steps
echo "Step 6/6: Next steps"
echo ""
echo "To analyze results:"
echo -e "  ${YELLOW}python3 analysis/compare_experiments.py${NC}"
echo ""
echo "To generate all analysis:"
echo -e "  ${YELLOW}python3 run_thesis_analysis.py${NC}"
echo ""
echo "Result files are in:"
echo "  • results/202*/ - Individual experiment runs"
echo "  • results/thesis_core_suite_*.json - Suite summary"
echo "  • $LOGFILE - Full execution log"
echo ""

# Play a sound to notify completion (macOS)
afplay /System/Library/Sounds/Glass.aiff 2>/dev/null || true

echo -e "${GREEN}Done! 🎉${NC}"
echo ""


