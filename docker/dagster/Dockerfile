FROM python:3.12-slim

RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    dagster-postgres \
    dagster-k8s \
    dagster-dbt \
    dbt-core

COPY dagster.yaml workspace.yaml /opt/dagster/

ENV DAGSTER_HOME=/opt/dagster

WORKDIR /opt/dagster
