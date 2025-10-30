#!/bin/bash
# Quick Test Runner - Validates system in ~5 minutes
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Quick Test - Validate System Before Overnight Run"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "This will run a 5-minute test experiment with:"
echo "  • 8 nodes (instead of 32)"
echo "  • 2-minute observation (instead of 30 minutes)"
echo "  • Quick validation of full workflow"
echo ""

read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo -e "${YELLOW}Starting quick test...${NC}"
echo ""

# Run quick test
python3 run_experiments.py --config test_quick.json

# Check if it succeeded
LATEST_RUN=$(ls -1t results/ | grep "^202" | head -n1)

if [ -z "$LATEST_RUN" ]; then
    echo ""
    echo "❌ Test failed - no results generated"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✅ Quick test PASSED!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Test run: $LATEST_RUN"
echo ""

# Check if metrics were generated
if [ -f "results/$LATEST_RUN/metrics.json" ]; then
    echo "Metrics generated:"
    cat "results/$LATEST_RUN/metrics.json" | python3 -m json.tool | head -20
    echo ""
fi

echo -e "${GREEN}System is ready for overnight run!${NC}"
echo ""
echo "To start the overnight run (core suite, ~7 hours):"
echo -e "${YELLOW}python3 run_thesis_experiments.py --core --runs 3 2>&1 | tee experiment_run.log${NC}"
echo ""
echo "Or use the interactive menu:"
echo -e "${YELLOW}./quick_start_thesis.sh${NC}"
echo ""


