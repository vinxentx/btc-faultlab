#!/bin/bash
# Quick Start Script for Thesis Experiments
# Bitcoin Fault Tolerance Research

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Bitcoin Fault Tolerance Thesis - Quick Start${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Function to display menu
show_menu() {
    echo -e "${GREEN}Available Commands:${NC}"
    echo ""
    echo "  1) List all available experiments"
    echo "  2) Run core experiment suite (4 experiments, ~7 hours)"
    echo "  3) Run single experiment (choose from list)"
    echo "  4) Run extended suite (all 8 experiments, ~14 hours)"
    echo "  5) Analyze existing results"
    echo "  6) Compare experiment results"
    echo "  7) Check system status"
    echo "  8) View documentation"
    echo "  9) Exit"
    echo ""
}

# Check Docker
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        echo -e "${RED}❌ Docker is not running${NC}"
        echo "Please start Docker Desktop and try again"
        exit 1
    fi
    echo -e "${GREEN}✅ Docker is running${NC}"
}

# Check Python
check_python() {
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ Python 3 not found${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Python 3 found: $(python3 --version)${NC}"
}

# List experiments
list_experiments() {
    echo -e "\n${BLUE}Available Experiments:${NC}\n"
    python3 run_thesis_experiments.py --list
}

# Run core suite
run_core_suite() {
    echo -e "\n${YELLOW}Starting Core Experiment Suite${NC}"
    echo -e "This will run 4 experiments with 3 replications each (~7 hours)"
    echo -e "Experiments: baseline, crash-only, network-only, combined\n"
    
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 run_thesis_experiments.py --core --runs 3
    fi
}

# Run single experiment
run_single_experiment() {
    echo -e "\n${BLUE}Available Experiments:${NC}"
    echo "  1) baseline       - No faults (perfect conditions)"
    echo "  2) crash_only     - 30% node crashes only"
    echo "  3) network_only   - Network impairments only"
    echo "  4) combined       - Crash + network faults"
    echo "  5) high_crash     - 50% node crashes"
    echo "  6) staggered_crash - Gradual failure pattern"
    echo "  7) fast_recovery  - Fast recovery mode"
    echo "  8) severe_network - Severe network conditions"
    echo ""
    
    read -p "Enter experiment name: " exp_name
    read -p "Number of replications (default: 3): " num_runs
    num_runs=${num_runs:-3}
    
    python3 run_thesis_experiments.py --experiment "$exp_name" --runs "$num_runs"
}

# Run extended suite
run_extended_suite() {
    echo -e "\n${YELLOW}Starting Extended Experiment Suite${NC}"
    echo -e "This will run ALL 8 experiments with 3 replications each (~14 hours)"
    echo -e "Make sure you have sufficient time and system resources\n"
    
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python3 run_thesis_experiments.py --extended --runs 3
    fi
}

# Analyze results
analyze_results() {
    echo -e "\n${BLUE}Running Complete Analysis${NC}\n"
    
    if [ -d "results" ]; then
        echo "Analyzing all results..."
        python3 run_thesis_analysis.py
        echo ""
        echo "Comparing experiments..."
        python3 analysis/compare_experiments.py
        echo ""
        echo -e "${GREEN}✅ Analysis complete!${NC}"
        echo "Check results/comparisons/ for plots and tables"
    else
        echo -e "${RED}❌ No results directory found${NC}"
        echo "Run some experiments first"
    fi
}

# Compare results
compare_results() {
    echo -e "\n${BLUE}Comparing Experiment Results${NC}\n"
    
    if [ -d "results" ]; then
        python3 analysis/compare_experiments.py
        echo ""
        echo -e "${GREEN}✅ Comparison complete!${NC}"
        echo "Check results/comparisons/thesis_experiment_comparison.png"
        echo "Check results/thesis_comparison_table.csv for LaTeX table"
    else
        echo -e "${RED}❌ No results directory found${NC}"
        echo "Run some experiments first"
    fi
}

# Check system status
check_status() {
    echo -e "\n${BLUE}System Status Check${NC}\n"
    
    check_docker
    check_python
    
    echo ""
    echo "Docker Containers:"
    if docker ps --format "table {{.Names}}\t{{.Status}}" | grep node > /dev/null 2>&1; then
        docker ps --format "table {{.Names}}\t{{.Status}}" | grep node | head -5
        echo "... (showing first 5 nodes)"
    else
        echo "No Bitcoin nodes running"
    fi
    
    echo ""
    echo "Recent Experiments:"
    if [ -d "results" ]; then
        ls -lt results/ | grep "^d" | head -5 | awk '{print "  " $9 " - " $6 " " $7 " " $8}'
    else
        echo "No experiments run yet"
    fi
    
    echo ""
    echo "Disk Space:"
    df -h . | tail -1 | awk '{print "  Free: " $4 " (" $5 " used)"}'
}

# View documentation
view_docs() {
    echo -e "\n${BLUE}Documentation Files:${NC}\n"
    echo "  1) THESIS_EXPERIMENT_GUIDE.md - Complete usage guide (RECOMMENDED)"
    echo "  2) OPTIMIZATION_SUMMARY.md    - What was changed and why"
    echo "  3) THESIS_README.md           - System overview"
    echo "  4) README.md                  - General documentation"
    echo ""
    
    read -p "Enter number to view (or press Enter to skip): " doc_num
    
    case $doc_num in
        1) less THESIS_EXPERIMENT_GUIDE.md ;;
        2) less OPTIMIZATION_SUMMARY.md ;;
        3) less THESIS_README.md ;;
        4) less README.md ;;
        *) echo "Skipping..." ;;
    esac
}

# Main menu loop
main() {
    # Initial checks
    check_docker
    check_python
    
    echo ""
    
    while true; do
        show_menu
        read -p "Enter your choice (1-9): " choice
        
        case $choice in
            1) list_experiments ;;
            2) run_core_suite ;;
            3) run_single_experiment ;;
            4) run_extended_suite ;;
            5) analyze_results ;;
            6) compare_results ;;
            7) check_status ;;
            8) view_docs ;;
            9) 
                echo -e "\n${GREEN}Good luck with your thesis! 🎓${NC}\n"
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid option. Please try again.${NC}"
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
        echo ""
    done
}

# Run main menu
main



