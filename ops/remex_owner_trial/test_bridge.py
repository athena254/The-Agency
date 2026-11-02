"""Offline checks for the small operational Telegram bridge; no real secrets or API calls."""
import asyncio
import importlib.util
import pathlib
import sqlite3
import sys
import unittest
ROOT = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('remex_bridge', ROOT/'remex_bridge.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def update(uid=100, user=5981285472, text='hello'):
    return {'update_id': uid, 'message': {'from': {'id': user}, 'chat': {'id': user, 'type': 'private'}, 'text': text}}


class Sink:
    def __init__(self): self.messages=[]
    async def send_message(self, chat, text, **kwargs): self.messages.append((chat,text)); return {'message_id':1}


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.b=m.Bridge.__new__(m.Bridge); self.b.adapter=Sink(); self.b.db=sqlite3.connect(':memory:')
        self.b.db.executescript('CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT NOT NULL);CREATE TABLE turns(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);')
        self.calls=[]
        async def api(method,path,payload=None):
            self.calls.append((method,path,payload.copy() if payload else None))
            if path=='/api/pilot/threads':return {'thread':{'id':'thread-1','title':'Test'}}
            if path=='/api/pilot/capabilities':return {'uncertainty_blocked':True,'tools':['calculator']}
            return {'task_id':'task-1','state':'COMPLETED','response':'PONG'}
        self.b.api=api
    async def asyncTearDown(self):self.b.db.close()
    async def test_owner_only(self):
        self.assertTrue(m.permitted(update()))
        for value in [update(user=1234),update(user=True),{'update_id':1,'message':{'from':None,'chat':None,'text':'hello'}}]:
            self.assertFalse(m.permitted(value))
    async def test_rejected_has_no_side_effects(self):
        await self.b.handle(update(user=1234));self.assertEqual(self.calls,[]);self.assertEqual(self.b.adapter.messages,[])
    async def test_normal_turn_uses_durable_request(self):
        await self.b.handle(update())
        post=[x for x in self.calls if x[1]=='/api/pilot/tasks'][0][2]
        self.assertEqual(post,{'request_id':'tg-remex-100','thread_id':'thread-1','message':'hello'})
        self.assertEqual(self.b.adapter.messages,[(m.OWNER,'PONG')])
        self.assertIsNotNone(self.b.db.execute('SELECT payload FROM turns WHERE id=100').fetchone())
    async def test_replay_retains_original_thread_and_payload(self):
        await self.b.handle(update());saved=self.b.db.execute('SELECT payload FROM turns WHERE id=100').fetchone()[0]
        self.b.put('thread','other-thread');self.calls.clear()
        await self.b.handle(update())
        self.assertEqual(self.calls,[('GET','/api/pilot/tasks/task-1',None)])
        self.assertEqual(saved,self.b.db.execute('SELECT payload FROM turns WHERE id=100').fetchone()[0])
    async def test_timeout_keeps_id_for_manual_process_replay(self):
        original=self.b.api
        async def flaky(method,path,payload=None):
            if path=='/api/pilot/tasks':raise TimeoutError()
            return await original(method,path,payload)
        self.b.api=flaky
        with self.assertRaises(TimeoutError):await self.b.handle(update())
        saved=self.b.db.execute('SELECT payload FROM turns WHERE id=100').fetchone()[0]
        self.b.api=original;await self.b.handle(update());self.assertEqual(saved,self.b.db.execute('SELECT payload FROM turns WHERE id=100').fetchone()[0])
    async def test_uncertain_is_not_success(self):
        original=self.b.api
        async def uncertain(method,path,payload=None):
            if path=='/api/pilot/tasks':return {'task_id':'task-1','state':'UNCERTAIN','response':None}
            return await original(method,path,payload)
        self.b.api=uncertain;await self.b.handle(update());text=self.b.adapter.messages[0][1]
        self.assertIn('UNCERTAIN',text);self.assertIn('not confirmed successful',text)
    async def test_status_never_dispatches_a_model(self):
        await self.b.handle(update(text='/status'));self.assertEqual(len(self.calls),1);self.assertIn('BLOCKED',self.b.adapter.messages[0][1])

    async def test_observation_error_keeps_offset_and_reads_same_task(self):
        self.b.put('task:100','task-existing')
        original=self.b.api
        async def unavailable(method,path,payload=None):
            if path.endswith('/task-existing'):raise RuntimeError('pilot:store')
            return await original(method,path,payload)
        self.b.api=unavailable
        self.assertFalse(await self.b.process_update(update()))
        self.assertIsNone(self.b.get('offset'))
        self.assertIn('Task accepted',self.b.adapter.messages[0][1])
        self.assertNotIn('cannot accept',self.b.adapter.messages[0][1])
        self.assertFalse(await self.b.process_update(update()))
        self.assertEqual(len(self.b.adapter.messages),1)
        self.b.api=original;self.calls.clear()
        self.assertTrue(await self.b.process_update(update()))
        self.assertEqual(self.calls,[('GET','/api/pilot/tasks/task-existing',None)])
        self.assertEqual(self.b.get('offset'),'101')

    async def test_denied_is_terminal_without_status_polling(self):
        original=self.b.api
        async def denied(method,path,payload=None):
            if path=='/api/pilot/tasks':return {'task_id':'task-1','state':'DENIED','response':None}
            return await original(method,path,payload)
        self.b.api=denied;await self.b.handle(update())
        self.assertIn('DENIED',self.b.adapter.messages[0][1])
        self.assertFalse(any(x[1]=='/api/pilot/tasks/task-1' for x in self.calls))

    async def test_submission_error_does_not_claim_rejection_or_success(self):
        original=self.b.api
        async def unavailable(method,path,payload=None):
            if path=='/api/pilot/tasks':raise RuntimeError('pilot:store')
            return await original(method,path,payload)
        self.b.api=unavailable;self.assertTrue(await self.b.process_update(update()))
        self.assertIn('No success is confirmed',self.b.adapter.messages[0][1])
        self.assertIn('will not be resent automatically',self.b.adapter.messages[0][1])
        self.assertEqual(self.b.get('offset'),'101')

    async def test_registration_failure_closes_every_resource(self):
        closed=[]
        class Adapter:
            async def set_my_commands(self,commands):raise RuntimeError('synthetic registration error')
            async def close(self):closed.append('adapter');raise RuntimeError('synthetic close error')
        class Client:
            async def aclose(self):closed.append('client')
        class DB:
            def close(self):closed.append('db')
        b=m.Bridge.__new__(m.Bridge);b.adapter=Adapter();b.client=Client();b.db=DB()
        with self.assertRaises(RuntimeError):await b.run()
        self.assertEqual(closed,['adapter','client','db'])

if __name__=='__main__':unittest.main(verbosity=2)
