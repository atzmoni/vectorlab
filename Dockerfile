# syntax=docker/dockerfile:1

# Python 3.12 is verified against the full dependency set: every wheel resolves
# (vtracer ships cp38-abi3, so it is forward compatible) and the suite passes.
# opencv-python-headless and pymupdf bundle their own native libraries, so ldd
# reports libstdc++6 as the only system library needed on top of the slim base.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    OUTPUT_DIR=/data/output

RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Copied on its own so the dependency layer survives edits to application code.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py ./

# A vectorize request writes the SVG/DXF to OUTPUT_DIR and the download request
# reads it back, so the two must see the same filesystem. That rules out
# scale-to-zero serverless runtimes, and it is why /data is a volume: mount it
# to keep links working across restarts, or point OUTPUT_DIR at shared storage
# before running more than one replica.
RUN useradd --system --create-home --uid 10001 vectorlab \
 && mkdir -p /data/output \
 && chown -R vectorlab:vectorlab /data
USER vectorlab
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD ["sh", "-c", "python -c \"import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=4)\""]

# run.py binds 127.0.0.1 with reload enabled for local development. A container
# has to bind every interface and honour the $PORT most hosts inject.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
