#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内存管理器
内存使用优化和清理
"""

import asyncio
import gc
import logging
import psutil
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# 定义北京时区
CHINA_TZ = timezone(timedelta(hours=8))

class MemoryManager:
    """内存管理器"""
    
    def __init__(self):
        self.process = psutil.Process(os.getpid())
        self.memory_stats = {
            'peak_memory': 0,
            'cleanup_count': 0,
            'gc_collections': 0,
            'last_cleanup': None,
            'memory_warnings': 0
        }
        
        # 内存阈值配置
        self.memory_thresholds = {
            'warning': 512 * 1024 * 1024,    # 512MB 警告阈值
            'critical': 1024 * 1024 * 1024,  # 1GB 临界阈值
            'cleanup': 768 * 1024 * 1024     # 768MB 清理阈值
        }
        
        # 清理任务
        self.cleanup_task = None
        self.monitoring_task = None
        
        logger.info("🧠 内存管理器初始化完成")
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """获取当前内存使用情况"""
        try:
            memory_info = self.process.memory_info()
            memory_percent = self.process.memory_percent()
            
            # 系统内存信息
            system_memory = psutil.virtual_memory()
            
            return {
                'rss': memory_info.rss,  # 常驻内存
                'vms': memory_info.vms,  # 虚拟内存
                'percent': memory_percent,
                'available_mb': system_memory.available / (1024 * 1024),
                'total_mb': system_memory.total / (1024 * 1024),
                'system_usage_percent': system_memory.percent,
                'rss_mb': memory_info.rss / (1024 * 1024),
                'vms_mb': memory_info.vms / (1024 * 1024)
            }
        except Exception as e:
            logger.error(f"获取内存使用情况失败: {e}")
            return {}
    
    def check_memory_pressure(self) -> str:
        """检查内存压力级别"""
        try:
            memory_usage = self.get_memory_usage()
            rss = memory_usage.get('rss', 0)
            
            if rss >= self.memory_thresholds['critical']:
                return 'critical'
            elif rss >= self.memory_thresholds['cleanup']:
                return 'high'
            elif rss >= self.memory_thresholds['warning']:
                return 'warning'
            else:
                return 'normal'
        except Exception as e:
            logger.error(f"检查内存压力失败: {e}")
            return 'unknown'
    
    async def cleanup_memory(self, force: bool = False) -> bool:
        """清理内存"""
        try:
            logger.info("🧹 开始内存清理...")
            
            # 记录清理前的内存使用
            before_memory = self.get_memory_usage()
            before_rss = before_memory.get('rss_mb', 0)
            
            # 1. 强制垃圾回收
            collected_objects = 0
            for generation in range(3):
                collected = gc.collect(generation)
                collected_objects += collected
            
            # 2. 强制内存整理（仅在高压力或强制时）
            pressure = self.check_memory_pressure()
            if force or pressure in ['critical', 'high']:
                gc.collect()
                # 在Python中没有直接的内存整理，但可以清理一些内部缓存
                import sys
                if hasattr(sys, '_clear_type_cache'):
                    sys._clear_type_cache()
            
            # 记录清理后的内存使用
            after_memory = self.get_memory_usage()
            after_rss = after_memory.get('rss_mb', 0)
            freed_mb = before_rss - after_rss
            
            # 更新统计
            self.memory_stats['cleanup_count'] += 1
            self.memory_stats['gc_collections'] += collected_objects
            self.memory_stats['last_cleanup'] = datetime.now(CHINA_TZ)
            
            logger.info(f"✅ 内存清理完成 - 释放: {freed_mb:.2f}MB, 回收对象: {collected_objects}个")
            logger.info(f"📊 内存使用: {before_rss:.2f}MB -> {after_rss:.2f}MB")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ 内存清理失败: {e}")
            return False
    
    async def monitor_memory(self):
        """内存监控任务"""
        try:
            memory_usage = self.get_memory_usage()
            pressure = self.check_memory_pressure()
            
            # 更新峰值内存
            current_rss = memory_usage.get('rss', 0)
            if current_rss > self.memory_stats['peak_memory']:
                self.memory_stats['peak_memory'] = current_rss
            
            # 根据内存压力采取行动
            if pressure == 'critical':
                logger.warning(f"🚨 内存使用临界: {memory_usage.get('rss_mb', 0):.2f}MB")
                await self.cleanup_memory(force=True)
                self.memory_stats['memory_warnings'] += 1
                
            elif pressure == 'high':
                logger.warning(f"⚠️ 内存使用偏高: {memory_usage.get('rss_mb', 0):.2f}MB")
                await self.cleanup_memory()
                
            elif pressure == 'warning':
                logger.info(f"💡 内存使用警告: {memory_usage.get('rss_mb', 0):.2f}MB")
                
            # 每5分钟记录一次内存状态
            if int(time.time()) % 300 == 0:
                logger.info(f"📊 内存状态: {memory_usage.get('rss_mb', 0):.2f}MB ({memory_usage.get('percent', 0):.1f}%)")
                
        except Exception as e:
            logger.error(f"内存监控失败: {e}")
    
    async def start_background_tasks(self):
        """启动后台监控任务"""
        try:
            # 启动内存监控任务
            self.monitoring_task = asyncio.create_task(self._periodic_monitoring())
            
            # 启动定期清理任务
            self.cleanup_task = asyncio.create_task(self._periodic_cleanup())
            
            logger.info("✅ 内存管理后台任务已启动")
            
        except Exception as e:
            logger.error(f"启动内存管理后台任务失败: {e}")
    
    async def _periodic_monitoring(self):
        """定期内存监控"""
        while True:
            try:
                await self.monitor_memory()
                await asyncio.sleep(30)  # 每30秒监控一次
                
            except asyncio.CancelledError:
                logger.info("内存监控任务已取消")
                break
            except Exception as e:
                logger.error(f"定期内存监控失败: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟
    
    async def _periodic_cleanup(self):
        """定期内存清理"""
        while True:
            try:
                await asyncio.sleep(600)  # 每10分钟执行一次
                
                # 检查是否需要清理
                pressure = self.check_memory_pressure()
                if pressure in ['warning', 'high', 'critical']:
                    await self.cleanup_memory()
                    
            except asyncio.CancelledError:
                logger.info("内存清理任务已取消")
                break
            except Exception as e:
                logger.error(f"定期内存清理失败: {e}")
                await asyncio.sleep(300)  # 出错后等待5分钟
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """获取内存管理统计"""
        try:
            current_memory = self.get_memory_usage()
            pressure = self.check_memory_pressure()
            
            return {
                'current_usage': current_memory,
                'pressure_level': pressure,
                'peak_memory_mb': self.memory_stats['peak_memory'] / (1024 * 1024),
                'cleanup_count': self.memory_stats['cleanup_count'],
                'gc_collections': self.memory_stats['gc_collections'],
                'memory_warnings': self.memory_stats['memory_warnings'],
                'last_cleanup': self.memory_stats['last_cleanup'].isoformat() if self.memory_stats['last_cleanup'] else None,
                'thresholds': {
                    'warning_mb': self.memory_thresholds['warning'] / (1024 * 1024),
                    'cleanup_mb': self.memory_thresholds['cleanup'] / (1024 * 1024),
                    'critical_mb': self.memory_thresholds['critical'] / (1024 * 1024)
                },
                'gc_stats': {
                    'generation_0': gc.get_count()[0],
                    'generation_1': gc.get_count()[1], 
                    'generation_2': gc.get_count()[2],
                    'garbage_objects': len(gc.garbage)
                }
            }
        except Exception as e:
            logger.error(f"获取内存统计失败: {e}")
            return {"error": str(e)}
    
    async def emergency_cleanup(self) -> bool:
        """紧急内存清理"""
        try:
            logger.warning("🚨 执行紧急内存清理...")
            
            # 强制垃圾回收
            for _ in range(3):
                gc.collect()
            
            # 清理导入模块缓存
            import sys
            if hasattr(sys, '_clear_type_cache'):
                sys._clear_type_cache()
            
            logger.warning("✅ 紧急内存清理完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 紧急内存清理失败: {e}")
            return False
    
    async def stop_background_tasks(self):
        """停止后台任务"""
        try:
            if self.monitoring_task and not self.monitoring_task.done():
                self.monitoring_task.cancel()
                await self.monitoring_task
            
            if self.cleanup_task and not self.cleanup_task.done():
                self.cleanup_task.cancel()
                await self.cleanup_task
            
            logger.info("✅ 内存管理后台任务已停止")
            
        except Exception as e:
            logger.error(f"停止内存管理后台任务失败: {e}")

# 创建全局内存管理器实例
memory_manager = MemoryManager() 