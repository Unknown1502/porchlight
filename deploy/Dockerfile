# Porchlight — AgentCore Runtime image.
#
# The runtime contract this satisfies:
#   * linux/arm64
#   * listens on 0.0.0.0:8080
#   * POST /invocations  and  GET /ping
# All three are served by src/porchlight/server.py.
#
# Note what is deliberately NOT done here: the package is not pip-installed.
# porchlight.config resolves REPO_ROOT relative to the source tree in order to
# find packs/ and policies/, so the repository layout is preserved and put on
# PYTHONPATH instead. Installing into site-packages would move REPO_ROOT into
# the Python prefix and the jurisdiction packs would silently disappear.

FROM --platform=linux/arm64 python:3.12-slim

# PORCHLIGHT_OFFLINE=0 is set HERE, not in .env, and that matters.
#
# porchlight.config defaults PORCHLIGHT_OFFLINE to "1", and .env is deliberately
# never copied into the image. So an image with no explicit value ran the
# deterministic stubs — while deploy_runtime.sh's preflight "protected" against
# exactly that by grepping a .env file the container never sees. The check was
# real, the thing it checked was not reachable. Pinning it in the image is what
# actually makes the deployed agent run on Bedrock.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app/src
ENV PORCHLIGHT_OFFLINE=0
ENV PORT=8080
ENV AWS_REGION=us-west-2
# Pinned to Nova Pro, not the project's documented intended model
# (global.anthropic.claude-sonnet-4-6), because Anthropic's models are
# blocked account-wide by an AWS Marketplace billing issue
# (INVALID_PAYMENT_INSTRUMENT — confirmed 2026-09-14, see docs/CLAW-BACK.md).
# Nova Pro is what the recorded demo actually verified live, end to end, on
# this same account. Shipping the Anthropic default here would deploy a
# container that fails on its first real request.
ENV PORCHLIGHT_MODEL_ID=us.amazon.nova-pro-v1:0

WORKDIR /app

# Dependencies first, so a source edit does not re-resolve the wheel set.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Everything the running system reads at runtime.
# src/ carries the dashboard and its two vendored woff2 files. They are served
# from /fonts by the app rather than a CDN: the container has no guaranteed
# outbound network, and a screen about someone's fraud report should not make
# a third-party request to render.
COPY src/    ./src/
COPY packs/  ./packs/
COPY policies/ ./policies/
COPY corpus/ ./corpus/
# eval/ carries labels.json, which /replay reads to decide which reports to hold
# back so a campaign fires live rather than being on screen at page load. Without
# it the hold-back silently degrades to "load everything" and the demo opens on
# the answer — a failure with no error message, which is the worst kind.
COPY eval/  ./eval/
COPY tasks.py pyproject.toml README.md LICENSE ./

# The corpus is generated, not committed (corpus/seed/*.json is gitignored), so
# build it into the image. A container without it answers /replay with a 404.
RUN python corpus/generate.py --out corpus/seed --count 60 --campaigns 1

# Non-root, but able to write the local community store when AgentCore Memory
# is not configured (REPO_ROOT/.porchlight_store.json).
RUN useradd --create-home --uid 10001 porchlight \
    && chown -R porchlight:porchlight /app
USER porchlight

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "porchlight.server:app", "--host", "0.0.0.0", "--port", "8080"]
