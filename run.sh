#!/bin/bash

cd "$(dirname "$0")"

echo "========================================"
echo "  遗产日活动排班管理系统"
echo "========================================"
echo ""

echo "[1/4] 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到Python3，请先安装Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "   ✓ Python $PYTHON_VERSION"

echo ""
echo "[2/4] 创建虚拟环境并安装依赖..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "   ✓ 依赖安装完成"

echo ""
echo "[3/4] 初始化数据库和测试数据..."
python3 -m app.seed_data
echo "   ✓ 数据初始化完成"

echo ""
echo "[4/4] 启动服务..."
echo ""
echo "📖 API文档地址: http://localhost:8080/docs"
echo "📖 备选文档地址: http://localhost:8080/redoc"
echo ""
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo ""

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
