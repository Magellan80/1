import time
import asyncio
import os
import sys

class AutoRestartManager:
    def __init__(self, polling_timeout=60, ws_timeout=30, heartbeat_timeout=45):
        self.last_polling = time.time()
        self.last_ws = time.time()
        self.last_heartbeat = time.time()

        self.polling_timeout = polling_timeout
        self.ws_timeout = ws_timeout
        self.heartbeat_timeout = heartbeat_timeout

    # Telegram polling heartbeat
    def ping_polling(self):
        self.last_polling = time.time()

    # WS heartbeat
    def ping_ws(self):
        self.last_ws = time.time()

    # Event loop heartbeat
    def ping_heartbeat(self):
        self.last_heartbeat = time.time()

    async def monitor(self):
        while True:
            now = time.time()

            # 1) Polling завис
            if now - self.last_polling > self.polling_timeout:
                print("[AUTO-RESTART] Telegram polling завис. Перезапуск процесса...")
                self.restart_process()

            # 2) WS завис
            if now - self.last_ws > self.ws_timeout:
                print("[AUTO-RESTART] WS завис. Перезапуск процесса...")
                self.restart_process()

            # 3) Event loop завис
            if now - self.last_heartbeat > self.heartbeat_timeout:
                print("[AUTO-RESTART] Event loop завис. Перезапуск процесса...")
                self.restart_process()

            await asyncio.sleep(5)

    def restart_process(self):
        print("[AUTO-RESTART] Выполняю полный рестарт процесса...")
        python = sys.executable
        os.execv(python, [python] + sys.argv)
