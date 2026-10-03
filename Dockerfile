FROM python:3.11-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY hatch_build.py ./
COPY LICENSE ./
COPY src ./src
RUN uv pip install --system --no-cache-dir .

FROM python:3.11-slim
WORKDIR /work
ENV PYTHONUNBUFFERED=1
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/apitest /usr/local/bin/apitest
ENTRYPOINT ["apitest"]
CMD ["--help"]
