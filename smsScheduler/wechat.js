// WeChat 4.x for macOS. Called by wechat.py; never types via global keystrokes.
ObjC.import('Cocoa');
ObjC.import('ApplicationServices');
ObjC.bindFunction('CGPreflightPostEventAccess', ['bool', []]);

var phase = 'startup';
function run(args) {
  try { return execute(args); }
  catch (err) { throw Error('WeChat ' + phase + ': ' + err.message); }
}

function execute(args) {
  phase = 'startup';
  var mode = args[0];
  if (['list', 'check', 'send'].indexOf(mode) < 0) throw Error('Invalid WeChat operation');
  var se = Application('System Events');
  var p = se.processes.byName('WeChat');
  if (!p.exists()) throw Error('Open WeChat and sign in first.');
  var session = ObjC.deepUnwrap($.CGSessionCopyCurrentDictionary());
  if (!session || session.CGSSessionScreenIsLocked || session.kCGSessionOnConsoleKey === false)
    throw Error('WeChat needs an unlocked, active Mac session.');

  function attr(e, key) { try { return e.attributes.byName(key).value(); } catch (_) { return null; } }
  function children(e, role) { return e.uiElements().filter(function(x) { return x.role() === role; }); }
  function one(items, label) {
    if (items.length !== 1) throw Error('Cannot uniquely identify ' + label + '. Open WeChat’s main Chats window.');
    return items[0];
  }
  function layout() {
    // Settings, floating chats, and helper windows can share the same AX subrole.
    // Identify the main chat panel by its sidebar rows instead of counting windows.
    var found = [];
    function scan(node, win, depth) {
      if (depth > 8) return;
      var nodes;
      try { nodes = node.uiElements(); } catch (_) { return; }
      var lists = nodes.filter(function(e) { return e.role() === 'AXList'; });
      var groups = nodes.filter(function(e) { return e.role() === 'AXGroup'; });
      if (groups.length === 1) {
        for (var n = 0; n < lists.length; n++) {
          var candidate = {win: win, main: node, list: lists[n], content: groups[0]};
          if (rows(candidate).length) { found.push(candidate); return; }
        }
      }
      nodes.forEach(function(e) {
        if (['AXGroup', 'AXSplitGroup'].indexOf(e.role()) >= 0) scan(e, win, depth + 1);
      });
    }
    p.windows().forEach(function(win) {
      try { scan(win, win, 0); } catch (_) { /* The window may be opening or closing. */ }
    });
    if (!found.length) throw Error('The main conversation list is not ready. Open WeChat, sign in, and select Chats with at least one conversation in the sidebar.');
    if (found.length !== 1) throw Error('More than one conversation list is open. Close extra WeChat chat windows and try again.');
    return found[0];
  }
  function waitForLayout() {
    phase = 'find main chat window';
    var last;
    for (var n = 0; n < 10; n++) {
      try { return layout(); } catch (err) { last = err; }
      if (n < 9) delay(0.4);
    }
    throw last;
  }
  function rows(l) {
    return l.list.uiElements().filter(function(e) { return String(attr(e, 'AXIdentifier') || '').indexOf('session_item_') === 0; });
  }
  function collect(e, test, result) {
    if (test(e)) result.push(e);
    // Do not traverse message history, sidebar rows, or large nested lists.
    if (['AXList', 'AXTable', 'AXOutline', 'AXButton', 'AXTextArea', 'AXStaticText'].indexOf(e.role()) >= 0) return;
    e.uiElements().forEach(function(c) { collect(c, test, result); });
  }
  function controls(l) {
    var headers = [], editors = [], buttons = [];
    collect(l.content, function(e) {
      if (attr(e, 'AXIdentifier') === 'big_title_line_h_view') headers.push(e);
      if (e.role() === 'AXTextArea') editors.push(e);
      if (e.role() === 'AXButton' && ['Send', '发送', '傳送', '發送'].indexOf(attr(e, 'AXTitle')) >= 0) buttons.push(e);
      return false;
    }, []);
    return {header: one(headers, 'conversation header'), editor: one(editors, 'message composer'),
      send: one(buttons, 'Send button')};
  }
  function foreground() {
    if (!p.frontmost()) throw Error('WeChat lost focus; no automatic retry.');
    var current = ObjC.deepUnwrap($.CGSessionCopyCurrentDictionary());
    if (!current || current.CGSSessionScreenIsLocked || current.kCGSessionOnConsoleKey === false)
      throw Error('The screen was locked during the operation.');
  }
  function click(e, win, viewport) {
    foreground();
    if (!$.CGPreflightPostEventAccess())
      throw Error('macOS allows reading WeChat but blocks mouse control. Enable Accessibility for osascript (add /usr/bin/osascript using the + button), then rerun Check access.');
    var pos = e.position(), size = e.size(), wp = win.position(), ws = win.size();
    var left = Math.max(pos[0], wp[0]), top = Math.max(pos[1], wp[1]);
    var right = Math.min(pos[0] + size[0], wp[0] + ws[0]), bottom = Math.min(pos[1] + size[1], wp[1] + ws[1]);
    if (viewport) {
      var vp = viewport.position(), vs = viewport.size();
      left = Math.max(left, vp[0]); top = Math.max(top, vp[1]);
      right = Math.min(right, vp[0] + vs[0]); bottom = Math.min(bottom, vp[1] + vs[1]);
    }
    if (right - left < 4 || bottom - top < 4)
      throw Error('The conversation/control is off-screen. Keep the target chat visible in WeChat’s sidebar.');
    var x = Math.round((left + right) / 2), y = Math.round((top + bottom) / 2);
    var point = $.CGPointMake(x, y);
    // Deliver clicks specifically to WeChat rather than to another desktop app.
    $.CGEventPostToPid(p.unixId(), $.CGEventCreateMouseEvent(null, 1, point, 0));
    delay(0.08);
    $.CGEventPostToPid(p.unixId(), $.CGEventCreateMouseEvent(null, 2, point, 0));
  }
  var l = waitForLayout();
  if (mode === 'list') {
    var names = rows(l).map(function(r) { return String(attr(r, 'AXIdentifier')).slice(13); });
    // Duplicate display names cannot identify a recipient reliably.
    return JSON.stringify(names.filter(function(n, i) { return n && names.indexOf(n) === i && names.lastIndexOf(n) === i; }));
  }
  var target = args[1];
  if (!target) throw Error('Choose a WeChat conversation first.');
  Application('com.tencent.xinWeChat').activate();
  p.frontmost = true;
  // Raising the identified window avoids leaving an unrelated WeChat window on top.
  try { l.win.attributes.byName('AXMinimized').value = false; } catch (_) {}
  try { l.win.actions.byName('AXRaise').perform(); } catch (_) {}
  delay(1);
  l = waitForLayout();
  phase = 'select conversation';
  if (l.win.sheets().length) throw Error('Dismiss the dialog in WeChat first.');
  function selectTarget() {
    l = layout();
    if (l.win.sheets().length) throw Error('Dismiss the dialog in WeChat first.');
    var matching = rows(l).filter(function(r) { return attr(r, 'AXIdentifier') === 'session_item_' + target; });
    var row = one(matching, 'target conversation (use a unique remark name and keep it visible in the sidebar)');
    click(row, l.win, l.list);
  }
  selectTarget();
  var c = null;
  var selectionError = null;
  phase = 'wait for selected conversation';
  for (var i = 0; i < 10; i++) {
    delay(0.3);
    // A first click can be consumed by window activation. Reselect only before
    // entering any message, with fresh recipient/visibility checks each time.
    if (i === 3 || i === 6) selectTarget();
    try { c = controls(layout()); }
    catch (err) { selectionError = err; c = null; continue; }
    if (c.header.value() === target) break;
  }
  if (!c) throw Error('The selected chat did not open. Bring WeChat’s main window fully to the front, keep its sidebar unobstructed, and retry Check access. No message was entered. Details: ' + selectionError.message);
  function verify(requireSend) {
    phase = 'verify selected chat';
    foreground();
    if (c.header.value() !== target) throw Error('Selected conversation does not exactly match the scheduled recipient.');
    phase = 'verify composer';
    if (!c.editor.enabled() || (requireSend && !c.send.enabled())) throw Error('The message composer is unavailable. Check that WeChat is signed in.');
  }
  verify();
  phase = 'check existing draft';
  if (c.editor.value()) throw Error('This conversation already has a draft. Clear or send it yourself before scheduling.');
  if (mode === 'check') return 'Ready: exact conversation verified; composer empty. Nothing sent.';

  // Message is read from stdin, never interpolated into a script or shell command.
  var input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  var message = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding));
  if (!message || !message.trim()) throw Error('Message cannot be empty.');
  var clicked = false;
  try {
    c.editor.value = message;
    delay(0.2);
    verify(true);
    if (c.editor.value() !== message) throw Error('WeChat did not accept the complete message.');
    // Refresh coordinates and check the header immediately before clicking Send.
    l = layout();
    c = controls(l);
    verify(true);
    if (c.editor.value() !== message) throw Error('The draft changed before sending.');
    clicked = true;
    click(c.send, l.win);
    for (var n = 0; n < 20; n++) {
      delay(0.25);
      foreground();
      if (c.header.value() !== target) throw Error('Conversation changed after submission; inspect WeChat before retrying.');
      if (c.editor.value() === '') return 'Submitted to WeChat. Recipient delivery is not confirmed.';
    }
    throw Error('Submission could not be confirmed. Inspect WeChat before retrying; the draft may remain.');
  } catch (err) {
    // Only clear our own untouched draft before any possible send. Never retry automatically.
    if (!clicked && c.header.value() === target && c.editor.value() === message) c.editor.value = '';
    throw err;
  }
}
