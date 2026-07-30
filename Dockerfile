# Containerized pipeline: generates both synthetic datasets, rebuilds every
# engine (activity, SPC, economic evaluation, revenue cycle), applies the
# governance layer, and runs the invariant suite. CI builds and runs this image.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir pytest

COPY . .

# The test suite's session fixture rebuilds everything itself, so the explicit
# runs here exist to fail fast and legibly on a pipeline error rather than
# surfacing it as a confusing collection error.
CMD ["sh", "-c", "\
python data_generator/generate_claims_data.py && \
python engine/build_rcm_metrics.py && \
python canadian/generate_activity_data.py && \
python engine/build_activity_metrics.py && \
python engine/health_economics.py && \
python governance/deidentify.py && \
python governance/data_quality.py && \
pytest tests/ -v"]
