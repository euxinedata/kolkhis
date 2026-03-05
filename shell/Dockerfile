FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server git yamllint nano && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir dbt-core sqlfluff \
    git+https://github.com/euxinedata/dbt-kolkhis.git@v0.6.0

# sshd setup: key-only auth
RUN mkdir -p /run/sshd
RUN echo 'PermitRootLogin no' >> /etc/ssh/sshd_config && \
    echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config

# Allow git to work in repos cloned by the backend (different UID)
RUN git config --system --add safe.directory '*'

# Bake auth file templates into image as fallback for first-run seeding
COPY etc/ /etc/shell-templates/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
