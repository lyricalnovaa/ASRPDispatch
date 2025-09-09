# Use a small Python base image
FROM python:3.11-slim

# Install FFmpeg + runtime deps (opus is important for Discord voice)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (better build caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your code
COPY . .

# Run the bot
CMD ["python", "main.py"]