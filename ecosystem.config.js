module.exports = {
  apps: [{
    name: 'statistical-tool-backend',
    script: 'venv/bin/uvicorn',
    args: 'main:app --host 0.0.0.0 --port 8000 --workers 4',
    interpreter: 'none',
    cwd: process.env.HOME + '/apps/statistical-tool-backend',
    instances: 1,
    exec_mode: 'fork',
    env: {
      NODE_ENV: 'production',
      PYTHONUNBUFFERED: '1',
      PYTHONDONTWRITEBYTECODE: '1'
    },
    error_file: './logs/pm2-error.log',
    out_file: './logs/pm2-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: true,
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    max_memory_restart: '2G',
    watch: false,
    ignore_watch: ['node_modules', 'logs', 'temp_files', 'venv', '.git', '__pycache__'],
  }]
};
