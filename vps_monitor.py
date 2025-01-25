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
    
    for key, status in vps_status.items():
        index, hostname, script_name = key.split(':')
        table += "| {:<7} | {:<21} | {:<16} | {:<8} | {:<23} | {:<8} | {:<8} | {:<8} | {:<8} |\n".format(
            status['index'], hostname[:21], script_name[:16], status['status'][:8],
            status['last_check'], status['username'][:8],
            status.get('user', 'N/A')[:8], status.get('command', 'N/A')[:8], status.get('pid', 'N/A')[:8]
        )
        table += "+---------+-----------------------+------------------+----------+-------------------------+----------+----------+----------+----------+\n"
    
    logger.info("\n" + table)

# 登录功能
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect('/')
        else:
            return render_template_string('''
                <!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                    <meta charset="UTF-8">
                    <title>登录</title>
                    <style>
                        body {
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background-color: #f2f2f2;
                        }
                        .login-container {
                            text-align: center;
                            background-color: #ffffff;
                            padding: 30px;
                            border-radius: 8px;
                            box-shadow: 0px 0px 10px 0px #aaaaaa;
                        }
                        p.error {
                            color: red;
                        }
                    </style>
                </head>
                <body>
                    <div class="login-container">
                        <h2>密码错误</h2>
                        <a href="/login">返回</a>
                    </div>
                </body>
                </html>
            ''')
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <title>登录</title>
            <style>
                body {
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background-color: #f2f2f2;
                }
                .login-container {
                    text-align: center;
                    background-color: #ffffff;
                    padding: 30px;
                    border-radius: 8px;
                    box-shadow: 0px 0px 10px 0px #aaaaaa;
                }
                input[type=password], input[type=submit] {
                    padding: 10px;
                    margin: 10px;
                    font-size: 16px;
                    width: 100%;
                }
                input[type=submit] {
                    width: 50%;
                }
            </style>
        </head>
        <body>
            <div class="login-container">
                <h2>请输入密码</h2>
                <form method="post">
                    <p><input type="password" name="password" placeholder="密码"></p>
                    <p><input type="submit" value="登录"></p>
                </form>
            </div>
        </body>
        </html>
    ''')

@app.route('/')
@login_required
def index():
    html = '''
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>Serv00 状态概览</title>
        <style>
            body {
                font-family: Arial, sans-serif;
            }
            .container {
                width: 90%;
                margin: 0 auto;
            }
            h1 {
                text-align: center;
            }
            #executeButton {
                display: block;
                margin: 20px auto;
                padding: 10px 20px;
                font-size: 16px;
            }
            #result {
                text-align: center;
                color: green;
                font-weight: bold;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
            }
            table, th, td {
                border: 1px solid #dddddd;
            }
            th, td {
                padding: 8px;
                text-align: center;
            }
            th {
                background-color: #f2f2f2;
            }
            tr:nth-child(even){
                background-color: #f9f9f9;
            }
        </style>
    </head>
    <body>
    <div class="container">
        <h1>Serv00 状态概览</h1>
        <button id="executeButton" onclick="executeTasks()">立即执行所有任务</button>
        <p id="result"></p>
        <table>
            <tr>
                <th>Index</th>
                <th>Hostname</th>
                <th>Script Name</th>
                <th>Status</th>
                <th>Last Check</th>
                <th>Username</th>
                <th>User</th>
                <th>Command</th>
                <th>PID</th>
            </tr>
            {% for key, data in vps_status.items() %}
            <tr>
                <td>{{ data.index }}</td>
                <td><a href="/status/{{ key }}">{{ key.split(':')[1] }}</a></td>
                <td>{{ data.script_name }}</td>
                <td>{{ data.status }}</td>
                <td>{{ data.last_check }}</td>
                <td>{{ data.username }}</td>
                <td>{{ data.user }}</td>
                <td>{{ data.command }}</td>
                <td>{{ data.pid }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    <script>
    function executeTasks() {
        fetch('/execute', {
            method: 'POST',
            credentials: 'include'
        })
        .then(response => response.json())
        .then(data => {
            document.getElementById('result').innerText = data.status;
            setTimeout(function(){ location.reload(); }, 5000); // 5秒后刷新页面
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('result').innerText = '执行任务时出错。';
        });
    }
    </script>
    </body>
    </html>
    '''
    return render_template_string(html, vps_status=vps_status)

@app.route('/execute', methods=['POST'])
@login_required
def execute_tasks():
    # 异步执行任务，避免阻塞请求
    Thread(target=check_all_vps).start()
    return jsonify({"status": "所有任务正在执行，页面将在5秒后刷新。"})

@app.route('/status/<path:key>')
@login_required
def vps_status_detail(key):
    return jsonify(vps_status[key]) if key in vps_status else (jsonify({"error": "VPS or script not found"}), 404)

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "uptime": time.time() - start_time}), 200

def run_flask():
    app.run(host='0.0.0.0', port=7860)

def main():
    global start_time
    start_time = time.time()
    
    logger.info("===== VPS monitoring script is starting =====")
    
    Thread(target=run_flask).start()
    logger.info("Flask server started in background")

    check_all_vps()
    schedule.every(4).hours.do(check_all_vps)
    logger.info("Scheduled VPS check every 4 hours")
    
    logger.info("===== VPS monitoring script is running =====")
    
    heartbeat_count = 0
    while True:
        schedule.run_pending()
        time.sleep(60)
        heartbeat_count += 1
        if heartbeat_count % 60 == 0:
            uptime_hours = heartbeat_count // 60
            logger.info(f"Heartbeat: Script is still running. Uptime: {uptime_hours} hours")

if __name__ == "__main__":
    main()
