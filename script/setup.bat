@echo off
echo Starting Prefect Workflow Service Setup...

REM Check if Docker Desktop is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Docker Desktop is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

REM Create required directories
if not exist "service\prefect\data" mkdir service\prefect\data
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
echo Prefect Workflow Service Setup Complete!
echo ======================================
echo Prefect UI: http://localhost:4200
echo PostgreSQL: localhost:5432
echo Redis: localhost:6379
echo ======================================
echo.

pause
