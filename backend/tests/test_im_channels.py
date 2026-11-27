from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.api.routers.im import (
    FeishuConfigRequest,
    cancel_feishu_qr,
    cancel_wechat_qr,
    create_code,
    delete_config,
    delete_wechat_config,
    poll_feishu_qr,
    poll_wechat_qr,
    save_config,
    start_feishu_qr,
    start_wechat_qr,
    status,
    unbind,
    wechat_status,
)
from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.im_models import (
    IMBinding,
    IMBindingCode,
    IMChannelConfig,
    IMChannelState,
    IMInbound,
    IMMessageContext,
    IMOutbound,
)
from app.domain.migration_models import MaintenanceLock
from app.domain.models import ChatSession, DurableJob, User, Workspace
from app.services import im_channels as im
from app.services.im_feishu import feishu_qr_manager, parse_event
from app.services.im_wechat import chunk_text, parse_message as parse_wechat_message, wechat_qr_manager


class TestIMChannels(TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        models = (
            User,
            Workspace,
            ChatSession,
            DurableJob,
            MaintenanceLock,
            IMChannelConfig,
            IMChannelState,
            IMBindingCode,
            IMBinding,
            IMInbound,
            IMMessageContext,
            IMOutbound,
        )
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

    def test_per_user_feishu_config_isolation_and_delivery(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                storage_root=Path(tmp),
                secret_provider='environment',
                master_key='test-master-key-for-im-channels',
                durable_queue_enabled=True,
            )
            ctx_a = self.context('a')
            ctx_b = self.context('b')

            with patch('app.api.routers.im.feishu_listener_manager.wake') as wake:
                st_a = status(self.db, ctx_a, settings)
                self.assertFalse(st_a['configured'])

                saved_a = save_config(FeishuConfigRequest(app_id='cli_user_a', app_secret='sec_a'), self.db, ctx_a, settings)
                self.assertTrue(saved_a['configured'])
                self.assertEqual(saved_a['config_source'], 'user')
                self.assertEqual(saved_a['app_id'], 'cli_user_a')
                self.assertEqual(wake.call_count, 1)

                # User B still sees unconfigured and cannot reuse User A's app_id
                self.assertFalse(status(self.db, ctx_b, settings)['configured'])
                with self.assertRaises(AppError) as conflict:
                    save_config(FeishuConfigRequest(app_id='cli_user_a', app_secret='sec_b'), self.db, ctx_b, settings)
                self.assertEqual(conflict.exception.status_code, 409)

                # User B configures their own bot
                saved_b = save_config(FeishuConfigRequest(app_id='cli_user_b', app_secret='sec_b'), self.db, ctx_b, settings)
                self.assertEqual(saved_b['app_id'], 'cli_user_b')

                # Binding & delivery use User A's decrypted credentials
                code_a = create_code(self.db, ctx_a, settings)['code']
                im.intake(self.db, 'feishu:cli_user_a', 'sender-a', 'm-a', f'/bind {code_a}')
                row_id = self.db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'm-a'))
                self.assertTrue(im.process(self.db, settings, row_id))
                with patch('app.services.im_feishu.send_text', return_value='rcpt-1') as send:
                    im.deliver(self.db, row_id, settings=settings)
                self.assertEqual(send.call_args.kwargs, {'app_id': 'cli_user_a', 'app_secret': 'sec_a'})

                # Deleting config removes binding and marks unconfigured
                deleted = delete_config(self.db, ctx_a, settings)
                self.assertFalse(deleted['configured'])
                self.assertFalse(deleted['bound'])

    def test_feishu_qr_registration_auto_binds_and_isolates_users(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                storage_root=Path(tmp),
                secret_provider='environment',
                master_key='test-master-key-for-im-channels',
                durable_queue_enabled=True,
            )
            ctx_a = self.context('a')
            ctx_b = self.context('b')

            responses = [
                {'supported_auth_methods': ['client_secret']},
                {
                    'device_code': 'dev-code-1',
                    'user_code': 'ABCD-1234',
                    'verification_uri_complete': 'https://open.feishu.cn/page/launcher?user_code=ABCD-1234',
                    'expires_in': 600,
                    'interval': 5,
                },
                {'error': 'authorization_pending'},
                {'error': 'slow_down'},
                {
                    'client_id': 'cli_qr_a',
                    'client_secret': 'sec_qr_a',
                    'user_info': {'open_id': 'ou_qr_a', 'tenant_brand': 'feishu'},
                },
            ]
            with (
                patch('app.services.im_feishu._post_registration', side_effect=responses),
                patch('app.api.routers.im.feishu_listener_manager.wake') as wake,
            ):
                started = start_feishu_qr(ctx_a, settings)
                sid = started['session_id']
                self.assertEqual(started['user_code'], 'ABCD-1234')
                self.assertIn('user_code=ABCD-1234', started['qr_url'])

                # Another user cannot poll user A's QR session
                self.assertEqual(poll_feishu_qr(sid, self.db, ctx_b, settings), {'status': 'expired'})

                pending = poll_feishu_qr(sid, self.db, ctx_a, settings)
                self.assertEqual(pending, {'status': 'pending', 'interval': 5})

                slowed = poll_feishu_qr(sid, self.db, ctx_a, settings)
                self.assertEqual(slowed, {'status': 'pending', 'interval': 10})

                confirmed = poll_feishu_qr(sid, self.db, ctx_a, settings)
                self.assertEqual(confirmed['status'], 'confirmed')
                self.assertTrue(confirmed['channel_status']['configured'])
                self.assertTrue(confirmed['channel_status']['bound'])
                self.assertEqual(confirmed['channel_status']['app_id'], 'cli_qr_a')
                self.assertEqual(wake.call_count, 1)

                # Session is consumed after confirmation
                self.assertEqual(poll_feishu_qr(sid, self.db, ctx_a, settings), {'status': 'expired'})
                cancel_feishu_qr(sid, ctx_a)

    def test_wechat_qr_login_inbound_context_delivery_and_session_expiry(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                storage_root=Path(tmp),
                secret_provider='environment',
                master_key='test-master-key-for-im-channels',
                durable_queue_enabled=True,
            )
            ctx_a = self.context('a')
            ctx_b = self.context('b')

            self.assertFalse(wechat_status(self.db, ctx_a, settings)['configured'])

            with (
                patch.object(
                    wechat_qr_manager,
                    'start',
                    return_value={'session_id': 'wx-sid-1', 'qr_url': 'https://liteapp.weixin.qq.com/q/test', 'expires_in': 300},
                ),
                patch.object(
                    wechat_qr_manager,
                    'get_status',
                    side_effect=[
                        {'status': 'wait'},
                        {'status': 'scanned'},
                        {
                            'status': 'confirmed',
                            'result': {
                                'bot_token': 'wx-token-1',
                                'bot_id': 'bot_a@im.bot',
                                'ilink_user_id': 'wx_user_a@im.wechat',
                                'base_url': 'https://ilinkai.weixin.qq.com',
                            },
                        },
                    ],
                ),
                patch.object(wechat_qr_manager, 'consume') as consume,
                patch('app.api.routers.im.wechat_listener_manager.wake') as wake,
            ):
                started = start_wechat_qr(ctx_a, settings)
                self.assertEqual(started['session_id'], 'wx-sid-1')
                self.assertEqual(poll_wechat_qr('wx-sid-1', self.db, ctx_a, settings), {'status': 'wait'})
                self.assertEqual(poll_wechat_qr('wx-sid-1', self.db, ctx_a, settings), {'status': 'scanned'})
                confirmed = poll_wechat_qr('wx-sid-1', self.db, ctx_a, settings)
                self.assertEqual(confirmed['status'], 'confirmed')
                self.assertTrue(confirmed['channel_status']['configured'])
                self.assertTrue(confirmed['channel_status']['bound'])
                self.assertEqual(confirmed['channel_status']['bot_id'], 'bot_a@im.bot')
                self.assertFalse(confirmed['channel_status']['session_expired'])
                consume.assert_called_once()
                self.assertEqual(wake.call_count, 1)

            # User B remains unconfigured
            self.assertFalse(wechat_status(self.db, ctx_b, settings)['configured'])

            # Inbound WeChat message persists context_token and delivers via im_wechat.send_text
            im.intake(
                self.db,
                'wechat:bot_a@im.bot',
                'wx_user_a@im.wechat',
                'wx-msg-1',
                '什么是快速排序？',
                context_token='ctx-tok-123',
            )
            row_id = self.db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'wx-msg-1'))
            with patch.object(im, '_answer', return_value='快速排序是一种分治算法。'):
                self.assertTrue(im.process(self.db, settings, row_id))
            with patch('app.services.im_wechat.send_text', return_value='wx-rcpt-1') as send_wx:
                im.deliver(self.db, row_id, settings=settings)
            self.assertEqual(send_wx.call_args.args, ('wx_user_a@im.wechat', '快速排序是一种分治算法。', row_id))
            self.assertEqual(
                send_wx.call_args.kwargs,
                {
                    'bot_token': 'wx-token-1',
                    'base_url': 'https://ilinkai.weixin.qq.com',
                    'context_token': 'ctx-tok-123',
                },
            )
            self.assertEqual(self.db.get(IMOutbound, row_id).status, 'sent')

            # Session expiration error marks IMChannelState.session_expired
            im.intake(
                self.db,
                'wechat:bot_a@im.bot',
                'wx_user_a@im.wechat',
                'wx-msg-2',
                '继续解释',
                context_token='ctx-tok-456',
            )
            row2_id = self.db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'wx-msg-2'))
            with patch.object(im, '_answer', return_value='第二段回复'):
                self.assertTrue(im.process(self.db, settings, row2_id))
            with patch('app.services.im_wechat.send_text', side_effect=AppError(422, 'wechat_session_expired', 'expired')):
                im.deliver(self.db, row2_id, settings=settings)
            self.assertEqual(self.db.get(IMOutbound, row2_id).status, 'failed')
            self.assertTrue(wechat_status(self.db, ctx_a, settings)['session_expired'])

            # Disconnect removes WeChat config and binding
            with patch('app.api.routers.im.wechat_listener_manager.wake') as wake_del:
                disconnected = delete_wechat_config(self.db, ctx_a, settings)
                self.assertFalse(disconnected['configured'])
                self.assertFalse(disconnected['bound'])
                self.assertEqual(wake_del.call_count, 1)
            cancel_wechat_qr('wx-sid-1', ctx_a)

    def test_wechat_message_parser_and_chunker(self):
        parsed = parse_wechat_message({
            'message_type': 1,
            'from_user_id': 'u1@im.wechat',
            'message_id': 98765,
            'context_token': 'ctx-1',
            'item_list': [{'type': 1, 'text_item': {'text': ' 你好 '}}, {'type': 2}],
        })
        self.assertEqual(parsed, ('u1@im.wechat', '98765', '你好', 'ctx-1'))
        # Bot echo message ignored
        self.assertIsNone(parse_wechat_message({
            'message_type': 2,
            'from_user_id': 'u1@im.wechat',
            'message_id': 98766,
            'item_list': [{'type': 1, 'text_item': {'text': 'hi'}}],
        }))
        chunks = chunk_text(('a' * 1000) + '\n\n' + ('b' * 1000), limit=1200)
        self.assertEqual(chunks, ['a' * 1000, 'b' * 1000])

    def test_rich_parsers_for_feishu_and_wechat_media(self):
        from app.services.im_feishu import parse_event_rich
        from app.services.im_wechat import _aes_ecb_decrypt, _aes_ecb_encrypt, parse_message_rich

        # Feishu image, file, audio, post
        sender = NS(sender_type='user', sender_id=NS(open_id='ou_1'))
        img_event = NS(event=NS(message=NS(chat_type='p2p', message_type='image', message_id='m1', content='{"image_key":"img_k1"}'), sender=sender))
        self.assertEqual(parse_event_rich(img_event)['media'][0]['file_key'], 'img_k1')

        file_event = NS(event=NS(message=NS(chat_type='p2p', message_type='file', message_id='m2', content='{"file_key":"fk_1","file_name":"notes.pdf"}'), sender=sender))
        parsed_file = parse_event_rich(file_event)
        self.assertEqual(parsed_file['media'][0]['kind'], 'file')
        self.assertEqual(parsed_file['media'][0]['filename'], 'notes.pdf')

        audio_event = NS(event=NS(message=NS(chat_type='p2p', message_type='audio', message_id='m3', content='{"file_key":"ak_1","duration":2400}'), sender=sender))
        parsed_audio = parse_event_rich(audio_event)
        self.assertEqual(parsed_audio['media'][0]['kind'], 'audio')
        self.assertEqual(parsed_audio['media'][0]['duration_ms'], 2400)

        post_event = NS(event=NS(message=NS(chat_type='p2p', message_type='post', message_id='m4', content='{"title":"标题","content":[[{"tag":"text","text":"正文"},{"tag":"img","image_key":"img_p1"}]]}'), sender=sender))
        parsed_post = parse_event_rich(post_event)
        self.assertIn('标题', parsed_post['text'])
        self.assertIn('正文', parsed_post['text'])
        self.assertEqual(parsed_post['media'][0]['file_key'], 'img_p1')

        # WeChat voice, image, file, and AES-128-ECB roundtrip
        wx_msg = {
            'message_type': 1,
            'from_user_id': 'u1@im.wechat',
            'message_id': 'wx-m1',
            'context_token': 'ctx-wx',
            'item_list': [
                {'type': 2, 'image_item': {'url': 'https://example.com/1.png'}},
                {'type': 3, 'voice_item': {'encode_type': 6, 'playtime': 1800, 'text': '你好世界', 'media': {'encrypt_query_param': 'enc1', 'aes_key': 'MDEyMzQ1Njc4OWFiY2RlZg=='}}},
                {'type': 4, 'file_item': {'file_name': 'report.docx', 'media': {'url': 'https://example.com/report.docx'}}},
            ],
        }
        parsed_wx = parse_message_rich(wx_msg)
        self.assertEqual(len(parsed_wx['media']), 3)
        self.assertEqual([m['kind'] for m in parsed_wx['media']], ['image', 'audio', 'file'])
        self.assertEqual(parsed_wx['media'][1]['wechat_asr_text'], '你好世界')
        self.assertEqual(parsed_wx['media'][2]['filename'], 'report.docx')

        key = b'0123456789abcdef'
        plaintext = b'hello wechat media payload'
        self.assertEqual(_aes_ecb_decrypt(_aes_ecb_encrypt(plaintext, key), key), plaintext)

    def test_inbound_file_asr_html_to_image_and_tts_delivery(self):
        import json
        import tempfile
        from pathlib import Path
        from app.api.routers.im import save_config
        from app.domain.models import AudioTranscription, FileRecord, FileTextChunk
        from app.providers.ports.tts import TTSAudioChunk
        from app.services.im_media import detect_tts_request, extract_html_blocks, render_html_to_png

        # 1. TTS intent detection & HTML extraction + PNG rendering
        tts_on, cleaned = detect_tts_request('/tts 解释一下牛顿第二定律')
        self.assertTrue(tts_on)
        self.assertEqual(cleaned, '解释一下牛顿第二定律')
        tts_nl, _ = detect_tts_request('请用语音回复我什么是量子纠缠')
        self.assertTrue(tts_nl)
        tts_off, _ = detect_tts_request('什么是勾股定理？')
        self.assertFalse(tts_off)

        cleaned_md, html_blocks = extract_html_blocks('结果如下：\n```html\n<h1>标题</h1><p>内容段落</p>\n```')
        self.assertEqual(len(html_blocks), 1)
        self.assertIn('HTML 内容已转为图片发送', cleaned_md)
        png_bytes = render_html_to_png(html_blocks[0])
        self.assertTrue(png_bytes.startswith(b'\x89PNG'))

        # 2. Full end-to-end IM flow: inbound audio (ASR) + file + HTML-to-image + TTS reply
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine('sqlite://')
            Base.metadata.create_all(engine)
            db = Session(engine, expire_on_commit=False)
            self.addCleanup(engine.dispose)
            self.addCleanup(db.close)
            db.add(User(id='a', tenant_id='t', username='a', username_normalized='a', password_hash='unused'))
            db.add(Workspace(id='a', tenant_id='t', owner_user_id='a', name='a'))
            db.commit()

            settings = Settings(
                storage_root=Path(tmp),
                secret_provider='environment',
                master_key='test-master-key-for-im-channels',
                durable_queue_enabled=True,
            )
            ctx_a = WorkspaceContext(Principal('a', 'a', 't', 'test'), db.get(Workspace, 'a'), frozenset({'workspace.read', 'workspace.write'}))

            with patch('app.api.routers.im.feishu_listener_manager.wake'):
                save_config(FeishuConfigRequest(app_id='cli_media_a', app_secret='sec_media_a'), db, ctx_a, settings)
            code = create_code(db, ctx_a, settings)['code']
            im.intake(db, 'feishu:cli_media_a', 'sender-a', 'bind-m', f'/bind {code}')
            bind_row = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'bind-m'))
            self.assertTrue(im.process(db, settings, bind_row))

            # Intake an inbound message with an audio attachment + text file + TTS request
            im.intake(
                db,
                'feishu:cli_media_a',
                'sender-a',
                'msg-media-1',
                '请用语音回复我并总结附件',
                media=[
                    {'kind': 'audio', 'source': 'feishu', 'message_id': 'msg-media-1', 'file_key': 'aud_1', 'resource_type': 'file', 'filename': 'voice.ogg', 'mime_type': 'audio/ogg'},
                    {'kind': 'file', 'source': 'feishu', 'message_id': 'msg-media-1', 'file_key': 'doc_1', 'resource_type': 'file', 'filename': 'note.txt', 'mime_type': 'text/plain'},
                ],
            )
            row_id = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'msg-media-1'))

            class _FakeTTS:
                async def stream(self, text: str, *, session_id=None):
                    yield TTSAudioChunk(audio=b'\x00\x01' * 4800, sample_rate=24000, channels=1)

                async def cancel(self, session_id=None):
                    pass

                async def close(self):
                    pass

            with (
                patch('app.services.im_feishu.download_message_resource', side_effect=[b'OggS-fake-audio', b'hello world notes']),
                patch('app.services.im_media.transcribe_im_audio', return_value='这是语音识别的文本'),
                patch.object(im, '_answer', return_value='总结完成：\n```html\n<h1>总结卡片</h1><p>第一点</p>\n```') as mock_ans,
                patch('app.services.im_media.tts_provider_for_workspace', return_value=_FakeTTS()),
            ):
                self.assertTrue(im.process(db, settings, row_id))
                self.assertIn('这是语音识别的文本', mock_ans.call_args.kwargs['prompt_text'])
                self.assertEqual(len(mock_ans.call_args.kwargs['file_ids']), 1)

            outbound = db.get(IMOutbound, row_id)
            outbound_media = json.loads(outbound.media_json)
            self.assertEqual([m['kind'] for m in outbound_media], ['image', 'audio'])

            with (
                patch('app.services.im_feishu.send_text', return_value='rcpt-txt') as s_txt,
                patch('app.services.im_feishu.send_image', return_value='rcpt-img') as s_img,
                patch('app.services.im_feishu.send_audio', return_value='rcpt-aud') as s_aud,
            ):
                im.deliver(db, row_id, settings=settings)
                self.assertEqual(s_txt.call_count, 1)
                self.assertEqual(s_img.call_count, 1)
                self.assertEqual(s_aud.call_count, 1)
            self.assertEqual(db.get(IMOutbound, row_id).status, 'sent')

    def test_wechat_quotes_emojis_and_text_model_switching(self):
        from app.domain.models import ProviderConfig
        from app.services.im_feishu import parse_event_rich
        from app.services.im_wechat import parse_message_rich

        # 1. WeChat quote (ref_msg) with nested image + emoji_item + link_item
        wx_quote_msg = {
            'message_type': 1,
            'from_user_id': 'u2@im.wechat',
            'message_id': 'wx-quote-1',
            'context_token': 'ctx-q',
            'item_list': [
                {
                    'type': 1,
                    'text_item': {
                        'text': '这个公式怎么推导？[微笑]',
                        'ref_msg': {
                            'title': '上一条公式',
                            'message_item': {
                                'type': 2,
                                'image_item': {'url': 'https://example.com/formula.png'},
                            },
                        },
                    },
                },
                {'type': 6, 'emoji_item': {'desc': '大笑', 'url': 'https://example.com/laugh.png'}},
                {'type': 99, 'link_item': {'title': '微积分讲义', 'url': 'https://example.com/calc'}},
            ],
        }
        parsed = parse_message_rich(wx_quote_msg)
        self.assertIsNotNone(parsed)
        self.assertIn('[引用消息: 上一条公式]', parsed['text'])
        self.assertIn('这个公式怎么推导？[微笑]', parsed['text'])
        self.assertIn('[表情: 大笑]', parsed['text'])
        self.assertIn('[链接卡片: 微积分讲义 - https://example.com/calc]', parsed['text'])
        self.assertEqual(len(parsed['media']), 2)
        self.assertEqual([m['kind'] for m in parsed['media']], ['image', 'image'])

        # Feishu sticker + post emotion
        sender = NS(sender_type='user', sender_id=NS(open_id='ou_2'))
        sticker_ev = NS(event=NS(message=NS(chat_type='p2p', message_type='sticker', message_id='fs-s1', content='{"file_key":"stk_1"}'), sender=sender))
        self.assertEqual(parse_event_rich(sticker_ev)['text'], '[表情]')
        post_ev = NS(event=NS(message=NS(chat_type='p2p', message_type='post', message_id='fs-p2', content='{"content":[[{"tag":"emotion","emoji_type":"SMILE"},{"tag":"text","text":"你好"}]]}'), sender=sender))
        self.assertIn('[表情: SMILE]', parse_event_rich(post_ev)['text'])

        # 2. Text-based model listing & switching
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        db = Session(engine, expire_on_commit=False)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        db.add(User(id='a', tenant_id='t', username='a', username_normalized='a', password_hash='unused'))
        db.add(Workspace(id='a', tenant_id='t', owner_user_id='a', name='a'))
        db.add(ProviderConfig(
            id='prov-ds',
            workspace_id='a',
            provider_type='deepseek_chat',
            display_name='DeepSeek',
            base_url='https://api.deepseek.com',
            enabled=True,
            remote_capability=True,
            capabilities={'default_model': 'deepseek-chat', 'discovered_model_ids': ['deepseek-chat', 'deepseek-reasoner']},
        ))
        db.add(ProviderConfig(
            id='prov-qwen',
            workspace_id='a',
            provider_type='qwen',
            display_name='通义千问',
            base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
            enabled=True,
            remote_capability=True,
            capabilities={'default_model': 'qwen-plus', 'discovered_model_ids': ['qwen-plus', 'qwen-max']},
        ))
        db.commit()

        settings = Settings(secret_provider='environment', master_key='test-master-key-for-im-channels', durable_queue_enabled=True)
        ctx_a = WorkspaceContext(Principal('a', 'a', 't', 'test'), db.get(Workspace, 'a'), frozenset({'workspace.read', 'workspace.write'}))
        from app.api.routers.im import save_config
        with patch('app.api.routers.im.feishu_listener_manager.wake'):
            save_config(FeishuConfigRequest(app_id='app', app_secret='sec'), db, ctx_a, settings)
        code = create_code(db, ctx_a, settings)['code']
        im.intake(db, 'feishu:app', 'sender-a', 'b1', f'/bind {code}')
        b_row = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'b1'))
        self.assertTrue(im.process(db, settings, b_row))

        # List models
        im.intake(db, 'feishu:app', 'sender-a', 'm-list', '/models')
        list_id = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'm-list'))
        self.assertTrue(im.process(db, settings, list_id))
        list_out = db.get(IMOutbound, list_id).text
        self.assertIn('deepseek-chat', list_out)
        self.assertIn('qwen-plus', list_out)

        # Switch via natural language
        im.intake(db, 'feishu:app', 'sender-a', 'm-sw', '切换模型 qwen-max')
        sw_id = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'm-sw'))
        self.assertTrue(im.process(db, settings, sw_id))
        self.assertIn('已切换模型为：qwen-max', db.get(IMOutbound, sw_id).text)
        binding = db.scalar(select(IMBinding).where(IMBinding.sender == 'sender-a'))
        self.assertEqual(binding.preferred_provider_id, 'prov-qwen')
        self.assertEqual(binding.preferred_model_id, 'qwen-max')

        # Subsequent _answer uses the switched model
        from app.domain.models import ChatSession

        class _FakeService:
            def __init__(self):
                self.reqs = []

            def create_session(self, *args, **kwargs):
                cs = ChatSession(id='sess-1', workspace_id='a', title='飞书 · 学习助手', session_kind='standalone', memory_enabled=False, status='active')
                db.add(cs)
                db.commit()
                return cs

            def create_stream(self, session_id, req, **kwargs):
                self.reqs.append(req)
                return iter(())

        fake_svc = _FakeService()
        im.intake(db, 'feishu:app', 'sender-a', 'm-ask', '讲一下傅里叶变换')
        ask_id = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'm-ask'))
        with patch('app.services.chat_service_factory.build_chat_service', return_value=fake_svc):
            self.assertTrue(im.process(db, settings, ask_id))
        self.assertEqual(fake_svc.reqs[0].model_id, 'qwen-max')
        self.assertEqual(fake_svc.reqs[0].provider_id, 'prov-qwen')

        # Reset to default
        im.intake(db, 'feishu:app', 'sender-a', 'm-rst', '/model default')
        rst_id = db.scalar(select(IMInbound.id).where(IMInbound.message_id == 'm-rst'))
        self.assertTrue(im.process(db, settings, rst_id))
        db.refresh(binding)
        self.assertIsNone(binding.preferred_provider_id)
        self.assertIsNone(binding.preferred_model_id)

    def test_feishu_qr_start_surfaces_registration_error_payload(self):
        settings = Settings(durable_queue_enabled=True)
        ctx_a = self.context('a')
        with patch(
            'app.services.im_feishu._post_registration',
            return_value={'error': 'rate_limited', 'error_description': 'Too many requests'},
        ):
            with self.assertRaises(AppError) as err:
                start_feishu_qr(ctx_a, settings)
        self.assertEqual(err.exception.status_code, 503)
        self.assertEqual(err.exception.code, 'feishu_qr_failed')
        self.assertIn('Too many requests', err.exception.message)



