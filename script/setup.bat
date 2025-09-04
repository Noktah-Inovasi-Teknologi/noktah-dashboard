@echo off
echo Starting Enterprise Dashboard Setup...

REM Check if Docker Desktop is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Docker Desktop is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

REM Create required directories
if not exist "data" mkdir data
if not exist "data\backups" mkdir data\backups
if not exist "data\uploads" mkdir data\uploads
if not exist "data\crewai" mkdir data\crewai
if not exist "log" mkdir log
if not exist "secrets" mkdir secrets

REM Copy environment file
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env
        echo .env file created from example. Please update with your configuration.
    ) else (
        echo Warning: No .env.example file found.
    )
)

REM Build and start services
echo Building and starting services...
docker-compose build
docker-compose up -d

REM Wait for services to be healthy
echo Waiting for services to start...
timeout /t 60

REM Check service health
echo Checking service health...
docker-compose ps

REM Display access information
echo.
echo ======================================
echo Enterprise Dashboard Setup Complete!
echo ======================================
echo Main Dashboard: http://localhost
echo Grafana Monitoring: http://localhost:3001
echo Prefect Workflows: http://localhost:4200
echo CrewAI Service: http://localhost:8000
echo Prometheus Metrics: http://localhost:9090
echo ======================================
echo.

pause