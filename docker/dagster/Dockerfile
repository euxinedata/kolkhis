FROM python:3.12-slim

RUN apt-get update -qq && apt-get install -y -qq git > /dev/null 2>&1 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    dagster-postgres \
    dagster-k8s \
    dagster-dbt \
    dbt-core

COPY dagster.yaml workspace.yaml /opt/dagster/
COPY entrypoint.py /opt/dagster/entrypoint.py

ENV DAGSTER_HOME=/opt/dagster

WORKDIR /opt/dagster

CMD ["python", "entrypoint.py"]
