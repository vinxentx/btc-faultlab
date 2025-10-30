#!/bin/bash
# Pre-flight Check Script
# Validates system is ready for overnight experiment run

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Pre-Flight Check for Overnight Experiment Run"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

CHECKS_PASSED=0
CHECKS_TOTAL=0

# Function to check and report
check() {
    CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
        CHECKS_PASSED=$((CHECKS_PASSED + 1))
        return 0
    else
        echo -e "${RED}❌ $2${NC}"
        if [ ! -z "$3" ]; then
            echo -e "   ${YELLOW}Fix: $3${NC}"
        fi
        return 1
    fi
}

# 1. Check Docker is running
docker info > /dev/null 2>&1
check $? "Docker is running" "Start Docker Desktop"

# 2. Check Docker has sufficient resources
if docker info > /dev/null 2>&1; then
    DOCKER_MEM=$(docker info 2>/dev/null | grep "Total Memory" | awk '{print $3}')
    if [ ! -z "$DOCKER_MEM" ]; then
        MEM_GB=$(echo $DOCKER_MEM | sed 's/GiB//')
        if (( $(echo "$MEM_GB >= 12" | bc -l) )); then
            check 0 "Docker has sufficient memory (${DOCKER_MEM})"
        else
            check 1 "Docker has sufficient memory (${DOCKER_MEM})" "Increase Docker memory to 16GB in Docker Desktop settings"
        fi
    fi
fi

# 3. Check Python 3 is available
command -v python3 > /dev/null 2>&1
check $? "Python 3 is installed" "Install Python 3"

# 4. Check Ansible is available
command -v ansible-playbook > /dev/null 2>&1
check $? "Ansible is installed" "Run: pip3 install ansible"

# 5. Check disk space (need at least 30GB free)
FREE_SPACE=$(df -g . | tail -1 | awk '{print $4}')
if [ $FREE_SPACE -ge 30 ]; then
    check 0 "Sufficient disk space (${FREE_SPACE}GB free)"
else
    check 1 "Sufficient disk space (${FREE_SPACE}GB free)" "Free up disk space (need 30GB+)"
fi

# 6. Check required files exist
[ -f "run_thesis_experiments.py" ]
check $? "run_thesis_experiments.py exists"

[ -f "thesis_experiments.json" ]
check $? "thesis_experiments.json exists"

[ -f "group_vars/all.yml" ]
check $? "group_vars/all.yml exists"

# 7. Check no conflicting containers
RUNNING_NODES=$(docker ps --filter "name=node" --format "{{.Names}}" | wc -l)
if [ $RUNNING_NODES -eq 0 ]; then
    check 0 "No conflicting Docker containers"
else
    check 1 "No conflicting Docker containers (found $RUNNING_NODES)" "Run: docker stop \$(docker ps -q --filter 'name=node')"
fi

# 8. Check Python dependencies
python3 -c "import pandas" > /dev/null 2>&1
check $? "Python pandas installed" "Run: pip3 install pandas"

python3 -c "import matplotlib" > /dev/null 2>&1
check $? "Python matplotlib installed" "Run: pip3 install matplotlib"

python3 -c "import numpy" > /dev/null 2>&1
check $? "Python numpy installed" "Run: pip3 install numpy"

# 9. Check results directory
if [ -d "results" ]; then
    check 0 "Results directory exists"
else
    mkdir -p results
    check 0 "Results directory created"
fi

# 10. Test a quick Docker operation
docker run --rm alpine:3.19 echo "Docker works" > /dev/null 2>&1
check $? "Docker can run containers" "Check Docker is functioning properly"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  Checks Passed: ${GREEN}${CHECKS_PASSED}${NC}/${CHECKS_TOTAL}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [ $CHECKS_PASSED -eq $CHECKS_TOTAL ]; then
    echo -e "${GREEN}✅ All checks passed! System is ready for overnight run.${NC}"
    echo ""
    echo "Recommended command for core suite (4 experiments, ~7 hours):"
    echo -e "${YELLOW}python3 run_thesis_experiments.py --core --runs 3 2>&1 | tee experiment_run.log${NC}"
    echo ""
    echo "This will:"
    echo "  • Run 4 essential experiments (baseline, crash, network, combined)"
    echo "  • 3 replications each (12 total runs)"
    echo "  • Save all output to experiment_run.log"
    echo "  • Auto-retry failed runs up to 2 times"
    echo "  • Save progress checkpoints after each run"
    echo ""
    exit 0
else
    echo -e "${RED}❌ Some checks failed. Please fix the issues above before running.${NC}"
    echo ""
    exit 1
fi



