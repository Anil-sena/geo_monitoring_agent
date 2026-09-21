# Use official Python image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for geospatial libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Upgrade pip
RUN pip install --upgrade pip

# Copy requirements and install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Change to backend directory
WORKDIR /app/backend

# Expose port
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "geoagent.main:app", "--host", "0.0.0.0", "--port", "8000"]