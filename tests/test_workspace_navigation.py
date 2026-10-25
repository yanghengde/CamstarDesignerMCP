"""Navigation icons and page initialization follow the actual HTML templates."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

STATIC = Path(__file__).parents[1] / 'static'


def run_js(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for browser function checks')
    result = subprocess.run([node, '-'], input=script, text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


class Selectors(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.selectors = set()
        self.ids = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.selectors.add(tag)
        self.selectors.update('.' + name for name in attrs.get('class', '').split())
        if attrs.get('id'):
            self.ids.add(attrs['id'])


def test_clf_welcome_initialization_uses_existing_template_elements():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    template = html.split('<template id="welcomeTemplate">', 1)[1].split('</template>', 1)[0]
    selectors = Selectors(template).selectors
    source = (STATIC / 'clf-workspace.js').read_text(encoding='utf-8')
    configure = source.split("        const welcome=document.getElementById('welcomeTemplate').content;", 1)[1].split("        const panel=node('aside'", 1)[0]
    run_js("const assert=require('node:assert/strict');\nconst elements=Object.fromEntries(" + json.dumps(sorted(selectors)) + ".map(key=>[key,{textContent:''}]));\nconst welcome={querySelector:selector=>elements[selector]||null,querySelectorAll:()=>[]};\n" + configure + "\nassert.match(elements.h2.textContent,/CLF/);assert.ok(elements['.welcome-description'].textContent);\n")


@pytest.mark.parametrize('page', ['index.html', 'logs.html', 'progress.html', 'testing.html', 'wcf-test.html'])
def test_workspace_menu_icons_resolve_and_distinguish_functions(page):
    html = (STATIC / page).read_text(encoding='utf-8')
    nav = re.search(r'<nav class="nav".*?</nav>', html, re.S).group()
    targets = re.findall(r'<use href="/svg/navigation.svg#([\w-]+)"', nav)
    symbols = {item.attrib['id'] for item in ET.parse(STATIC / 'svg/navigation.svg').getroot()}
    assert len(targets) == 7 and len(set(targets)) == 7
    assert set(targets) <= symbols


def test_wcf_bootstrap_failure_can_be_retried_without_reloading_page():
    ids = Selectors((STATIC / 'wcf-test.html').read_text(encoding='utf-8')).ids
    source = (STATIC / 'wcf-simple.js').read_text(encoding='utf-8')
    run_js(r"""
const assert=require('node:assert/strict');
class Element {
  constructor(){this.value='';this.children=[];this.dataset={};this.classList={add(){},toggle(){}};}
  replaceChildren(...items){this.children=items;}
  append(item){this.children.push(item);}
  get firstChild(){return this.children[0];}
  setAttribute(){}
}
const elements=Object.fromEntries(IDS.map(id=>[id,new Element()]));
const document={getElementById:id=>elements[id],createElement:()=>new Element(),querySelectorAll:()=>[]};
const sessionStorage={getItem:()=>null,removeItem(){}};
let attempts=0;
const fetch=async path=>{
  if(path==='/config')return {ok:true,json:async()=>({username:'user'})};
  assert.match(path,/simple\/bootstrap/);
  if(++attempts===1)return {ok:false,status:409,json:async()=>({detail:'系统默认连接尚未关联工作项目'})};
  return {ok:true,json:async()=>({services:[],history:[],write_target_confirmed:false})};
};
""".replace('IDS', json.dumps(sorted(ids))) + source + r"""
(async()=>{
  await new Promise(setImmediate);
  assert.equal(elements.connection.textContent,'配置加载失败');
  assert.match(elements.notice.textContent,/关联工作项目/);
  assert.equal(elements.refreshButton.disabled,false);
  await elements.refreshButton.onclick();
  assert.equal(attempts,2);
  assert.equal(elements.connection.textContent,'默认配置已载入');
  assert.equal(elements.refreshButton.disabled,false);
  assert.equal(elements.notice.hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
""")
