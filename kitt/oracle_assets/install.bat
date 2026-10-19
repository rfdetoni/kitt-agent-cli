@echo off
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\OEM\kitt-bootstrap.ps1
if errorlevel 1 (
 echo KITT_ORACLE_BOOTSTRAP_FAILED
 if exist C:\\KittOracle echo FAILED>C:\\KittOracle\\bootstrap.status
 exit /b 1
)
echo KITT_ORACLE_BOOTSTRAP_COMPLETED
exit /b 0
