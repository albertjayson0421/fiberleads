# Pinned to the FLEET runtime so dev == production.
# Fleet: CPython 3.11.2 + curl_cffi 0.16.3 (see runtime/DEPLOY_LOCK.txt).
FROM python:3.11.2-slim

# pycurl needs libcurl headers; build-essential for any C extensions.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libcurl4-openssl-dev libssl-dev build-essential ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root dev user (least privilege inside the container too).
RUN useradd -ms /bin/bash dev
USER dev

# Secrets are NEVER baked in. At runtime they come from Secret Manager /
# an owner-provided .env that is gitignored — never committed to the image.
CMD ["bash"]
