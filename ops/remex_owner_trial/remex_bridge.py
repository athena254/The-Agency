"""Owner-only Telegram transport for the existing metered Agency pilot API."""
import asyncio
import json
import logging
import os
import pathlib
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).parent
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
    os.environ.pop(key, None)
sys.path.insert(0, str(ROOT / 'release' / 'src'))
from agency.telegram.adapter import TelegramAdapter
from agency.telegram.config import TelegramConfig

OWNER = 5981285472
BASE = 'http://127.0.0.1:8001'
TERMINAL = {'COMPLETED', 'FAILED', 'UNCERTAIN', 'CANCELLED', 'DENIED'}
COMMANDS = [
    {'command': 'start', 'description': 'About this private Agency trial'},
    {'command': 'status', 'description': 'Check model and execution readiness'},
    {'command': 'new', 'description': 'Start a new conversation'},
]


def permitted(update):
    if not isinstance(update, dict):
        return False
    message = update.get('message')
    if not isinstance(message, dict):
        return False
    sender, chat = message.get('from'), message.get('chat')
    return (type(update.get('update_id')) is int and update['update_id'] > 0
            and isinstance(sender, dict) and isinstance(chat, dict)
            and type(sender.get('id')) is int and sender['id'] == OWNER
            and type(chat.get('id')) is int and chat['id'] == OWNER
            and chat.get('type') == 'private'
            and isinstance(message.get('text'), str))


class Bridge:
    def __init__(self):
        import httpx
        credentials = json.loads((ROOT / 'secrets.json').read_text())
        self.adapter = TelegramAdapter(TelegramConfig(
            bot_token=credentials['telegram_bot_token'], beta_mode=False,
            api_base='https://api.telegram.org',
            allowed_chat_ids=[OWNER], allowed_user_ids=[OWNER], parse_mode='', timeout=35))
        self.client = httpx.AsyncClient(base_url=BASE, timeout=15, trust_env=False,
            headers={'Authorization': 'Bearer ' + (ROOT / 'web-data/owner.token').read_text().strip(),
                     'Origin': BASE})
        self.db = sqlite3.connect(ROOT / 'bridge.db')
        self.db.executescript('CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY,v TEXT NOT NULL);'
            'CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);')
        self.db.commit()

    def get(self, key):
        row = self.db.execute('SELECT v FROM kv WHERE k=?', (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, str(value)))
        self.db.commit()

    async def api(self, method, path, payload=None):
        headers = {}
        if method == 'POST':
            session = await self.client.get('/api/session')
            session.raise_for_status()
            headers['X-Prototype-CSRF'] = session.json()['csrf_token']
        response = await self.client.request(method, path, json=payload, headers=headers)
        data = response.json()
        if not response.is_success:
            raise RuntimeError('pilot:' + str(data.get('code', response.status_code)))
        return data

    async def handle(self, update):
        if not permitted(update):
            return
        uid = update['update_id']
        text = update['message']['text'].strip()
        command = text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
        if command in {'/start', '/help'}:
            reply = ('Remex is connected to Mercury 3 and your private latest-code trial workspace. '
                     'Send a message, ask for a calculation, or use /new and /status. '
                     'Tools are limited to calculator, UTC time and web search. '
                     'Provider charges may apply; no paid fallback is configured. '
                     'Your older workspace and uncertain execution are untouched.')
        elif command == '/status':
            caps = await self.api('GET', '/api/pilot/capabilities')
            reply = ('Mercury 3 | ' + ('BLOCKED: unresolved execution; no automatic retry.'
                if caps['uncertainty_blocked'] else 'Ready') + '\nTools: ' + ', '.join(caps['tools']))
        elif command == '/new':
            title = text.partition(' ')[2].strip()[:120] or 'Remex conversation'
            data = await self.api('POST', '/api/pilot/threads',
                {'request_id': 'tg-remex-thread-' + str(uid), 'title': title})
            self.put('thread', data['thread']['id'])
            reply = 'New conversation: ' + data['thread']['title']
        elif command.startswith('/'):
            reply = 'Supported commands: /start, /status, /new. Or send an ordinary message.'
        elif not text or len(text) > 8000:
            reply = 'Please send between 1 and 8000 characters.'
        else:
            row = self.db.execute('SELECT payload FROM turns WHERE id=?', (uid,)).fetchone()
            if row:
                payload = json.loads(row[0])
            else:
                thread = self.get('thread')
                if not thread:
                    data = await self.api('POST', '/api/pilot/threads',
                        {'request_id': 'tg-remex-thread-default', 'title': 'Remex conversation'})
                    thread = data['thread']['id']
                    self.put('thread', thread)
                payload = {'request_id': 'tg-remex-' + str(uid), 'thread_id': thread, 'message': text}
                self.db.execute('INSERT INTO turns VALUES (?,?)', (uid, json.dumps(payload)))
                self.db.commit()
            known_task = self.get('task:' + str(uid))
            task = (await self.api('GET', '/api/pilot/tasks/' + known_task) if known_task
                    else await self.api('POST', '/api/pilot/tasks', payload))
            self.put('task:' + str(uid), task['task_id'])
            deadline = time.monotonic() + 150
            while task['state'] not in TERMINAL and time.monotonic() < deadline:
                await asyncio.sleep(1)
                task = await self.api('GET', '/api/pilot/tasks/' + task['task_id'])
            if task['state'] == 'COMPLETED':
                reply = task['response'] or 'Completed without a response body.'
            else:
                reply = ('Task state: ' + task['state'] + '. Outcome is not confirmed successful. '
                         'Do not retry automatically. View the saved task in the web workspace.')
        await self.adapter.send_message(OWNER, reply, parse_mode='')
        self.put('last_owner_update', uid)

    async def process_update(self, update):
        uid = update.get('update_id')
        if type(uid) is not int or uid <= 0:
            return True
        try:
            await self.handle(update)
        except Exception as exc:
            known = self.get('task:' + str(uid))
            if known:
                if not self.get('notice:' + str(uid)):
                    await self.adapter.send_message(OWNER,
                        'Task accepted; its outcome/status is temporarily unavailable. '
                        'No replacement execution will be submitted. Saved task: ' + known,
                        parse_mode='')
                    self.put('notice:' + str(uid), 'sent')
                return False
            code = str(exc) if isinstance(exc, RuntimeError) else ''
            if not code.startswith('pilot:'):
                raise
            await self.adapter.send_message(OWNER,
                'Request outcome is unavailable (' + code[6:] + '). '
                'No success is confirmed and it will not be resent automatically. '
                'Check the web workspace. Saved request: tg-remex-' + str(uid), parse_mode='')
        self.put('offset', uid + 1)
        return True

    async def run(self):
        try:
            await self.adapter.set_my_commands(COMMANDS)
            while True:
                offset = self.get('offset')
                try:
                    updates = await self.adapter.get_updates(offset=int(offset) if offset else None, timeout=20)
                    for update in updates:
                        if not await self.process_update(update):
                            await asyncio.sleep(5)
                            break
                    (ROOT / 'bridge-status.json').write_text(json.dumps({
                        'state': 'polling', 'owner_id': OWNER, 'model': 'mercury-3',
                        'last_poll_unix': time.time(), 'last_owner_update': self.get('last_owner_update')}))
                except Exception as exc:
                    if getattr(getattr(exc, 'response', None), 'status_code', None) == 409:
                        raise RuntimeError('competing Telegram poller; stopped') from None
                    print('bridge_transport_error', type(exc).__name__, flush=True)
                    await asyncio.sleep(5)
        finally:
            try:
                await self.adapter.close()
            finally:
                try:
                    await self.client.aclose()
                finally:
                    self.db.close()


def main():
    import msvcrt
    lock = (ROOT / 'bridge.lock').open('a+b')
    lock.seek(0)
    if lock.read(1) == b'':
        lock.write(b'1')
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    sys.stdout = (ROOT / 'bridge.stdout.log').open('a', encoding='utf-8', buffering=1)
    sys.stderr = (ROOT / 'bridge.stderr.log').open('a', encoding='utf-8', buffering=1)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    print('owner_pilot_telegram_bridge_starting', flush=True)
    asyncio.run(Bridge().run())


if __name__ == '__main__':
    main()
