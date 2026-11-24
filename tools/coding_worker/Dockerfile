ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
FROM ${PYTHON_IMAGE} AS cpu
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib HOME=/tmp
COPY cpu-requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
WORKDIR /workspace
USER 1000:1000
FROM cpu AS plot
USER root
COPY plot-requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
USER 1000:1000

FROM cpu AS cpu-acceptance
COPY acceptance /opt/labpilot-acceptance
ENV PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
FROM plot AS plot-acceptance
COPY acceptance /opt/labpilot-acceptance
ENV PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
