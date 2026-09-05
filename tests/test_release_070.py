import asyncio
import base64
import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meshcore_station.database import Database
from meshcore_station.main import create_app
from meshcore_station.service import StationService
from meshcore_station.transports.mock import MockTransport
from test_api import settings


def test_bootstrap_protects_http_ws_and_rotates_immediately(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        assert client.get('/api/backup').status_code == 401
        assert client.get('/').status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws'):
                pass
        token = (tmp_path / 'setup-token').read_text().strip()
        client.auth = ('meshcore', token)
        assert client.post('/api/setup', json={'transport': 'mock'}).status_code == 400
        assert client.post('/api/setup', json={'transport': 'mock', 'web_username': 'оператор', 'web_password': 'безопасный-пароль'}).status_code == 200
        assert not (tmp_path / 'setup-token').exists()
        assert client.get('/api/status').status_code == 401
        client.auth = ('оператор', 'безопасный-пароль')
        assert client.get('/api/status').status_code == 200
        assert client.get('/api/setup').json()['password_required'] is False
        # Saving again without a password must keep the newly saved one.
        assert client.post('/api/setup', json={'transport': 'mock', 'web_username': 'оператор'}).status_code == 200
        assert json.loads((tmp_path / 'station.json').read_text(encoding='utf-8'))['web_password'] == 'безопасный-пароль'


def test_setup_token_survives_restart(tmp_path):
    for _ in range(2):
        with TestClient(create_app(settings(tmp_path))) as client:
            token = (tmp_path / 'setup-token').read_text().strip()
            if _ == 0:
                first = token
            assert token == first
            assert client.get('/api/status', auth=('meshcore', token)).status_code == 200


def test_login_throttle_and_cross_origin(tmp_path):
    with TestClient(create_app(replace(settings(tmp_path), web_password='long-password'))) as client:
        for _ in range(10):
            assert client.get('/api/status', auth=('meshcore', 'wrong')).status_code == 401
        assert client.get('/api/status', auth=('meshcore', 'long-password')).status_code == 429
    with TestClient(create_app(replace(settings(tmp_path), web_password='long-password'))) as client:
        client.auth = ('meshcore', 'long-password')
        assert client.post('/api/advert', json={}, headers={'Origin': 'https://attacker.invalid'}).status_code == 403
        assert client.post('/api/advert', json={}, headers={'Origin': 'http://testserver'}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws', headers={'Origin': 'https://attacker.invalid'}):
                pass


def test_queue_cancel_retry_expiry_and_uncertainty(tmp_path):
    class Uncertain(MockTransport):
        async def send_message(self, *args):
            raise TimeoutError('ACK timeout')

    async def scenario():
        db = Database(tmp_path / 'queue.db')
        db.initialize()
        radio = Uncertain(seed=False)
        service = StationService(db, radio)
        queued = await service.send_message('channel', '0', 'Test')
        cancelled = await service.message_action(queued['id'], 'cancel')
        assert cancelled['status'] == 'cancelled'
        with pytest.raises(ValueError):
            await service.message_action(queued['id'], 'cancel')
        queued = await service.message_action(queued['id'], 'retry')
        assert queued['expires_at'] > time.time()
        radio._connected = True
        result = await service._deliver_message(queued)
        assert result['status'] == 'unconfirmed'
        assert db.queued_messages() == []
        assert (await service._deliver_message(result))['attempt_count'] == 1
        expired = db.add_message(target_type='channel', target_id='0', direction='out', text='Old', status='queued', expires_at=1)
        assert (await service._deliver_message(expired))['status'] == 'expired'
        assert db.get_message(expired['id'])['attempt_count'] == 0
        db.close()
    asyncio.run(scenario())


def test_cannot_cancel_active_send(tmp_path):
    class Slow(MockTransport):
        async def send_message(self, *args):
            started.set()
            await release.wait()
            return {'status': 'sent'}

    async def scenario():
        nonlocal started, release
        started, release = asyncio.Event(), asyncio.Event()
        db = Database(tmp_path / 'active.db')
        db.initialize()
        radio = Slow(seed=False)
        radio._connected = True
        service = StationService(db, radio)
        task = asyncio.create_task(service.send_message('channel', '0', 'Active'))
        await started.wait()
        with pytest.raises(ValueError):
            await service.message_action(1, 'cancel')
        release.set()
        assert (await task)['status'] == 'sent'
        db.close()
    started = release = None
    asyncio.run(scenario())


def test_backup_roundtrip_timeline_and_safe_queue(tmp_path):
    source = Database(tmp_path / 'source.db')
    source.initialize()
    message = source.add_message(target_type='channel', target_id='0', direction='out', text='Queued', status='queued')
    source.update_message(message['id'], 'sending', attempted=True)
    source.update_message(message['id'], 'queued', error='offline')
    source.set_setting('onboarding_complete', True)
    target = Database(tmp_path / 'target.db')
    target.initialize()
    exported = source.export_data()
    assert target.import_data(exported)['messages'] == 1
    restored = target.get_message(1)
    assert restored['status'] == 'cancelled'
    assert restored['attempt_count'] == 1
    assert len(restored['timeline']) == 4
    assert target.queued_messages() == []
    assert 'onboarding_complete' not in target.get_settings()
    assert target.import_data(exported)['messages'] == 0
    assert list((tmp_path / 'recovery').glob('*.db'))
    source.close()
    target.close()


def test_invalid_backup_never_partially_applies(tmp_path):
    db = Database(tmp_path / 'atomic.db')
    db.initialize()
    data = {'version': 1, 'contacts': [{'id': 'good', 'name': 'Good'}, {'id': 'bad', 'name': 'Bad', 'raw': {'x': 1}, 'last_seen': []}]}
    # The bad contact fails during import, after the first staged write.
    data['contacts'][1]['last_seen'] = {'invalid': True}
    with pytest.raises((ValueError, TypeError)):
        db.import_data(data)
    assert db.list_contacts() == []
    assert not (tmp_path / 'recovery').exists()
    db.close()


def test_retention_keeps_pending_messages_and_counts_rows(tmp_path):
    db = Database(tmp_path / 'retention.db')
    db.initialize()
    for status in ['queued', 'sending', 'unconfirmed', 'sent']:
        db.add_message(target_type='channel', target_id='0', direction='out', text=status, status=status, created_at=1)
    assert db.prune_history(1, 100, 60)['messages'] == 1
    assert len(db.list_messages('channel', '0')) == 3
    db.close()


def test_restore_preview_and_message_endpoints(tmp_path):
    app = create_app(replace(settings(tmp_path), web_password='long-password'))
    with TestClient(app) as client:
        client.auth = ('meshcore', 'long-password')
        assert client.post('/api/backup/preview', json={'data': {'version': 99}}).status_code == 400
        assert client.post('/api/backup/preview', json={'data': {'version': 1}}).json()['messages'] == 0
        app.state.station.transport._connected = False
        message = client.post('/api/messages', json={'target_type': 'channel', 'target_id': '0', 'text': 'pending', 'ttl_seconds': 900}).json()
        assert message['status'] == 'queued'
        assert client.post(f"/api/messages/{message['id']}/cancel").json()['status'] == 'cancelled'
        assert client.post(f"/api/messages/{message['id']}/retry").json()['status'] == 'queued'
        assert client.post('/api/messages/999/cancel').status_code == 404
        encrypted = client.post('/api/backup/encrypted', json={'password': 'backup-password'}).content
        damaged = bytearray(encrypted)
        damaged[-1] ^= 1
        assert client.post('/api/backup/restore-encrypted', json={'password': 'backup-password', 'payload_base64': base64.b64encode(damaged).decode()}).status_code == 400
