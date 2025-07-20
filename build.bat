:: This batch file converts a Python script into a Windows executable.
@echo off
setlocal

:: Determine if we have 'python' or 'python3' in the path. On Windows, the
:: Python executable is typically called 'python', so check that first.
where /q python
if ERRORLEVEL 1 goto python3
set PYTHON=python
goto build

:python3
where /q python3
if ERRORLEVEL 1 goto nopython
set PYTHON=python3

:: Verify the setup script has been run
:build
set VENV=.venv
set DIST_DIR=dist
:: Replace 'plugin' with the name of your plugin
set PLUGIN_DIR=%DIST_DIR%\flux
set NVIDIA_PLUGIN_DIR=C:\ProgramData\NVIDIA Corporation\nvtopps\rise\plugins\flux

if exist %VENV% (
	call %VENV%\Scripts\activate.bat

	:: Ensure plugin subfolder exists
	if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"

	:: Replace 'g-assist-plugin' with the name of your plugin
	pyinstaller --onefile --name g-assist-plugin-flux --distpath "%PLUGIN_DIR%" plugin.py
	if exist manifest.json (
		copy /y manifest.json "%PLUGIN_DIR%\manifest.json"
		echo manifest.json copied successfully.
	) 

	if exist config.json (
		copy /y config.json "%PLUGIN_DIR%\config.json"
		echo config.json copied successfully.
	) 

	call %VENV%\Scripts\deactivate.bat
	echo Executable can be found in the "%PLUGIN_DIR%" directory
	
	:: Auto-deploy to NVIDIA directory
	echo.
	echo Deploying to NVIDIA directory...
	
	:: Create NVIDIA plugin directory if it doesn't exist
	if not exist "%NVIDIA_PLUGIN_DIR%" (
		mkdir "%NVIDIA_PLUGIN_DIR%"
		echo Created NVIDIA plugin directory: %NVIDIA_PLUGIN_DIR%
	)
	
	:: Copy manifest.json
	if exist "%PLUGIN_DIR%\manifest.json" (
		copy /y "%PLUGIN_DIR%\manifest.json" "%NVIDIA_PLUGIN_DIR%\manifest.json"
		echo manifest.json deployed to NVIDIA directory.
	)
	
	:: Copy executable
	if exist "%PLUGIN_DIR%\g-assist-plugin-flux.exe" (
		copy /y "%PLUGIN_DIR%\g-assist-plugin-flux.exe" "%NVIDIA_PLUGIN_DIR%\g-assist-plugin-flux.exe"
		if %ERRORLEVEL% EQU 0 (
			echo g-assist-plugin-flux.exe deployed to NVIDIA directory.
		) else (
			echo.
			echo ERROR: Could not copy g-assist-plugin-flux.exe
			echo The file may be in use by G-Assist. Please:
			echo 1. Close G-Assist completely
			echo 2. Run build.bat again
			echo.
		)
	)
	
	:: Copy config.json if it exists
	if exist "%PLUGIN_DIR%\config.json" (
		copy /y "%PLUGIN_DIR%\config.json" "%NVIDIA_PLUGIN_DIR%\config.json"
		echo config.json deployed to NVIDIA directory.
	)
	
	echo.
	echo Deployment complete! Plugin is ready to use.
	exit /b 0
) else (
	echo Please run setup.bat before attempting to build
	exit /b 1
)

:nopython
echo Python needs to be installed and in your path
exit /b 1
