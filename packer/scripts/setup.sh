#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# System updates
apt-get update -y
apt-get upgrade -y

# Install Python 3.12
apt-get install -y python3.12 python3.12-venv python3.12-dev

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Create service user
useradd --system --no-create-home --shell /bin/false kolkhis-worker

# Set up worker directory
mkdir -p /etc/kolkhis-worker
chown kolkhis-worker:kolkhis-worker /etc/kolkhis-worker

# Install worker dependencies
cd /opt/kolkhis-worker
uv venv --python python3.12
uv pip install --python .venv/bin/python -r <(cat <<'EOF'
fastapi>=0.129.0
uvicorn>=0.41.0
duckdb>=1.2.0
pyarrow>=18.0.0
EOF
)

# Pre-download DuckDB extensions so they're cached in the snapshot
.venv/bin/python -c "
import duckdb
conn = duckdb.connect()
conn.install_extension('iceberg')
conn.install_extension('httpfs')
conn.close()
"

# Set ownership
chown -R kolkhis-worker:kolkhis-worker /opt/kolkhis-worker

# Enable systemd service
systemctl daemon-reload
systemctl enable kolkhis-worker.service

# Cleanup
apt-get clean
rm -rf /var/lib/apt/lists/*
