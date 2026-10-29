@echo off
REM Local build of ValorInventoryAgent-Setup.exe (needs Python 3.10+ and Inno Setup 6: jrsoftware.org/isdl.php)
python -m pip install --upgrade pyinstaller pywebview pythonnet || goto :err
python -m PyInstaller --noconfirm --windowed --onedir --name ValorInventoryAgent --icon app.ico --collect-all webview app.py || goto :err
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
%ISCC% installer.iss || goto :err
echo.
echo Done. Installer: installer_out\ValorInventoryAgent-Setup.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
