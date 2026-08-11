#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统后端启动脚本
"""

import sys
import uvicorn
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config import settings

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("🚀 启动餐厅订单数据采集系统后端服务...")
    print(f"📋 版本: {settings.APP_VERSION}")
    print(f"📡 服务地址: http://{settings.HOST}:{settings.PORT}")
    print(f"📚 API文档: http://{settings.HOST}:{settings.PORT}/docs")
    print(f"🔧 调试模式: {'开启' if settings.DEBUG else '关闭'}")
    print("=" * 50)
    
    try:
        # 启动服务器
        uvicorn.run(
            "main:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=settings.DEBUG,
            workers=settings.WORKERS,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("🛑 用户中断，系统关闭")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1) 