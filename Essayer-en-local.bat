@echo off
chcp 65001 >nul
title Passerelle en ligne (essai local)
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   Python n'est pas installe. Installez-le depuis https://www.python.org/downloads/windows/
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)
echo.
echo   Passerelle s'ouvre dans votre navigateur : http://localhost:8000
echo   Gardez cette fenetre ouverte ; fermez-la pour arreter.
echo.
start "" "http://localhost:8000"
%PY% -m http.server 8000
pause
