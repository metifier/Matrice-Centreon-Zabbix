@echo off
rem Lance l'interface web Matrice Centreon -> Zabbix (Windows).
rem Au premier lancement : crée un environnement Python local (.venv) et installe les dépendances.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python introuvable. Installez Python 3.10 ou plus recent depuis https://www.python.org/downloads/
  echo en cochant "Add python.exe to PATH", puis relancez ce script.
  goto fin
)
echo Creation de l'environnement Python local (.venv)...
%PY% -m venv .venv
if errorlevel 1 goto erreur

:install
echo Installation / mise a jour des dependances...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto erreur

".venv\Scripts\python.exe" -m centreon_inventory web --ouvrir %*
goto fin

:erreur
echo.
echo ERREUR : voir les messages ci-dessus.
echo Derriere un proxy d'entreprise, definissez-le avant de relancer, par exemple :
echo   set HTTPS_PROXY=http://proxy.entreprise.local:8080

:fin
pause
