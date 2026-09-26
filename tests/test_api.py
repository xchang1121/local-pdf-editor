from pathlib import Path
from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from app.main import app


@pytest.fixture
def client():
    with TestClient(app, base_url='http://127.0.0.1') as c:yield c


def session(client):
    r=client.get('/api/session');assert r.status_code==200
    return {'X-Session-Token':r.json()['token']}


def upload(client,headers):
    raw=(Path(__file__).parents[1]/'examples/demo.pdf').read_bytes()
    r=client.post('/api/import',headers=headers,files={'file':('中文.pdf',raw,'application/pdf')})
    assert r.status_code==200,r.text
    return r.json()


def test_index_and_security_headers(client):
    r=client.get('/')
    assert r.status_code==200 and '页间' in r.text
    assert r.headers['x-frame-options']=='DENY'
    assert "object-src 'none'" in r.headers['content-security-policy']
    assert client.get('/static/app.js').status_code==200
    assert client.get('/demo.pdf').content.startswith(b'%PDF')


def test_auth_isolation_and_session_clear(client):
    first=session(client);second=session(client)
    imported=upload(client,first)
    page={'id':'p','source':imported['source'],'index':0}
    assert client.post('/api/preview',json={'page':page}).status_code==401
    assert client.post('/api/preview',headers=second,json={'page':page}).status_code==404
    assert client.delete('/api/session',headers=first).status_code==200
    assert client.post('/api/preview',headers=first,json={'page':page}).status_code==401


def test_full_import_preview_export(client):
    headers=session(client);data=upload(client,headers)
    assert len(data['pages'])==3
    p={'id':'a','source':data['source'],'index':0,'ops':[{'kind':'text','rect':[50,700,500,750],'text':'API 测试新增'}]}
    r=client.post('/api/preview',headers=headers,json={'page':p,'scale':.4})
    assert r.status_code==200,r.text
    assert any('API' in b['text'] for b in r.json()['blocks'])
    p2={'id':'b','source':data['source'],'index':2}
    r=client.post('/api/export',headers=headers,json={'pages':[p2,p],'filename':'已修改.pdf'})
    assert r.status_code==200,r.text
    assert r.headers['content-type']=='application/pdf'
    assert "filename*=UTF-8''" in r.headers['content-disposition']
    with fitz.open(stream=r.content,filetype='pdf') as d:
        assert len(d)==2 and not d[0].get_text().strip() and 'API' in d[1].get_text()
    status=client.get('/api/session',headers=headers).json()
    assert status['resumed'] is True and data['source'] in status['sources']


def test_origin_host_and_invalid_upload(client):
    headers=session(client)
    assert client.post('/api/export',headers={**headers,'Origin':'https://evil.invalid'},json={}).status_code==403
    assert client.get('/',headers={'Host':'evil.invalid'}).status_code==400
    r=client.post('/api/import',headers=headers,files={'file':('bad.pdf',b'nope','application/pdf')})
    assert r.status_code==422
    assert client.post('/api/export',headers=headers,json={'pages':[]}).status_code==422


def test_declared_oversize_request(client):
    headers=session(client)
    r=client.post('/api/import',headers={**headers,'Content-Length':str(70*1024*1024)},content=b'x')
    assert r.status_code==413


def test_session_expiration_creates_new_token(client):
    from app.main import SESSION_TTL, prune_sessions
    import time
    headers = session(client)
    old = headers['X-Session-Token']
    app.state.sessions[old].touched = time.monotonic() - SESSION_TTL - 1
    prune_sessions(app.state.sessions)
    assert old not in app.state.sessions
    status = client.get('/api/session', headers=headers).json()
    assert status['resumed'] is False and status['token'] != old
    assert client.post('/api/preview', headers=headers, json={'page':{'id':'p'}}).status_code == 401
