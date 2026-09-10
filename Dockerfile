FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# the robotic-assays library is baked in at build time (see README)
COPY robotic-assays-lib /opt/robotic-assays
ENV ROBOTIC_ASSAYS_PATH=/opt/robotic-assays
COPY src ./src
COPY criteria.json .
ENV PYTHONPATH=/app/src
EXPOSE 8080
CMD ["uvicorn", "results_bridge.main:app", "--host", "0.0.0.0", "--port", "8080"]
