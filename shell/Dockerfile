FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server git yamllint && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir dbt-core sqlfluff

# sshd setup: key-only auth, non-root user
RUN mkdir -p /run/sshd
RUN echo 'PermitRootLogin no' >> /etc/ssh/sshd_config && \
    echo 'PasswordAuthentication no' >> /etc/ssh/sshd_config
RUN useradd -m -s /bin/bash shelluser
RUN mkdir -p /home/shelluser/.ssh && chmod 700 /home/shelluser/.ssh && \
    chown shelluser:shelluser /home/shelluser/.ssh

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
