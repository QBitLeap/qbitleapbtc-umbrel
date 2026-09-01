# syntax=docker/dockerfile:1.7

FROM python:3.12-alpine

WORKDIR /app

COPY server.py /app/server.py
COPY router.py /app/router.py
COPY permissionless_telemetry.py /app/permissionless_telemetry.py

EXPOSE 8080

CMD ["python", "/app/server.py"]
