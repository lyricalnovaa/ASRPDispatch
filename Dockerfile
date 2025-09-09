FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

RUN git clone https://github.com/Rapptz/discord-ext-voice-recv.git /tmp/voice-recv \
    && pip install --no-cache-dir /tmp/voice-recv

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Start bot
CMD ["python", "main.py"]