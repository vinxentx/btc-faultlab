#!/bin/bash
# Docker Cleanup Script - Fixes txindex corruption issues
# Run this if you encounter "txindex: best block of the index not found" errors

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}  Docker Cleanup - Remove Containers and Volumes${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "This script will:"
echo "  1. Stop all Bitcoin node containers"
echo "  2. Remove all node containers"
echo "  3. Remove all node volumes (fixes txindex corruption)"
echo ""
echo -e "${RED}⚠️  WARNING: This will delete all blockchain data!${NC}"
echo ""

read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""
echo -e "${GREEN}Step 1: Stopping containers...${NC}"

# Check if docker compose file exists
if [ -f "docker-compose.yml" ]; then
    docker compose down -v 2>/dev/null || true
    echo "✓ docker compose down completed"
else
    echo "  No docker-compose.yml found, skipping..."
fi

echo ""
echo -e "${GREEN}Step 2: Removing node containers...${NC}"

# Get all node containers
NODE_CONTAINERS=$(docker ps -a --filter "name=node" --format "{{.Names}}" 2>/dev/null || echo "")

if [ -z "$NODE_CONTAINERS" ]; then
    echo "  No node containers found"
else
    echo "  Found node containers:"
    echo "$NODE_CONTAINERS" | while read container; do
        echo "    - $container"
    done
    
    echo "$NODE_CONTAINERS" | while read container; do
        docker rm -f "$container" 2>/dev/null || true
    done
    echo "  ✓ Removed $(echo "$NODE_CONTAINERS" | wc -l | tr -d ' ') containers"
fi

# Also remove txgen
if docker ps -a --format "{{.Names}}" | grep -q "txgen"; then
    docker rm -f txgen 2>/dev/null || true
    echo "  ✓ Removed txgen container"
fi

echo ""
echo -e "${GREEN}Step 3: Removing node volumes...${NC}"

# Get all volumes with node names or btcnet
ALL_VOLUMES=$(docker volume ls --format "{{.Name}}" 2>/dev/null || echo "")
NODE_VOLUMES=$(echo "$ALL_VOLUMES" | grep -E "(node|btcnet|txlogs)" || echo "")

if [ -z "$NODE_VOLUMES" ]; then
    echo "  No node volumes found"
else
    echo "  Found volumes:"
    echo "$NODE_VOLUMES" | while read volume; do
        echo "    - $volume"
    done
    
    VOLUME_COUNT=$(echo "$NODE_VOLUMES" | wc -l | tr -d ' ')
    echo "$NODE_VOLUMES" | while read volume; do
        docker volume rm -f "$volume" 2>/dev/null || true
    done
    echo "  ✓ Removed $VOLUME_COUNT volumes"
fi

echo ""
echo -e "${GREEN}Step 4: Verifying cleanup...${NC}"

REMAINING_CONTAINERS=$(docker ps -a --filter "name=node" --format "{{.Names}}" 2>/dev/null | wc -l | tr -d ' ')
REMAINING_VOLUMES=$(docker volume ls --format "{{.Name}}" | grep -E "(node|btcnet)" 2>/dev/null | wc -l | tr -d ' ')

if [ "$REMAINING_CONTAINERS" -eq 0 ] && [ "$REMAINING_VOLUMES" -eq 0 ]; then
    echo -e "${GREEN}✅ Cleanup completed successfully!${NC}"
    echo ""
    echo "All node containers and volumes have been removed."
    echo "Next experiment will start with fresh blockchain data."
else
    echo -e "${YELLOW}⚠️  Cleanup completed with warnings:${NC}"
    [ "$REMAINING_CONTAINERS" -gt 0 ] && echo "  - $REMAINING_CONTAINERS containers still present"
    [ "$REMAINING_VOLUMES" -gt 0 ] && echo "  - $REMAINING_VOLUMES volumes still present"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Cleanup Complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""


