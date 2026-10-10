"""Workspace links remain directly accessible and old bookmarks still work."""
from html.parser import HTMLParser

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from web.routes import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize('path', ['/', '/records', '/progress', '/logs'])
def test_workspace_pages_link_to_distinct_assistant_and_records_urls(client, path):
    class NavigationParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'a' and 'nav-item' in attributes.get('class', '').split():
                self.links.append(attributes['href'])

    response = client.get(path)
    assert response.status_code == 200
    parser = NavigationParser()
    parser.feed(response.text)
    assert parser.links == ['/', '/records', '/progress', '/logs']


def test_legacy_records_bookmark_redirects_and_preserves_session(client):
    response = client.get('/?view=records&session_id=example', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/records?session_id=example'
    assert client.get(response.headers['location']).status_code == 200


def test_homepage_does_not_redirect_to_records(client):
    assert client.get('/', follow_redirects=False).status_code == 200
    assert client.get('/?session_id=example', follow_redirects=False).status_code == 200
