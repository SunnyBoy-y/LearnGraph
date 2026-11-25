from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.api.routers.im import unbind
from app.core.database import Base
from app.core.security import Principal
from app.domain.im_models import IMBinding, IMBindingCode, IMInbound, IMOutbound
from app.domain.migration_models import MaintenanceLock
from app.domain.models import ChatSession, DurableJob, User, Workspace
from app.services import im_channels as im
from app.services.im_feishu import parse_event


class TestIMChannels(TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models = (User, Workspace, ChatSession, DurableJob, MaintenanceLock,
                  IMBindingCode, IMBinding, IMInbound, IMOutbound)
        Base.metadata.create_all(self.engine, tables=[m.__table__ for m in models])
        self.db = Session(self.engine, expire_on_commit=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.conn = 'feishu:test'
        for uid in ('a', 'b'):
            self.db.add(User(id=uid, tenant_id='t', username=uid, username_normalized=uid, password_hash='unused'))
            self.db.add(Workspace(id=uid, tenant_id='t', owner_user_id=uid, name=uid))
        self.db.commit()

    def context(self, uid):
        return WorkspaceContext(Principal(uid, uid, 't', 'test'), self.db.get(Workspace, uid), frozenset())

    def test_one_time_binding_rejects_cross_user_rebind_and_duplicates(self):
        a, b = (im.issue_code(self.db, self.context(uid), self.conn) for uid in ('a', 'b'))
        im.intake(self.db, self.conn, 'sender-a', 'bind-a', f'/bind {a}')
        im.intake(self.db, self.conn, 'sender-a', 'bind-a', f'/bind {a}')
        im.intake(self.db, self.conn, 'sender-b', 'replay-a', f'/bind {a}')
        im.intake(self.db, self.conn, 'sender-a', 'steal-b', f'/bind {b}')
        im.intake(self.db, self.conn, 'sender-b', 'bind-b', f'/bind {b}')
        im.intake(self.db, self.conn, 'sender-a', 'normal-a', 'question')
        rows = {r.message_id: (r.workspace_id, r.text) for r in self.db.scalars(select(IMInbound))}
        self.assertEqual(rows, {'bind-a': ('a', ''), 'bind-b': ('b', ''), 'normal-a': ('a', 'question')})
        self.assertEqual(len(list(self.db.scalars(select(DurableJob)))), 3)

    def test_durable_delivery_retry_and_revocation(self):
        code = im.issue_code(self.db, self.context('a'), self.conn)
        im.intake(self.db, self.conn, 'sender-a', 'bind', f'/bind {code}')
        row_id = self.db.scalar(select(IMInbound.id))
        with patch.object(im, 'feishu_connection', return_value=self.conn):
            self.assertTrue(im.process(self.db, None, row_id))
            self.assertTrue(im.process(self.db, None, row_id))
            with patch('app.services.im_feishu.send_text', side_effect=im.AppError(503, 'temporarily_down', 'try again')) as send:
                with self.assertRaises(im.AppError):
                    im.deliver(self.db, row_id)
                self.assertEqual(send.call_args.args[2], row_id)
                self.assertEqual(self.db.get(IMOutbound, row_id).status, 'sending')
                unbind(self.db, self.context('a'))
                im.deliver(self.db, row_id)
                self.assertEqual(send.call_count, 1)
        self.assertEqual(self.db.get(IMOutbound, row_id).status, 'cancelled')

    def test_send_retry_does_not_generate_again(self):
        code = im.issue_code(self.db, self.context('a'), self.conn)
        im.intake(self.db, self.conn, 'sender-a', 'bind', f'/bind {code}')
        im.intake(self.db, self.conn, 'sender-a', 'question', 'explain')
        with Session(self.engine, expire_on_commit=False) as worker:
            first = worker.scalar(select(IMInbound.id).where(IMInbound.message_id == 'bind'))
            second = worker.scalar(select(IMInbound.id).where(IMInbound.message_id == 'question'))
            with patch.object(im, 'feishu_connection', return_value=self.conn), patch.object(im, '_answer', return_value='answer') as generate:
                self.assertFalse(im.process(worker, None, second))
                self.assertTrue(im.process(worker, None, first))
                self.assertTrue(im.process(worker, None, second))
                with patch('app.services.im_feishu.send_text', side_effect=[im.AppError(503, 'temporarily_down', 'try again'), 'receipt']) as send:
                    with self.assertRaises(im.AppError):
                        im.deliver(worker, second)
                    im.deliver(worker, second)
                    self.assertEqual([c.args for c in send.call_args_list], [('sender-a', 'answer', second)] * 2)
                self.assertTrue(im.process(worker, None, second))
                generate.assert_called_once()
                self.assertEqual(worker.get(IMOutbound, second).status, 'sent')

    def test_feishu_filters_groups_and_non_text(self):
        msg = NS(chat_type='p2p', message_type='text', message_id='mid', content='{"text":" 问题 "}')
        data = NS(event=NS(message=msg, sender=NS(sender_type='user', sender_id=NS(open_id='sid'))))
        self.assertEqual(parse_event(data), ('sid', 'mid', '问题'))
        msg.chat_type = 'group'
        self.assertIsNone(parse_event(data))
        msg.chat_type, msg.message_type = 'p2p', 'image'
        self.assertIsNone(parse_event(data))
