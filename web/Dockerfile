# Build from the repo root:  docker build -f web/Dockerfile -t asc-web .
# (or `docker compose up -d --build` from web/, which does the same)
FROM python:3.12-slim

RUN useradd --create-home --uid 1000 asc
WORKDIR /app

COPY requirements.txt /tmp/challenge-requirements.txt
COPY web/requirements.txt /tmp/web-requirements.txt
RUN pip install --no-cache-dir -r /tmp/challenge-requirements.txt -r /tmp/web-requirements.txt

# .dockerignore keeps solutions/, tests and SOLUTIONS-SPOILERS.md out of the image.
COPY challenges/ challenges/
COPY web/ web/

RUN mkdir /data && chown asc:asc /data
ENV ASC_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
USER asc
EXPOSE 8000

# One worker only: the run queue and its concurrency limit live in the process.
CMD ["uvicorn", "server:app", "--app-dir", "web", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
