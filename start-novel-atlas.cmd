@echo off
rem 切换到启动文件所在目录，避免双击时找不到项目文件。
cd /d "%~dp0"
rem 启动应用；浏览器会自动打开本机页面。
python launcher.py
rem 启动失败时保留窗口，方便用户看到错误原因。
if errorlevel 1 pause
