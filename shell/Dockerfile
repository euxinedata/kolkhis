FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server git yamllint sudo nano && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir dbt-core sqlfluff \
    git+https://github.com/euxinedata/dbt-kolkhis.git@v0.2.0

# sshd setup: key-only auth, non-root user
RUN mkdir -p /run/sshd
RUN echo 'PermitRootLogin no' >> /etc/ssh/sshd_config && \
    echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config
RUN useradd -m -s /bin/bash shelluser
RUN mkdir -p /home/shelluser/.ssh && chmod 700 /home/shelluser/.ssh && \
    chown shelluser:shelluser /home/shelluser/.ssh

# Allow git to work in repos cloned by the backend (different UID)
RUN git config --system --add safe.directory '*'

# Allow shelluser to manage users (create accounts, chown home dirs)
RUN echo 'shelluser ALL=(root) NOPASSWD: /usr/sbin/useradd, /bin/mkdir, /bin/cp, /bin/chown, /usr/bin/id' >> /etc/sudoers.d/shelluser

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
