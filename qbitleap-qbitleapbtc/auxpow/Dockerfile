# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

WORKDIR /app

# The official AuxPoW coordinator depends on Qbit's functional-test helpers.
COPY --from=qbit-src test/functional /opt/qbit-functional

# Copy the unmodified official qbit-mining-bootstrap AuxPoW components.
COPY qbitleap-qbitleapbtc/upstream/qbit-mining-bootstrap/lab/auxpow/*.py /app/lab/auxpow/
COPY qbitleap-qbitleapbtc/auxpow/start-auxpow.sh /usr/local/bin/start-auxpow

RUN chmod 0755 /usr/local/bin/start-auxpow

ENV PYTHONPATH=/app:/opt/qbit-functional

EXPOSE 3335

ENTRYPOINT ["/usr/local/bin/start-auxpow"]
