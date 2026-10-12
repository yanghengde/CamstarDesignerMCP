"""Refreshing a receipt cannot make an earlier design look like a new result."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_background_refresh_keeps_old_result_before_latest_user_turn():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the browser function regression')
    source = (Path(__file__).parents[1] / 'static' / 'index.html').read_text(encoding='utf-8')
    render = source.split('        function showDesignerResult(result) {', 1)[1].split(
        '        function renderReviewReceipt(', 1)[0]
    refresh = source.split('        async function refreshBackground() {', 1)[1].split(
        '        function renderSessions()', 1)[0]
    script = r"""
const assert = require('node:assert/strict');
class Element {
    constructor() { this.dataset = {}; this.children = []; }
    appendChild(child) { this.children.push(child); }
    setAttribute() {}
    addEventListener() {}
    remove() { messagesDiv.children.splice(messagesDiv.children.indexOf(this), 1); }
    replaceWith(card) { messagesDiv.children[messagesDiv.children.indexOf(this)] = card; }
}
const document = {createElement: () => new Element()};
const typingIndicator = new Element();
const messagesDiv = new Element();
messagesDiv.children = [typingIndicator];
messagesDiv.querySelector = () => messagesDiv.children.find(node => node.className === 'designer-result');
messagesDiv.insertBefore = (card, target) => messagesDiv.children.splice(messagesDiv.children.indexOf(target), 0, card);
let scrolls = 0;
const scrollToBottom = () => scrolls++;
const updateControls = () => {};
const monitorBackground = () => {};
const showNotice = () => {};
const currentUsername = 'user', currentSessionId = 'session';
let receipt = {id: 'old', objects: ['AssetTrace_6381'], workspace: 200};
const fetchJson = async () => ({designer_results: [receipt]});
""" + 'function showDesignerResult(result) {' + render + 'async function refreshBackground() {' + refresh + r"""
(async () => {
    showDesignerResult(receipt);
    const userTurn = new Element(), preview = new Element();
    messagesDiv.insertBefore(userTurn, typingIndicator);
    messagesDiv.insertBefore(preview, typingIndicator);
    receipt = {...receipt, workspace: 300};
    await refreshBackground();
    assert.equal(messagesDiv.children[0].dataset.packageId, 'old');
    assert.equal(messagesDiv.children[0].children[1].textContent, '对象：AssetTrace_6381 · 客户工作区：300');
    assert.equal(messagesDiv.children[1], userTurn);
    assert.equal(messagesDiv.children[2], preview);
    assert.equal(scrolls, 1);
    receipt = {...receipt, id: 'new', objects: ['AssetTrace_6382']};
    await refreshBackground();
    assert.equal(messagesDiv.children[0], userTurn);
    assert.equal(messagesDiv.children[1], preview);
    assert.equal(messagesDiv.children[2].dataset.packageId, 'new');
    assert.equal(scrolls, 2);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8',
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
