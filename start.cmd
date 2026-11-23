@echo off
chcp 866>nul
cd /d "%~dp0"

echo Запускаю Melke: PostgreSQL и AI-помощник...
docker compose up -d
if errorlevel 1 goto fail

echo Жду готовности API...
for /l %%i in (1,1,45) do (
    curl -s -f -o nul http://localhost:8000/health && goto ready
    ping -n 3 127.0.0.1 >nul
)
goto fail

:ready
echo Готово: http://localhost:8000/
start "" http://localhost:8000/
ping -n 4 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo Не удалось запустить. Проверьте, что Docker Desktop работает, и запустите файл снова.
pause
exit /b 1
