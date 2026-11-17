@echo off
setlocal
cmake -S "%~dp0app" -B "%~dp0build" -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release -DCOMMANDER_ENABLE_SCREEN_CAPTURE=OFF %*
if errorlevel 1 exit /b %errorlevel%
cmake --build "%~dp0build"
exit /b %errorlevel%
