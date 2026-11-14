"""Isolated synthetic DOM fixture; never connects to gateway or user's browser."""
import ast
from pathlib import Path
import pytest
from camoufox.async_api import AsyncCamoufox

SOURCE=Path('/home/kasm-user/scripts/gateway.py')
HTML=next(ast.literal_eval(n.value) for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='HUMAN' for t in n.targets))
NATIVE='''<!doctype html><html class="noVNC_connected"><head></head><body>
<button id="noVNC_keyboard_button" onclick="document.querySelector('textarea').focus()">keyboard</button>
<textarea id="noVNC_keyboardinput"></textarea><select id="noVNC_setting_resize" onchange="document.body.dataset.resize=this.value"><option value="scale">scale</option><option value="off">off</option></select>
<button id="noVNC_clipboard_button" class="noVNC_button" onclick="document.getElementById('noVNC_clipboard').classList.toggle('noVNC_open')">clip</button><div id="noVNC_clipboard"><textarea></textarea></div>
</body></html>'''

@pytest.mark.asyncio
async def test_mobile_fixture():
 async with AsyncCamoufox(headless=True, os='linux', canvas_noise=False) as browser:
  page=await browser.new_page(viewport={'width':390,'height':844})
  state={'claim':200,'status':200,'viewer':200,'returned':False,'fail_return':False,'view_requests':0}
  async def route(r):
   path=r.request.url.split('https://mobile.test',1)[1].split('?',1)[0]
   if path=='/human':await r.fulfill(content_type='text/html',body=HTML)
   elif path=='/claim':await r.fulfill(status=state['claim'],json={'ok':True})
   elif path=='/human/status':await r.fulfill(status=403 if state['returned'] else state['status'],json={'session_id':'fixture','state':'human'})
   elif path=='/return':
    state['returned']=not state['fail_return']
    await r.fulfill(status=403 if state['fail_return'] else 200,json={'state':'returned'})
   elif path.startswith('/viewer/fixture/'):
    state['view_requests']+=1
    await r.fulfill(status=state['viewer'],content_type='text/html',body=NATIVE)
   else:await r.abort()
  await page.route('**/*',route)
  async def load(ticket=True):
   await page.goto('about:blank')
   await page.goto('https://mobile.test/human?session_id=fixture'+('#synthetic-ticket' if ticket else ''))
   await page.wait_for_timeout(200)
  await load()
  assert await page.evaluate('location.hash')==''
  await page.locator('#keyboard').click()
  assert await page.frames[1].evaluate('document.activeElement.id')=='noVNC_keyboardinput', (await page.locator('#status').inner_text(), await page.frames[1].content())
  await page.locator('#zoom').click()
  assert await page.frames[1].evaluate('document.body.dataset.resize')=='off'
  await page.locator('#fit').click()
  assert await page.frames[1].evaluate('document.body.dataset.resize')=='scale'
  await page.locator('#clipboard').click()
  assert await page.frames[1].locator('#noVNC_clipboard').evaluate("e=>e.classList.contains('noVNC_open')")
  assert await page.evaluate('document.documentElement.scrollWidth<=390')
  box=await page.locator('#viewer').bounding_box()
  assert box['height']>600 and box['y']<200
  nav=await page.locator('nav').bounding_box()
  assert nav['y']>=box['y']+box['height']-1 and abs(nav['y']+nav['height']-844)<2
  assert await page.locator('details, #help').count()==0
  for button in await page.locator('nav button').all():assert (await button.bounding_box())['height']>=44
  await page.set_viewport_size({'width':390,'height':430})
  assert (await page.locator('#viewer').bounding_box())['height']>200
  assert await page.locator('#back').count()==0
  state.update(returned=False,claim=403);before=state['view_requests'];await load()
  assert await page.evaluate('location.hash')=='#synthetic-ticket'
  assert state['view_requests']==before and await page.locator('iframe').count()==0
  state.update(claim=200,status=403);await load(False)
  assert state['view_requests']==before and await page.locator('#reconnect').is_disabled()
  state.update(status=200,viewer=403);await load(False)
  assert await page.locator('iframe').count()==0
  state.update(viewer=200);await load(False);state['status']=403
  await page.locator('iframe').wait_for(state='detached',timeout=8000)
  assert await page.locator('iframe').count()==0 and await page.locator('#keyboard').is_disabled()
  print('fixture checks: native keyboard/clipboard/fit/zoom, bottom controls, no manual return/help, claim failure, unauthorized viewer, status revocation')
