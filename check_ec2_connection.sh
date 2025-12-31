#!/bin/bash
# EC2 Connection Diagnostic Script

HOST="ec2-16-16-196-4.eu-north-1.compute.amazonaws.com"
IP="16.16.196.4"
KEY_FILE="$HOME/.ssh/BTC-Faultlab_Key.pem"

echo "🔍 EC2 Connection Diagnostics"
echo "=============================="
echo ""

# 1. Check if key file exists
echo "1️⃣  Checking SSH key..."
if [ -f "$KEY_FILE" ]; then
    echo "   ✅ SSH key found: $KEY_FILE"
    echo "   📋 Key permissions: $(ls -l "$KEY_FILE" | awk '{print $1}')"
    if [ "$(stat -f %A "$KEY_FILE" 2>/dev/null || stat -c %a "$KEY_FILE" 2>/dev/null)" != "600" ] && [ "$(stat -f %A "$KEY_FILE" 2>/dev/null || stat -c %a "$KEY_FILE" 2>/dev/null)" != "400" ]; then
        echo "   ⚠️  WARNING: Key permissions should be 600 or 400"
        echo "   💡 Fix with: chmod 600 $KEY_FILE"
    fi
else
    echo "   ❌ SSH key NOT found: $KEY_FILE"
    exit 1
fi
echo ""

# 2. DNS Resolution
echo "2️⃣  Checking DNS resolution..."
if nslookup "$HOST" > /dev/null 2>&1; then
    RESOLVED_IP=$(nslookup "$HOST" | grep -A 1 "Name:" | tail -1 | awk '{print $2}')
    echo "   ✅ DNS resolves: $HOST -> $RESOLVED_IP"
    if [ "$RESOLVED_IP" != "$IP" ]; then
        echo "   ⚠️  WARNING: IP mismatch! Expected $IP, got $RESOLVED_IP"
        echo "   💡 The instance may have been restarted with a new IP"
    fi
else
    echo "   ❌ DNS resolution FAILED"
    echo "   💡 The hostname may be invalid or the instance was terminated"
fi
echo ""

# 3. Ping test
echo "3️⃣  Testing connectivity (ping)..."
if ping -c 3 -W 2 "$IP" > /dev/null 2>&1; then
    echo "   ✅ Ping successful"
else
    echo "   ❌ Ping FAILED - Instance is not reachable"
    echo "   💡 Possible causes:"
    echo "      - Instance is stopped/terminated"
    echo "      - Security Group blocks ICMP"
    echo "      - Instance has no public IP"
    echo "      - Instance is in private subnet"
fi
echo ""

# 4. Port 22 test
echo "4️⃣  Testing SSH port (22)..."
if timeout 5 bash -c "echo > /dev/tcp/$IP/22" 2>/dev/null; then
    echo "   ✅ Port 22 is OPEN"
else
    echo "   ❌ Port 22 is CLOSED or unreachable"
    echo "   💡 Check Security Group inbound rules for SSH (port 22)"
fi
echo ""

# 5. SSH connection test
echo "5️⃣  Testing SSH connection..."
if ssh -i "$KEY_FILE" -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes ubuntu@"$HOST" exit 2>/dev/null; then
    echo "   ✅ SSH connection successful!"
else
    EXIT_CODE=$?
    echo "   ❌ SSH connection FAILED (exit code: $EXIT_CODE)"
    echo ""
    echo "   🔧 Troubleshooting steps:"
    echo "   1. Check AWS Console → EC2 → Instances:"
    echo "      - Is the instance 'running'?"
    echo "      - What is the current public IP?"
    echo ""
    echo "   2. Check Security Group:"
    echo "      - Inbound rule for SSH (port 22) from your IP?"
    echo "      - Or temporarily allow 0.0.0.0/0 for testing"
    echo ""
    echo "   3. Try with verbose output:"
    echo "      ssh -v -i $KEY_FILE ubuntu@$HOST"
    echo ""
    echo "   4. If IP changed, update the hostname or use IP directly:"
    echo "      ssh -i $KEY_FILE ubuntu@<NEW_IP>"
fi
echo ""

echo "=============================="
echo "✅ Diagnostic complete"
echo ""
echo "💡 Next steps:"
echo "   1. Check AWS Console for instance status"
echo "   2. Verify Security Group rules"
echo "   3. Confirm current public IP address"










