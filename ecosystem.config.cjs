module.exports = {
  apps: [
    {
      name: "lex-api",
      script: "/root/lex/.venv/bin/uvicorn",
      args: "api:app --host 0.0.0.0 --port 8000",
      cwd: "/root/lex",
      interpreter: "none",
      restart_delay: 3000,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
        DATA_DIR: "data",
      },
    },
    {
      name: "lex-collector",
      script: "/root/lex/.venv/bin/python",
      args: "-m etl.extract_legislatie --delay 0.8 --batch-size 40",
      cwd: "/root/lex",
      interpreter: "none",
      autorestart: false,
      cron_restart: "0 2 * * *", // Runs every night at 02:00 AM
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1",
        DATA_DIR: "data",
      },
    },
  ],
};
