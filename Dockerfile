FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HR_HOST=0.0.0.0 HR_PORT=8000 HR_DATA_DIR=/data HR_TRUST_PROXY=1
VOLUME /data
EXPOSE 8000
CMD ["python", "server.py"]
