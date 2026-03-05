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
useradd --system --create-home --home-dir /home/kolkhis-worker --shell /bin/false kolkhis-worker

# Set up worker directory
mkdir -p /etc/kolkhis-worker
chown kolkhis-worker:kolkhis-worker /etc/kolkhis-worker

# Install worker dependencies
cd /opt/kolkhis-worker
rm -rf .venv __pycache__
uv venv --python python3.12
uv pip install --python .venv/bin/python \
  'fastapi>=0.115' \
  'uvicorn>=0.34' \
  'duckdb>=1.2' \
  'pyarrow>=19' \
  'python-dotenv>=1.0' \
  'httpx>=0.27'

# Pre-download DuckDB extensions into the worker's home directory
.venv/bin/python -c "
import duckdb
conn = duckdb.connect()
conn.execute(\"SET home_directory='/opt/kolkhis-worker'\")
conn.install_extension('avro')
conn.install_extension('iceberg')
conn.load_extension('iceberg')
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
