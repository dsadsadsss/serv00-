import paramiko
import schedule
import time
import os
import sys
from flask import Flask, jsonify, render_template_string, request, redirect, session
from threading import Thread
import logging
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # 请替换为一个随机的安全密钥

vps_status = {}
start_time = time.time()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger()

# 设置访问密码
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'your_default_password') 

def get_vps_configs():
    configs = []
    index = 1
    while True:
        hostname = os.environ.get(f'HOSTNAME_{index}')
        if not hostname:
            break
        
        username = os.environ.get(f'USERNAME_{index}')
        password = os.environ.get(f'PASSWORD_{index}')
        
        script_paths = []
        script_index = 1
        while True:
            script_path = os.environ.get(f'SCRIPT_PATHS_{index}_{script_index}')
            if not script_path:
                break
            port = os.environ.get(f'PORTS_{index}_{script_index}')
            script_paths.append((script_path.strip(), port))
            script_index += 1
        
        for script_path, port in script_paths:
            configs.append({
                'index': index,
                'hostname': hostname,
                'username': username,
                'password': password,
                'script_path': script_path,
                'port': port
            })
        
        index += 1
    return configs

def check_and_run_script(config):
    logger.info(f"Checking VPS {config['index']}: {config['hostname']} - {config['script_path']}")
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=config['hostname'], username=config['username'], password=config['password'], port=22)
        
        port = config.get('port')
        script_path = config['script_path']
        script_name = os.path.basename(script_path)
        key = f"{config['index']}:{config['hostname']}:{script_name}"
        
        if port:
            check_command = f"sockstat -4 -l | grep ':{port}'"
            stdin, stdout, stderr = client.exec_command(check_command)
            output = stdout.read().decode('utf-8').strip()
            
            if output:
                # 解析输出获取 'user', 'command', 'pid'
                lines = output.strip().split('\n')
                line = lines[0]
                parts = line.split()
                if len(parts) >= 3:
                    user = parts[0]
                    command = parts[1]
                    pid = parts[2]
                else:
                    user = command = pid = 'N/A'
                
                status = "Running"
                vps_status[key] = {
                    'index': config['index'],
                    'status': status,
                    'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'username': config['username'],
                    'script_name': script_name,
                    'user': user,
                    'command': command,
                    'pid': pid
                }
                logger.info(f"Port {port} is in use on VPS {config['index']} ({config['hostname']}), process is running.")
                # 服务正在运行，无需执行后续脚本检查
                return
            else:
                # 端口未被占用，继续检查脚本进程或启动脚本
                logger.info(f"Port {port} is not in use on VPS {config['index']} ({config['hostname']}), proceeding to check script process.")
        
        # 现在继续检查脚本是否正在运行
        check_command = f"ps aux | grep '{script_path}' | grep -v grep"
        stdin, stdout, stderr = client.exec_command(check_command)
        output = stdout.read().decode('utf-8').strip()
        
        if output and script_path in output:
            status = "Running"
            # 解析输出获取 PID 等信息
            lines = output.strip().split('\n')
            line = lines[0]
            parts = line.split()
            if len(parts) >= 2:
                user = parts[0]
                pid = parts[1]
            else:
                user = pid = 'N/A'
            command = script_name
            vps_status[key] = {
                'index': config['index'],
                'status': status,
                'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
                'username': config['username'],
                'script_name': script_name,
                'user': user,
                'command': command,
                'pid': pid
            }
        else:
            logger.info(f"Script {script_name} not running. Attempting to restart.")
            stdin, stdout, stderr = client.exec_command(f"nohup /bin/sh {script_path} > /dev/null 2>&1 & echo $!")
            new_pid = stdout.read().decode('utf-8').strip()
            
            if new_pid.isdigit():
                status = "Restarted"
                pid = new_pid
                user = config['username']
                command = script_name
            else:
                status = "Restart Failed"
                pid = user = command = 'N/A'
            
            vps_status[key] = {
                'index': config['index'],
                'status': status,
                'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
                'username': config['username'],
                'script_name': script_name,
                'user': user,
                'command': command,
                'pid': pid
            }
            
    except Exception as e:
        logger.error(f"Error occurred while checking VPS {config['index']} - {config['hostname']} - {script_name}: {str(e)}")
        vps_status[f"{config['index']}:{config['hostname']}:{script_name}"] = {
            'index': config['index'],
            'status': f"Error: {str(e)}",
            'last_check': time.strftime('%Y-%m-%d %H:%M:%S'),
            'username': config['username'],
            'script_name': script_name,
            'user': 'N/A',
            'command': 'N/A',
            'pid': 'N/A'
        }
    finally:
        if client:
            client.close()

def check_all_vps():
    logger.info("Starting VPS check")
    for config in get_vps_configs():
        check_and_run_script(config)
    
    table = "+---------+-----------------------+------------------+----------+-------------------------+----------+----------+----------+----------+\n"
    table += "| Index   | Hostname              | Script Name      | Status   | Last Check              | Username | User     | Command  | PID      |\n"
    table += "+---------+-----------------------+------------------+----------+-------------------------+----------+----------+----------+----------+\n"
    
    for key, status in vps_status.i
