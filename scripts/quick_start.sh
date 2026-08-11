#!/bin/bash

# KDS后端快速启动脚本

echo "🚀 KDS后厨控菜系统后端快速启动"
echo "=================================="

# 检查Python版本
echo "🔍 检查Python版本..."
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
required_version="3.8"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" = "$required_version" ]; then
    echo "✅ Python版本符合要求: $python_version"
else
    echo "❌ Python版本过低，需要3.8+，当前版本: $python_version"
    exit 1
fi

# 检查依赖
echo "🔍 检查依赖..."
if [ ! -f "requirements.txt" ]; then
    echo "❌ requirements.txt文件不存在"
    exit 1
fi

# 安装依赖
echo "📦 安装Python依赖..."
pip3 install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ 依赖安装失败"
    exit 1
fi

echo "✅ 依赖安装完成"

# 检查配置文件
echo "🔍 检查配置文件..."
if [ ! -f "config.py" ]; then
    echo "⚠️ 配置文件不存在，使用默认配置"
    echo "📝 请根据需要修改config.py文件"
fi

# 安装Playwright浏览器
echo "🌐 安装Playwright浏览器..."
python3 -m playwright install chromium

if [ $? -ne 0 ]; then
    echo "❌ Playwright浏览器安装失败"
    exit 1
fi

echo "✅ Playwright浏览器安装完成"

# 环境检查
echo "🔍 运行环境检查..."
python3 check_environment.py

if [ $? -ne 0 ]; then
    echo "❌ 环境检查失败，请修复后重试"
    exit 1
fi

echo "✅ 环境检查通过"

# 创建日志目录
echo "📁 创建日志目录..."
mkdir -p logs

# 启动服务
echo "🚀 启动KDS后端服务..."
echo "📡 服务地址: http://localhost:8000"
echo "📚 API文档: http://localhost:8000/docs"
echo "🔧 按Ctrl+C停止服务"
echo "=================================="

# 启动服务
python3 start.py 