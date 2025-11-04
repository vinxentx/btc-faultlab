#!/bin/bash
# Setup-Skript für AWS Ubuntu-Instanz
# Führe dieses Skript auf der AWS-Instanz aus

set -e  # Exit on error

echo "🚀 Bitcoin Fault Lab - AWS Setup"
echo "================================"
echo ""

# Prüfe ob wir auf Ubuntu sind
if [ ! -f /etc/os-release ]; then
    echo "❌ Fehler: /etc/os-release nicht gefunden. Bist du auf Ubuntu?"
    exit 1
fi

source /etc/os-release
if [ "$ID" != "ubuntu" ]; then
    echo "⚠️  Warnung: Dieses Skript ist für Ubuntu optimiert. Gefunden: $ID"
    read -p "Fortfahren? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "✅ Ubuntu erkannt: $VERSION"
echo ""

# System aktualisieren
echo "📦 Aktualisiere System-Pakete..."
sudo apt update
sudo apt upgrade -y

# Basis-Pakete installieren
echo "📦 Installiere Basis-Pakete..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    wget \
    vim \
    tmux \
    htop

# Python Dependencies installieren
echo "🐍 Installiere Python Dependencies..."
if [ -f "requirements.txt" ]; then
    pip3 install --user -r requirements.txt
    echo "✅ Python Dependencies installiert"
else
    echo "⚠️  requirements.txt nicht gefunden. Überspringe Python-Dependencies."
fi

# Ansible installieren
echo "🔧 Installiere Ansible..."
if ! command -v ansible &> /dev/null; then
    sudo apt install -y ansible
    echo "✅ Ansible installiert"
else
    echo "✅ Ansible bereits installiert: $(ansible --version | head -1)"
fi

# Prüfe ob Inventory-Datei existiert
if [ -f "inventories/aws.ini" ]; then
    echo ""
    echo "📝 Prüfe AWS Inventory-Konfiguration..."
    if grep -q "CHANGE_ME" inventories/aws.ini; then
        echo "⚠️  WICHTIG: inventories/aws.ini muss noch konfiguriert werden!"
        echo "   Bitte editiere: nano inventories/aws.ini"
        echo "   Ersetze CHANGE_ME mit deiner AWS-IP-Adresse"
    else
        echo "✅ Inventory-Datei scheint konfiguriert zu sein"
    fi
else
    echo "⚠️  inventories/aws.ini nicht gefunden. Erstelle aus Vorlage..."
    mkdir -p inventories
    cat > inventories/aws.ini << 'EOF'
[controller]
aws_instance ansible_host=CHANGE_ME ansible_user=ubuntu ansible_connection=ssh

[controller:vars]
ansible_python_interpreter=/usr/bin/python3
ansible_ssh_private_key_file=~/.ssh/id_rsa
EOF
    echo "⚠️  Bitte editiere jetzt: nano inventories/aws.ini"
fi

echo ""
echo "✅ Basis-Setup abgeschlossen!"
echo ""
echo "📋 Nächste Schritte:"
echo "1. Editiere inventories/aws.ini und setze deine AWS-IP"
echo "2. Führe Bootstrap aus:"
echo "   ansible-playbook -i inventories/aws.ini playbooks/01_bootstrap.yml"
echo "3. Nach Bootstrap: exit und neu einloggen"
echo "4. Docker testen: docker ps"
echo "5. Quick Test: python3 run_experiments.py --config quick_test_config.json"
echo ""
echo "🎯 Viel Erfolg!"

