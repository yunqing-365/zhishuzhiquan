@echo off
:: AI-Echo 开发环境一键启动脚本 (Windows)
:: 用法：双击运行，或在命令行执行 dev.bat

title AI-Echo Dev Launcher

echo.
echo   =====================================
echo     指数之源 AI-Echo  Dev Launcher
echo   =====================================
echo.

:: 检查 .env
if not exist ".env" (
  echo [警告] 未找到 .env，从模板创建...
  copy .env.example .env
  echo [提示] 已创建 .env，请检查配置后重新运行
  pause
  exit /b 1
)
echo [OK] .env 已加载

:: 安装前端依赖
if not exist "node_modules" (
  echo [>>] 安装前端依赖...
  call npm install
)

:: 安装后端依赖（静默检测）
python -c "import fastapi" 2>nul || (
  echo [>>] 安装后端依赖...
  pip install -r ai-echo-backend\requirements.txt
)

echo.
echo [>>] 启动后端 (port 8000)...
start "AI-Echo Backend" cmd /k "cd ai-echo-backend && python -m uvicorn oracle_engine:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 2 /nobreak > nul

echo [>>] 启动前端 (port 5173)...
start "AI-Echo Frontend" cmd /k "npm run dev"

echo.
echo [OK] 服务已启动:
echo      前端: http://localhost:5173
echo      后端: http://localhost:8000
echo      API文档: http://localhost:8000/docs
echo.
echo 关闭上面两个窗口即可停止服务
pause
