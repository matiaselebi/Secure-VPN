@echo off
setlocal enabledelayedexpansion

REM --- Auto-elevacion: si no corre como administrador, se relanza pidiendo permisos ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Este panel necesita permisos de administrador para instalar el tunel
    echo como servicio de Windows y para manejar el firewall del kill switch.
    echo Se va a pedir confirmacion de Windows...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

cd /d "%~dp0"

set TASK_NAME=SecureVPNDashboardAutostart
set PYTHONW=%~dp0venv\Scripts\pythonw.exe
set PYTHON=%~dp0venv\Scripts\python.exe
set DASHBOARD_URL=http://127.0.0.1:8891/

if not exist "%PYTHON%" (
    echo.
    echo No encontre el entorno virtual ^(venv^). Antes de usar este menu, abri
    echo una consola en esta carpeta y corre una sola vez:
    echo.
    echo     python -m venv venv
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ================================================
echo   SecureVPN - Panel de control  (admin)
echo ================================================
echo.
echo  1. Encender VPN     (Docker + laboratorio + aprovisionar + conectar, todo en uno)
echo  2. Desconectar VPN  (limpia el kill switch y baja el tunel)
echo  3. Ver estado
echo  4. Agregar peer     (ej: el celular; muestra QR para escanear)
echo  5. Levantar laboratorio   (contenedor Docker que hace de servidor)
echo  6. Aprovisionar servidor  (instala todo por SSH: lab o VPS real)
echo  7. Bajar laboratorio
echo  8. RESTAURAR INTERNET (emergencia: revierte firewall y tunel)
echo  9. Salir
echo.
echo  (todo esto tambien se puede hacer desde el dashboard: %DASHBOARD_URL%)
echo.
set /p opcion="Elegi una opcion (1-9): "

if "%opcion%"=="1" goto conectar
if "%opcion%"=="2" goto desconectar
if "%opcion%"=="3" goto estado
if "%opcion%"=="4" goto peer
if "%opcion%"=="5" goto lab_up
if "%opcion%"=="6" goto aprovisionar
if "%opcion%"=="7" goto lab_down
if "%opcion%"=="8" goto restaurar
if "%opcion%"=="9" goto salir
goto menu

:asegurar_dashboard
REM Deja el dashboard corriendo (si no lo estaba) y registrado para arrancar
REM solo en cada inicio de Windows. Se llama automaticamente al conectar:
REM plug and play, sin opcion aparte, como en SecureProxy/SecureDNS.
schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHONW%\" \"%~dp0scripts\run_dashboard.py\"" /sc onlogon /rl limited /f >nul 2>&1
powershell -NoProfile -Command "[System.Net.WebRequest]::DefaultWebProxy = $null; try { Invoke-WebRequest -Uri '%DASHBOARD_URL%health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% neq 0 (
    schtasks /run /tn "%TASK_NAME%" >nul 2>&1
    timeout /t 2 /nobreak >nul
)
exit /b 0

:conectar
echo.
if not exist "C:\Program Files\WireGuard\wireguard.exe" (
    echo Falta la app oficial de WireGuard. Instalala desde:
    echo     https://www.wireguard.com/install/
    echo y volve a intentar.
    pause
    goto menu
)
echo Dejando el dashboard corriendo (y arrancando solo con Windows)...
call :asegurar_dashboard
echo Encendiendo la VPN completa (Docker, laboratorio, aprovisionar, conectar)...
echo La primera vez puede tardar varios minutos.
"%PYTHON%" scripts\start_vpn.py
if %errorlevel%==0 (
    echo.
    echo Abriendo el dashboard: %DASHBOARD_URL%
    start "" %DASHBOARD_URL%
)
pause
goto menu

:desconectar
echo.
"%PYTHON%" scripts\disconnect_vpn.py
echo.
echo Apagando tambien el dashboard y su inicio automatico...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
"%PYTHON%" scripts\stop_dashboard.py
echo.
echo Todo apagado (tunel, kill switch, dashboard y autostart), igual que
echo hace SecureDNS al detenerse. Se enciende todo de nuevo con la opcion 1.
pause
goto menu

:estado
echo.
"%PYTHON%" scripts\vpn_status.py
echo.
powershell -NoProfile -Command "[System.Net.WebRequest]::DefaultWebProxy = $null; try { Invoke-WebRequest -Uri '%DASHBOARD_URL%health' -UseBasicParsing -TimeoutSec 3 | Out-Null; Write-Host 'Dashboard            : corriendo en %DASHBOARD_URL%' } catch { Write-Host 'Dashboard            : no esta corriendo (se inicia solo al conectar)' }"
echo.
pause
goto menu

:peer
echo.
set /p NOMBRE_PEER="Nombre del nuevo peer (ej: celular): "
if "%NOMBRE_PEER%"=="" (
    echo No ingresaste ningun nombre.
    pause
    goto menu
)
"%PYTHON%" scripts\add_peer.py "%NOMBRE_PEER%"
pause
goto menu

:lab_up
echo.
"%PYTHON%" scripts\lab_up.py
pause
goto menu

:aprovisionar
echo.
"%PYTHON%" scripts\provision_server.py
pause
goto menu

:lab_down
echo.
"%PYTHON%" scripts\lab_down.py
pause
goto menu

:restaurar
echo.
echo Esto revierte el firewall (borra las reglas SecureVPN_ y restablece la
echo politica por defecto) y baja el tunel, pase lo que pase. Tu internet
echo vuelve a la normalidad. No toca reglas de otros programas (las
echo SecureProxy_Block_* de SecureProxy quedan como estan).
set /p CONFIRMA="Confirmar? (s/n): "
if /i not "%CONFIRMA%"=="s" (
    echo Cancelado.
    pause
    goto menu
)
"%PYTHON%" scripts\restore_internet.py
pause
goto menu

:salir
endlocal
exit /b 0
