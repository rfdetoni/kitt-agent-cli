# Build ONLY from an operator-pinned, pre-cached local image containing
# Python 3, Git and the Wine build dependencies. No package downloads.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY implementer.py /usr/local/bin/kitt-cleanroom-implementer
COPY llama-cli /usr/local/bin/llama-cli
COPY implementer.gguf /opt/models/implementer.gguf
RUN chmod 0555 /usr/local/bin/kitt-cleanroom-implementer /usr/local/bin/llama-cli \
    && chmod 0444 /opt/models/implementer.gguf
LABEL org.kitt.cleanroom="offline-implementer-v1"
ENTRYPOINT ["/usr/bin/python3", "/usr/local/bin/kitt-cleanroom-implementer"]
