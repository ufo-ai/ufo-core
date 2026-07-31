import "./tokens.css";
import "./components.css";

const BASE = '/surface/web';
const MAX_STREAM_RETRIES = 5;
const nav = document.querySelector('nav');
const main = document.querySelector('main');
const list = document.getElementById('agents');
const workspaceList = document.getElementById('workspace');
const head = document.getElementById('agent-head');
const tabs = document.getElementById('tabs');
const panel = document.getElementById('panel');
const log = document.getElementById('log');
const composer = document.getElementById('composer');
const input = document.getElementById('msg');
const files = document.getElementById('files');
const send = composer.querySelector('button.send');
let agents = [];
let member = null;
let selected = null;
const chats = new Map();

function chatState(id) {
  let state = chats.get(id);
  if (!state) {
    state = { messages: null, busy: false, live: null, handoffs: {} };
    chats.set(id, state);
  }
  return state;
}

function bubble(cls, text) {
  const el = document.createElement('div');
  el.className = 'bubble ' + cls;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function errorLine(text) {
  const line = document.createElement('div');
  line.className = 'meta';
  line.textContent = text;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function emptyState(text) {
  const el = document.createElement('div');
  el.className = 'empty';
  el.textContent = text;
  log.appendChild(el);
}

const TABS = ['chat', 'conversations', 'overview', 'tasks', 'connections', 'skills', 'usage'];
const MAX_PREVIEW_BYTES = 256 * 1024;
const WORKSPACE_TABS = ['sources', 'credentials', 'memory', 'artifacts', 'sites', 'usage'];
const WORKSPACE_LABELS = {
  sources: 'Sources',
  credentials: 'Credentials',
  memory: 'Memory',
  artifacts: 'Artifacts',
  sites: 'Sites',
  usage: 'Usage',
};
let tab = 'chat';
let panelLoad = 0;

function hashState() {
  const workspaceMatch = location.hash.match(/^#\/workspace\/(\w+)$/);
  if (workspaceMatch && WORKSPACE_TABS.includes(workspaceMatch[1])) {
    return { agent: null, tab: 'chat', workspace: workspaceMatch[1] };
  }
  const match = location.hash.match(/^#\/agents\/([0-9a-f-]{36})(?:\/(\w+))?$/);
  if (!match) return { agent: null, tab: 'chat', workspace: null };
  return {
    agent: agents.find((agent) => agent.id === match[1]) || null,
    tab: TABS.includes(match[2]) ? match[2] : 'chat',
    workspace: null,
  };
}

function cell(text) {
  const el = document.createElement('td');
  el.textContent = text == null ? '\u2014' : text;
  return el;
}

function row(...texts) {
  const el = document.createElement('tr');
  texts.forEach((text) => el.appendChild(cell(text)));
  return el;
}

function table(headers, rows) {
  const el = document.createElement('table');
  const heading = document.createElement('tr');
  headers.forEach((header) => {
    const cellEl = document.createElement('th');
    cellEl.textContent = header;
    heading.appendChild(cellEl);
  });
  el.appendChild(heading);
  rows.forEach((entry) => el.appendChild(entry));
  return el;
}

async function adminIntent(agentId, envelope, result, notice) {
  result.textContent = '';
  let res;
  try {
    res = await fetch(BASE + '/agents/' + agentId + '/intents', {
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify(envelope),
    });
  } catch (err) {
    result.textContent = 'Network error — try again.';
    return;
  }
  const outcome = await res.json().catch(() => null);
  const message = outcome && outcome.message
    ? outcome.message
    : 'Error ' + res.status + ' — try again.';
  if (outcome && outcome.applied) {
    showAdmin(notice ? notice + ' ' + message : message);
    return;
  }
  result.textContent = message;
}

function actionButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

function actionCell(...nodes) {
  const el = document.createElement('td');
  el.append(...nodes);
  return el;
}

function adminAgentRow(agent, result) {
  const el = document.createElement('tr');
  el.append(
    cell(agent.name + (agent.main ? ' ·' : '')),
    cell(agent.model),
    cell(agent.internet_access_allowed ? 'allowed' : 'blocked'),
    cell(agent.installations.join(', ') || '—'),
    cell(agent.main ? 'every member' : agent.web_audience.concat('admins').join(', ')),
  );
  const actions = [actionButton('Copy', () => prefillCreateForm(agent))];
  if (!agent.main) {
    const email = document.createElement('input');
    email.placeholder = 'email@work.com';
    const send = (verb) => {
      if (!email.value.trim()) return;
      adminIntent(agent.id, { verb, email: email.value.trim() }, result);
    };
    actions.push(
      email,
      actionButton('Grant', () => send('grant_web_access')),
      actionButton('Revoke', () => send('revoke_web_access')),
    );
  }
  el.appendChild(actionCell(...actions));
  return el;
}

function adminMemberRow(payload, member, gated, result) {
  const mainAgent = payload.agents.find((agent) => agent.main);
  const el = document.createElement('tr');
  el.append(cell(member.email), cell(member.admin ? 'admin' : 'member'));
  if (gated) el.appendChild(cell(member.seated ? 'seated' : '—'));
  const apply = (spec) =>
    adminIntent(mainAgent.id, { verb: 'apply', kind: 'member', name: member.id, spec }, result);
  el.appendChild(actionCell(
    actionButton(member.admin ? 'Remove admin' : 'Make admin', () =>
      apply({ admin: !member.admin, seated: member.seated })),
    actionButton(member.seated ? 'Unseat' : 'Seat', () =>
      apply({ admin: member.admin, seated: !member.seated })),
  ));
  return el;
}

let createPrefill = null;

function prefillCreateForm(agent) {
  createPrefill = agent;
  showAdmin('Copying ' + agent.name + ' — configuration only.');
}

function createAgentSection(payload, result) {
  const section = document.createElement('div');
  const title = document.createElement('h2');
  title.textContent = 'Create agent';
  section.appendChild(title);
  const form = document.createElement('form');
  form.className = 'admin-create';
  const name = document.createElement('input');
  name.placeholder = 'agent name';
  const model = document.createElement('select');
  for (const id of payload.models) {
    const option = document.createElement('option');
    option.value = option.textContent = id;
    model.appendChild(option);
  }
  model.value = payload.models[0];
  const reasoning = document.createElement('select');
  for (const level of payload.reasoning_levels) {
    const option = document.createElement('option');
    option.value = option.textContent = level;
    reasoning.appendChild(option);
  }
  reasoning.value = payload.reasoning_levels[0];
  const internetLabel = document.createElement('label');
  const internet = document.createElement('input');
  internet.type = 'checkbox';
  internet.checked = true;
  internetLabel.append(internet, document.createTextNode(' public internet'));
  const prompt = document.createElement('textarea');
  prompt.placeholder = 'System prompt';
  prompt.rows = 4;
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'send';
  submit.textContent = 'Create';
  form.append(name, model, reasoning, internetLabel, prompt, submit);
  section.appendChild(form);
  const source = createPrefill;
  createPrefill = null;
  if (source) {
    model.value = source.model;
    internet.checked = source.internet_access_allowed;
    fetch(BASE + '/agents/' + source.id + '/overview', { credentials: 'same-origin' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data) return;
        prompt.value = data.agent.prompt;
        reasoning.value = data.spec.reasoning;
      })
      .catch(() => {});
  }
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!name.value.trim() || !prompt.value.trim()) return;
    const mainAgent = payload.agents.find((agent) => agent.main);
    adminIntent(
      mainAgent.id,
      {
        verb: 'apply',
        kind: 'agent',
        name: name.value.trim(),
        spec: {
          model: model.value,
          internet_access_allowed: internet.checked,
          reasoning: reasoning.value,
          prompt: prompt.value,
        },
      },
      result,
      'Created ' + name.value.trim() + '.',
    );
  });
  return section;
}

async function showAdmin(notice) {
  selected = null;
  panelLoad += 1;
  location.hash = '#/admin';
  for (const item of list.children) item.firstChild.setAttribute('aria-current', 'false');
  const view = document.createElement('div');
  view.className = 'admin-view';
  main.replaceChildren(view);
  let res;
  try {
    res = await fetch(BASE + '/api/admin', { credentials: 'same-origin' });
  } catch (err) {
    view.textContent = 'Network error — try again.';
    return;
  }
  if (!res.ok) {
    view.textContent = 'Error ' + res.status + ' — reload to retry.';
    return;
  }
  const payload = await res.json();
  const result = document.createElement('div');
  result.className = 'result mono';
  if (notice) result.textContent = notice;
  view.appendChild(result);
  const agentsTitle = document.createElement('h2');
  agentsTitle.textContent = 'Agents';
  view.appendChild(agentsTitle);
  view.appendChild(table(
    ['agent', 'model', 'public internet', 'surfaces', 'web audience', ''],
    payload.agents.map((agent) => adminAgentRow(agent, result)),
  ));
  view.appendChild(createAgentSection(payload, result));
  const membersTitle = document.createElement('h2');
  const seats = payload.seats;
  const gated = seats.limit !== null || seats.included !== null;
  membersTitle.textContent = 'Members'
    + (gated ? '' : ' · seats ungated')
    + (seats.limit !== null ? ' · ' + seats.limit + ' seat limit' : '')
    + (seats.included !== null ? ' · ' + seats.included + ' included' : '');
  view.appendChild(membersTitle);
  view.appendChild(table(
    gated ? ['member', 'role', 'seat', ''] : ['member', 'role', ''],
    payload.members.map((member) => adminMemberRow(payload, member, gated, result)),
  ));
  const billingTitle = document.createElement('h2');
  billingTitle.textContent = 'Billing';
  view.appendChild(billingTitle);
  if (payload.caps.length) {
    view.appendChild(table(
      ['cap', 'subject', 'window', 'limit', 'on breach'],
      payload.caps.map((cap) => row(
        cap.scope,
        cap.subject || '—',
        (cap.window_seconds / 3600) + 'h',
        '$' + (cap.limit_micro_usd / 1e6).toFixed(2),
        cap.on_breach
      ))
    ));
  } else {
    const none = document.createElement('div');
    none.textContent = 'No spend caps are set.';
    view.appendChild(none);
  }
  const billingLine = document.createElement('div');
  billingLine.textContent = 'The plan, invoices, and payment methods are managed with the agent '
    + 'in chat.';
  view.appendChild(billingLine);
  const deployTitle = document.createElement('h2');
  deployTitle.textContent = 'Deploy';
  view.appendChild(deployTitle);
  const ceiling = document.createElement('div');
  ceiling.textContent = 'Sandbox public internet: '
    + (payload.deploy.sandbox_internet ? 'allowed' : 'blocked');
  view.appendChild(ceiling);
  view.appendChild(table(
    ['extension', 'version', 'public internet'],
    payload.deploy.extensions.map((extension) => row(
      extension.name,
      extension.version,
      extension.sandbox_internet ? 'allowed' : 'blocked'
    ))
  ));
}

function restoreChatPane() {
  main.replaceChildren(head, tabs, log, panel, composer);
}

function wireMemberChrome() {
  document.getElementById('member-email').textContent =
    member.email + (member.admin ? ' · admin' : '');
  const admin = document.getElementById('admin');
  admin.hidden = !member.admin;
  admin.addEventListener('click', showAdmin);
}

function renderSidebar() {
  list.replaceChildren(...agents.map((agent) => {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    const name = document.createElement('span');
    name.textContent = agent.name + (agent.main ? ' ·' : '');
    const model = document.createElement('span');
    model.className = 'model mono';
    model.textContent = agent.model;
    button.append(name, model);
    button.setAttribute('aria-current', selected !== null && selected.id === agent.id);
    button.addEventListener('click', () => select(agent));
    item.appendChild(button);
    item.dataset.id = agent.id;
    return item;
  }));
}

function renderWorkspaceSidebar() {
  workspaceList.replaceChildren(...WORKSPACE_TABS.map((name) => {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = WORKSPACE_LABELS[name];
    button.setAttribute('aria-current', 'false');
    button.addEventListener('click', () => showWorkspace(name));
    item.appendChild(button);
    item.dataset.tab = name;
    return item;
  }));
}

function syncComposer() {
  const state = selected && chatState(selected.id);
  send.disabled = !state || state.busy || state.messages === null;
}

function formatSize(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  if (bytes >= 1024) return Math.round(bytes / 1024) + ' kB';
  return bytes + ' B';
}

function renderFiles(target, files) {
  const box = document.createElement('div');
  box.className = 'files';
  for (const file of files) {
    const row = document.createElement('div');
    if (file.url) {
      const link = document.createElement('a');
      link.href = file.url;
      link.textContent = file.filename;
      if (file.subject) link.title = file.subject;
      row.appendChild(link);
    } else {
      const name = document.createElement('span');
      name.textContent = file.filename;
      row.appendChild(name);
    }
    const size = document.createElement('span');
    size.className = 'mono';
    size.textContent = ' · ' + formatSize(file.size_bytes);
    row.appendChild(size);
    box.appendChild(row);
  }
  target.appendChild(box);
  log.scrollTop = log.scrollHeight;
}

function markAnswered(question, index) {
  if (!question) return null;
  const answered = (question.answered || []).concat(index);
  if (answered.length >= question.questions.length) return null;
  return { ...question, answered };
}

const MAX_ANSWER_BUTTONS = 10;

function buttonable(entry) {
  return Boolean(entry.options && entry.options.length <= MAX_ANSWER_BUTTONS
    && !entry.multi_select && !entry.free_text_only && !entry.allow_attachments);
}

function renderQuestion(agent, state, question) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  const title = document.createElement('div');
  title.textContent = question.title;
  panel.appendChild(title);
  question.questions.forEach((entry, index) => {
    if (question.answered && question.answered.includes(index)) return;
    const row = document.createElement('div');
    row.className = 'qrow';
    const text = document.createElement('div');
    text.textContent = entry.header
      ? entry.header + ' — ' + entry.question
      : entry.question;
    row.appendChild(text);
    if (buttonable(entry)) {
      const options = document.createElement('div');
      options.className = 'options';
      for (const option of entry.options) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = option.label;
        if (option.description) button.title = option.description;
        const body = question.questions.length === 1
          ? option.label
          : option.label + ' · ' + entry.question;
        button.addEventListener('click', () =>
          answer(agent, state, question, index, body, panel, row));
        options.appendChild(button);
      }
      row.appendChild(options);
    } else {
      for (const option of entry.options || []) {
        const listed = document.createElement('div');
        listed.className = 'meta';
        listed.textContent = option.description
          ? option.label + ' — ' + option.description
          : option.label;
        row.appendChild(listed);
      }
      const hint = document.createElement('div');
      hint.className = 'meta';
      hint.textContent = entry.multi_select
        ? 'Select all that apply — answer in the message box below.'
        : 'Answer in the message box below.';
      row.appendChild(hint);
    }
    panel.appendChild(row);
  });
  log.appendChild(panel);
  log.scrollTop = log.scrollHeight;
}

function renderCredentials(agent, state, request) {
  const panel = document.createElement('div');
  panel.className = 'panel';
  const reason = document.createElement('div');
  reason.textContent = request.reason;
  panel.appendChild(reason);
  for (const prompt of request.prompts) {
    panel.appendChild(credentialPromptRow(request.sealed, prompt, (slot) => {
      state.handoffs.credentials = pruneCredential(state.handoffs.credentials, slot);
    }));
  }
  log.appendChild(panel);
  log.scrollTop = log.scrollHeight;
}

function pruneCredential(request, slot) {
  if (!request) return null;
  const prompts = request.prompts.filter((prompt) => prompt.slot !== slot);
  return prompts.length ? { ...request, prompts } : null;
}

function renderHandoffs(agent, state) {
  const handoffs = state.handoffs;
  if (handoffs.files && handoffs.files.length) {
    const bubbles = log.querySelectorAll('.bubble.agent');
    const target = bubbles.length ? bubbles[bubbles.length - 1] : log;
    renderFiles(target, handoffs.files);
  }
  if (handoffs.credentials) renderCredentials(agent, state, handoffs.credentials);
  if (handoffs.question) renderQuestion(agent, state, handoffs.question);
}

function renderChat(agent) {
  const state = chatState(agent.id);
  log.replaceChildren();
  if (state.messages === null) return;
  for (const message of state.messages) {
    if (message.role === 'error') {
      errorLine(message.text);
    } else {
      bubble(message.role === 'user' ? 'me' : 'agent', message.text);
    }
  }
  if (state.live) {
    log.appendChild(state.live);
    log.scrollTop = log.scrollHeight;
  }
  renderHandoffs(agent, state);
  if (!state.messages.length && !state.busy) {
    emptyState('No conversation with ' + agent.name + ' yet.');
  }
}

function renderTabs() {
  tabs.replaceChildren(...TABS.map((name) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', name === tab);
    button.textContent = name;
    button.addEventListener('click', () => selectTab(name));
    return button;
  }));
}

function fail(load, text, notice) {
  if (load !== panelLoad) return;
  const failed = document.createElement('span');
  failed.className = 'empty';
  failed.textContent = notice ? notice + ' The panel could not be re-read: ' + text : text;
  panel.appendChild(failed);
}

function panelEmpty(text) {
  const el = document.createElement('span');
  el.className = 'empty';
  el.textContent = text;
  panel.appendChild(el);
}

function day(iso) {
  return iso == null ? null : iso.slice(0, 16).replace('T', ' ');
}

async function renderSkills(agent, load, notice) {
  let listed;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/skills',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) { panelEmpty('Error ' + res.status + ' — reload to retry.'); return; }
    listed = (await res.json()).skills;
  } catch (err) {
    fail(load, 'Network error — try again.', notice);
    return;
  }
  if (load !== panelLoad) return;
  const result = document.createElement('div');
  result.className = 'result mono';
  if (notice) result.textContent = notice;
  async function submitIntent(intent, control) {
    control.disabled = true;
    let submitted;
    try {
      submitted = await fetch(BASE + '/agents/' + agent.id + '/intents', {
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify(intent),
      });
    } catch (err) {
      result.textContent = 'Network error — try again.';
      control.disabled = false;
      return;
    }
    const outcome = await submitted.json().catch(() => null);
    result.textContent = outcome && outcome.message
      ? outcome.message
      : 'Error ' + submitted.status + ' — try again.';
    control.disabled = false;
    if (outcome && outcome.applied && selected === agent && tab === 'skills') {
      selectTab('skills', outcome.message);
    }
  }
  const custom = listed.filter((skill) => skill.origin === 'member');
  const deploy = listed.filter((skill) => skill.origin === 'deploy');
  const customTitle = document.createElement('h2');
  customTitle.textContent = "This agent's skills";
  panel.appendChild(customTitle);
  if (!custom.length) {
    panelEmpty('No member-authored skills for ' + agent.name + '.');
  } else {
    const listing = document.createElement('table');
    const heading = listing.createTHead().insertRow();
    for (const column of ['name', 'description', '']) {
      const cell = document.createElement('th');
      cell.textContent = column;
      heading.appendChild(cell);
    }
    const body = listing.createTBody();
    for (const skill of custom) {
      const entry = body.insertRow();
      entry.insertCell().textContent = skill.name;
      entry.insertCell().textContent = skill.description;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Delete';
      remove.addEventListener('click', () =>
        submitIntent({ verb: 'delete', kind: 'skill', name: skill.name }, remove));
      entry.insertCell().appendChild(remove);
    }
    panel.appendChild(listing);
  }
  const deployTitle = document.createElement('h2');
  deployTitle.textContent = 'Deploy skills';
  panel.appendChild(deployTitle);
  if (!deploy.length) {
    panelEmpty('No deploy skills.');
  } else {
    panel.appendChild(table(
      ['name', 'description'],
      deploy.map((skill) => row(skill.name, skill.description))
    ));
  }
  const formTitle = document.createElement('h2');
  formTitle.textContent = 'Save a skill';
  panel.appendChild(formTitle);
  const form = document.createElement('form');
  const name = document.createElement('input');
  name.type = 'text';
  name.placeholder = 'skill-name';
  const content = document.createElement('textarea');
  content.placeholder = '---\nname: skill-name\ndescription: …\n---\n';
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'send';
  save.textContent = 'Save';
  form.append(name, content, save);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!name.value.trim() || !content.value.trim()) return;
    submitIntent({
      verb: 'apply',
      kind: 'skill',
      name: name.value.trim(),
      spec: { files: { 'SKILL.md': content.value } },
    }, save);
  });
  panel.appendChild(form);
  panel.appendChild(result);
}

function memoryFilter(payload, place) {
  const bar = document.createElement('div');
  bar.className = 'filter';
  for (const kind of ['all'].concat(payload.kinds)) {
    const chosen = kind === 'all' ? !place.kind : place.kind === kind;
    const button = actionButton(kind, () =>
      showWorkspace('memory', undefined, { kind: kind === 'all' ? undefined : kind }));
    button.setAttribute('aria-current', chosen);
    bar.appendChild(button);
  }
  return bar;
}

function pageControls(view, payload, place) {
  const bar = document.createElement('div');
  bar.className = 'filter';
  for (const [label, cursor] of [['Newer', payload.newer], ['Older', payload.older]]) {
    if (!cursor) continue;
    bar.appendChild(actionButton(label, () =>
      showWorkspace(view, undefined, { kind: place.kind, after: cursor })));
  }
  return bar;
}

function memoryRow(match, result, query, place) {
  const el = row(match.text, match.kind, match.ref, day(match.created_at));
  const correctable = typeof match.ref === 'string' && match.ref.startsWith('memory/');
  el.appendChild(
    correctable
      ? actionCell(actionButton('Correct', () => correctMemory(match, result, query, place)))
      : cell(null)
  );
  return el;
}

function correctMemory(match, result, query, place) {
  const mainAgent = agents.find((agent) => agent.main);
  const form = document.createElement('form');
  form.className = 'correct';
  const body = document.createElement('input');
  body.type = 'text';
  body.value = match.text;
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'send';
  save.textContent = 'Record correction';
  form.append(body, save);
  result.replaceChildren(form);
  body.focus();
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!body.value.trim()) return;
    save.disabled = true;
    const outcome = await postIntent(mainAgent, {
      verb: 'record',
      kind: 'memory',
      corrects: match.ref.slice('memory/'.length),
      body: body.value.trim(),
    });
    if (outcome.applied) {
      showWorkspace('memory', query, place);
      return;
    }
    result.replaceChildren(form, document.createTextNode(outcome.message));
    save.disabled = false;
  });
}

function workspaceEmpty(view, text) {
  const el = document.createElement('span');
  el.className = 'empty';
  el.textContent = text;
  view.appendChild(el);
}

const VIEWER_TEXT_BYTES = 64 * 1024;

function artifactIsImage(entry) {
  return entry.media_type.startsWith('image/');
}

function artifactThumb(entry) {
  const preview = document.createElement('img');
  preview.className = 'thumb';
  preview.loading = 'lazy';
  preview.alt = '';
  preview.src = entry.url;
  preview.addEventListener('error', () => preview.remove());
  return preview;
}

async function artifactText(url, into) {
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
      into.textContent = 'Error ' + res.status + ' — reload to retry.';
      return;
    }
    const reader = res.body.getReader();
    const chunks = [];
    let read = 0;
    let ended = false;
    while (!ended && read <= VIEWER_TEXT_BYTES) {
      const step = await reader.read();
      if (step.done) ended = true;
      else {
        chunks.push(step.value);
        read += step.value.length;
      }
    }
    if (!ended) await reader.cancel();
    const bytes = new Uint8Array(read);
    let at = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, at);
      at += chunk.length;
    }
    const body = document.createElement('pre');
    body.textContent = new TextDecoder().decode(bytes.slice(0, VIEWER_TEXT_BYTES));
    into.textContent = '';
    into.appendChild(body);
    if (read > VIEWER_TEXT_BYTES) {
      const bound = document.createElement('div');
      bound.className = 'meta';
      bound.textContent = 'First ' + formatSize(VIEWER_TEXT_BYTES) + ' shown.';
      into.appendChild(bound);
    }
  } catch (err) {
    into.textContent = 'Network error — try again.';
  }
}

let viewer = null;
let viewerOpener = null;

function closeArtifact() {
  if (!viewer) return;
  viewer.remove();
  viewer = null;
  if (viewerOpener) viewerOpener.focus();
  viewerOpener = null;
}

function openArtifact(entry, opener) {
  closeArtifact();
  viewerOpener = opener;
  viewer = document.createElement('aside');
  viewer.className = 'viewer';
  const head = document.createElement('header');
  const title = document.createElement('h2');
  title.textContent = entry.filename;
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = 'Close';
  close.addEventListener('click', closeArtifact);
  head.append(title, close);
  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = [entry.subject, entry.media_type, formatSize(entry.size_bytes),
                      day(entry.created_at)].filter((part) => part).join(' · ');
  const shown = document.createElement('div');
  if (artifactIsImage(entry)) {
    const full = document.createElement('img');
    full.alt = '';
    full.src = entry.url;
    full.addEventListener('error', () => {
      full.remove();
      shown.className = 'meta';
      shown.textContent = 'The image did not load. Its link may have expired — reload the listing.';
    });
    shown.appendChild(full);
  } else if (
    entry.media_type.startsWith('text/') || entry.media_type === 'application/json'
  ) {
    shown.textContent = 'Loading…';
    artifactText(entry.url, shown);
  } else {
    shown.className = 'meta';
    shown.textContent = 'No preview for this file type. Download it to open it.';
  }
  const foot = document.createElement('footer');
  const download = document.createElement('a');
  download.href = entry.url;
  download.download = entry.filename;
  download.textContent = 'Download';
  foot.appendChild(download);
  viewer.append(head, meta, shown, foot);
  document.body.appendChild(viewer);
  close.focus();
}

function markWorkspace(name) {
  for (const item of list.children) item.firstChild.setAttribute('aria-current', 'false');
  for (const item of workspaceList.children) {
    item.firstChild.setAttribute('aria-current', item.dataset.tab === name);
  }
}

function workspaceSources(view, entries, notice) {
  // A source binding is the unit the `source` kind mutates: its streams share one provider,
  // account, and tenant URL, so the rows group by the kind's own binding name and each act
  // submits that binding's current spec. A row the kind does not manage (a config-registered
  // folder, a feed) carries no name and offers no acts.
  const result = document.createElement('div');
  result.className = 'result mono';
  if (notice) result.textContent = notice;
  const mainAgent = agents.find((agent) => agent.main);
  const bindings = new Map();
  const plain = [];
  for (const entry of entries) {
    if (entry.name === null) { plain.push(entry); continue; }
    if (!bindings.has(entry.name)) bindings.set(entry.name, []);
    bindings.get(entry.name).push(entry);
  }
  async function act(envelope, button) {
    button.disabled = true;
    let res;
    try {
      res = await fetch(BASE + '/agents/' + mainAgent.id + '/intents', {
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify(envelope),
      });
    } catch (err) {
      result.textContent = 'Network error — try again.';
      button.disabled = false;
      return;
    }
    const outcome = await res.json().catch(() => null);
    const message = outcome && outcome.message
      ? outcome.message
      : 'Error ' + res.status + ' — try again.';
    if (outcome && outcome.applied) {
      showWorkspace('sources', undefined, { notice: message });
      return;
    }
    result.textContent = message;
    button.disabled = false;
  }
  const rows = [];
  for (const [name, streams] of bindings) {
    const first = streams[0];
    const spec = {
      provider: first.backend,
      streams: streams.map((entry) => entry.stream).sort(),
      account_id: first.account_id,
      base_url: first.base_url,
      shared: first.shared,
    };
    const actions = document.createElement('td');
    for (const [label, envelope] of [
      ['Resync', { verb: 'apply', kind: 'source', name, spec: { ...spec, resync: true } }],
      ...(first.shared ? [] : [
        ['Share', { verb: 'apply', kind: 'source', name, spec: { ...spec, shared: true } }],
      ]),
      ['Remove', { verb: 'delete', kind: 'source', name }],
    ]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => act(envelope, button));
      actions.appendChild(button);
    }
    const line = row(
      first.backend,
      streams.map((entry) => entry.stream).sort().join(', '),
      first.owner_email || '—',
      first.shared ? 'shared' : 'private',
      String(streams.reduce((total, entry) => total + entry.consecutive_errors, 0)),
      streams.map((entry) => entry.next_sync_at).sort()[0].replace('T', ' ').slice(0, 16)
    );
    line.appendChild(actions);
    rows.push(line);
  }
  for (const entry of plain) {
    const line = row(
      entry.backend,
      '—',
      entry.owner_email || '—',
      entry.shared ? 'shared' : 'private',
      String(entry.consecutive_errors),
      entry.next_sync_at.replace('T', ' ').slice(0, 16)
    );
    line.appendChild(document.createElement('td'));
    rows.push(line);
  }
  if (!rows.length) {
    workspaceEmpty(view, 'No sources are registered. Register one in chat — the agent connects '
      + 'the account or credential it needs as part of the request.');
    view.appendChild(result);
    return;
  }
  view.appendChild(table(
    ['source', 'streams', 'owner', 'access', 'errors', 'next sync', ''],
    rows
  ));
  view.appendChild(result);
}

async function showWorkspace(name, query, placement) {
  // `placement` carries where a view opens: the keyset cursor and filter a paged listing walks
  // with, and the outcome a mutation left behind for the refreshed view to state.
  closeArtifact();
  selected = null;
  panelLoad += 1;
  const place = placement || {};
  location.hash = '#/workspace/' + name;
  markWorkspace(name);
  const view = document.createElement('div');
  view.className = 'admin-view';
  main.replaceChildren(view);
  let url = BASE + '/workspace/' + name;
  const params = new URLSearchParams();
  if (name === 'memory' && query) params.set('q', query);
  else {
    if (place.kind) params.set('kind', place.kind);
    if (place.after) params.set('after', place.after);
  }
  const search = params.toString();
  if (search) url += '?' + search;
  if (name === 'memory') {
    const form = document.createElement('form');
    form.className = 'search';
    const search = document.createElement('input');
    search.placeholder = 'Search memory…';
    search.value = query || '';
    const go = document.createElement('button');
    go.type = 'submit';
    go.className = 'send';
    go.textContent = 'Search';
    form.append(search, go);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      showWorkspace('memory', search.value.trim() || undefined);
    });
    view.appendChild(form);
  }
  let payload;
  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
      workspaceEmpty(view, 'Error ' + res.status + ' — reload to retry.');
      return;
    }
    payload = await res.json();
  } catch (err) {
    workspaceEmpty(view, 'Network error — try again.');
    return;
  }
  if (name === 'sources') {
    workspaceSources(view, payload.sources, place.notice);
    return;
  }
  if (name === 'credentials') {
    workspaceCredentials(view, payload.slots, place.notice);
    return;
  }
  if (name === 'memory') {
    if (!payload.available) {
      workspaceEmpty(view, 'This deploy has no memory extension.');
      return;
    }
    if (!query) view.appendChild(memoryFilter(payload, place));
    if (!payload.matches.length) {
      workspaceEmpty(view, query
        ? 'No matches.'
        : (place.kind ? 'No ' + place.kind + ' memories on this page.' : 'No memories yet.'));
      if (!query) view.appendChild(pageControls('memory', payload, place));
      return;
    }
    const result = document.createElement('div');
    result.className = 'result mono';
    view.appendChild(table(
      ['memory', 'kind', 'ref', 'date', ''],
      payload.matches.map((match) => memoryRow(match, result, query, place))
    ));
    if (!query) view.appendChild(pageControls('memory', payload, place));
    view.appendChild(result);
    return;
  }
  if (name === 'artifacts') {
    if (!payload.artifacts.length) { workspaceEmpty(view, 'No shared files yet.'); return; }
    view.appendChild(table(
      ['file', 'subject', 'type', 'size', 'date'],
      payload.artifacts.map((entry) => {
        const line = row(entry.filename, entry.subject, entry.media_type,
                         formatSize(entry.size_bytes), day(entry.created_at));
        if (entry.url) {
          const open = document.createElement('button');
          open.type = 'button';
          open.className = 'open';
          if (artifactIsImage(entry)) open.appendChild(artifactThumb(entry));
          const label = document.createElement('span');
          label.textContent = entry.filename;
          open.appendChild(label);
          open.addEventListener('click', () => openArtifact(entry, open));
          line.childNodes[0].textContent = '';
          line.childNodes[0].appendChild(open);
        }
        return line;
      })
    ));
    view.appendChild(pageControls('artifacts', payload, place));
    return;
  }
  if (name === 'usage') {
    renderWorkspaceUsage(view, payload);
    return;
  }
  if (!payload.available) {
    workspaceEmpty(view, 'No sites extension is installed.');
    return;
  }
  if (!payload.sites.length) { workspaceEmpty(view, 'No sites are hosted.'); return; }
  view.appendChild(table(
    ['site', 'summary'],
    payload.sites.map((site) => row(site.name, site.summary))
  ));
}

function money(micro) {
  return '$' + (micro / 1e6).toFixed(6);
}

function usageSection(view, title) {
  const heading = document.createElement('h2');
  heading.textContent = title;
  view.appendChild(heading);
}

function usageDimensions(view, lines, empty) {
  if (!lines.length) { workspaceEmpty(view, empty); return; }
  view.appendChild(table(
    ['dimension', 'units', 'cost'],
    lines.map((line) => row(
      line.dimension, line.amount.toLocaleString(), money(line.priced_micro_usd)
    ))
  ));
}

function renderWorkspaceUsage(view, payload) {
  usageSection(view, 'Your spend · last ' + (payload.window_seconds / 3600) + 'h · '
    + money(payload.total_micro_usd));
  usageDimensions(view, payload.by_dimension, 'No spend of yours in window.');
  usageSection(view, 'Your caps');
  if (!payload.caps.length) workspaceEmpty(view, 'No caps are set on you.');
  else {
    view.appendChild(table(
      ['window', 'limit', 'on breach'],
      payload.caps.map((cap) => row(
        (cap.window_seconds / 3600) + 'h',
        '$' + (cap.limit_micro_usd / 1e6).toFixed(2),
        cap.on_breach
      ))
    ));
  }
  if (!payload.workspace) return;
  usageSection(view, 'Workspace · ' + money(payload.workspace.total_micro_usd));
  usageDimensions(view, payload.workspace.by_dimension, 'No workspace spend in window.');
  for (const [title, rows] of [
    ['By member', payload.workspace.by_member],
    ['By agent', payload.workspace.by_agent],
  ]) {
    usageSection(view, title);
    if (!rows.length) workspaceEmpty(view, 'None in window.');
    else {
      view.appendChild(table(
        ['subject', 'cost'],
        rows.map((entry) => row(entry.label, money(entry.priced_micro_usd)))
      ));
    }
  }
}

async function renderUsage(agent, load) {
  let report;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/usage',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (res.status === 404) {
      panelEmpty('Usage for this agent is not shared with you.');
      return;
    }
    if (!res.ok) { panelEmpty('Error ' + res.status + ' — reload to retry.'); return; }
    report = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.');
    return;
  }
  if (load !== panelLoad) return;
  const heading = document.createElement('h2');
  heading.textContent = 'Last ' + (report.window_seconds / 3600) + 'h · $'
    + (report.total_micro_usd / 1e6).toFixed(6);
  panel.appendChild(heading);
  if (!report.by_dimension.length) panelEmpty('No spend in window for ' + agent.name + '.');
  else {
    panel.appendChild(table(
      ['dimension', 'units', 'cost'],
      report.by_dimension.map((line) => row(
        line.dimension,
        line.amount.toLocaleString(),
        '$' + (line.priced_micro_usd / 1e6).toFixed(6)
      ))
    ));
  }
  const caps = document.createElement('h2');
  caps.textContent = 'Caps for this agent';
  panel.appendChild(caps);
  if (!report.caps.length) panelEmpty('No agent-scoped caps.');
  else {
    panel.appendChild(table(
      ['window', 'limit', 'on breach'],
      report.caps.map((cap) => row(
        (cap.window_seconds / 3600) + 'h',
        '$' + (cap.limit_micro_usd / 1e6).toFixed(2),
        cap.on_breach
      ))
    ));
  }
}

function turnTree(turns, spawned) {
  const children = new Map();
  for (const turn of spawned) {
    if (!children.has(turn.parent_turn_id)) children.set(turn.parent_turn_id, []);
    children.get(turn.parent_turn_id).push(turn);
  }
  const rows = [];
  const placed = new Set();
  function walk(turn, depth) {
    rows.push({ turn, depth });
    placed.add(turn.id);
    for (const child of children.get(turn.id) || []) walk(child, depth + 1);
  }
  for (const turn of turns) walk(turn, 0);
  for (const turn of spawned) {
    if (!placed.has(turn.id)) walk(turn, 0);
  }
  return rows;
}

function turnLine(entry) {
  const line = document.createElement('div');
  line.className = entry.depth ? 'turn nested' : 'turn';
  line.style.marginLeft = (entry.depth * 16) + 'px';
  const heading = document.createElement('div');
  heading.className = 'mono';
  heading.textContent = (entry.turn.subagent_profile
    ? 'subagent ' + entry.turn.subagent_profile
    : 'turn ' + entry.turn.seq)
    + ' · ' + entry.turn.status
    + ' · ' + (day(entry.turn.created_at) || '');
  const asked = document.createElement('div');
  asked.className = 'bubble me';
  asked.textContent = entry.turn.inbound;
  line.append(heading, asked);
  const answer = entry.turn.outcome || entry.turn.error_class;
  if (answer) {
    const said = document.createElement('div');
    said.className = 'bubble agent';
    said.textContent = answer;
    line.appendChild(said);
  }
  return line;
}

async function renderConversationFiles(agent, conversation, load, target) {
  let payload;
  try {
    const res = await fetch(
      BASE + '/agents/' + agent.id + '/conversations/' + conversation.id + '/files',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) { panelEmpty('Error ' + res.status + ' — reload to retry.'); return; }
    payload = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.');
    return;
  }
  if (load !== panelLoad) return;
  const heading = document.createElement('h2');
  heading.textContent = 'Workspace files';
  target.appendChild(heading);
  if (!payload.files.length) {
    const none = document.createElement('span');
    none.className = 'empty';
    none.textContent = "No files in this conversation's workspace.";
    target.appendChild(none);
    return;
  }
  const preview = document.createElement('pre');
  preview.hidden = true;
  target.appendChild(table(
    ['file', 'size', 'modified', ''],
    payload.files.map((entry) => {
      const line = row(entry.path, formatSize(entry.size_bytes), day(entry.modified_at));
      const url = BASE + '/agents/' + agent.id + '/conversations/' + conversation.id
        + '/files/' + entry.path;
      const actions = document.createElement('td');
      if (entry.size_bytes <= MAX_PREVIEW_BYTES) {
        actions.appendChild(actionButton('View', async () => {
          preview.hidden = false;
          preview.textContent = 'Reading ' + entry.path + '…';
          try {
            const res = await fetch(url, { credentials: 'same-origin' });
            preview.textContent = res.ok
              ? await res.text()
              : 'Error ' + res.status + ' — reload to retry.';
          } catch (err) {
            preview.textContent = 'Network error — try again.';
          }
        }));
      }
      const download = document.createElement('a');
      download.href = url;
      download.textContent = 'Download';
      actions.appendChild(download);
      line.appendChild(actions);
      return line;
    })
  ));
  target.appendChild(preview);
}

async function renderConversation(agent, conversation, load) {
  panel.replaceChildren();
  const back = actionButton('All conversations', () => selectTab('conversations'));
  panel.appendChild(back);
  const heading = document.createElement('h2');
  heading.textContent = conversation.surface + ' · '
    + (conversation.member_email || conversation.queue_key);
  panel.appendChild(heading);
  let payload;
  try {
    const res = await fetch(
      BASE + '/agents/' + agent.id + '/conversations/' + conversation.id + '/turns',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (res.status === 404) {
      panelEmpty('This conversation is not shared with you.');
      return;
    }
    if (!res.ok) { panelEmpty('Error ' + res.status + ' — reload to retry.'); return; }
    payload = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.');
    return;
  }
  if (load !== panelLoad) return;
  if (!payload.turns.length && !payload.subagent_turns.length) {
    panelEmpty('No turns in this conversation yet.');
  }
  else {
    const rows = turnTree(payload.turns, payload.subagent_turns);
    const transcript = document.createElement('div');
    transcript.className = 'transcript';
    for (const entry of rows) transcript.appendChild(turnLine(entry));
    panel.appendChild(transcript);
  }
  await renderConversationFiles(agent, conversation, load, panel);
}

async function renderConversations(agent, load) {
  let payload;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/conversations',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) { panelEmpty('Error ' + res.status + ' — reload to retry.'); return; }
    payload = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.');
    return;
  }
  if (load !== panelLoad) return;
  if (!payload.conversations.length) {
    panelEmpty('No conversations with ' + agent.name + ' yet.');
    return;
  }
  panel.appendChild(table(
    ['conversation', 'surface', 'turns', 'last activity', ''],
    payload.conversations.map((entry) => {
      const line = row(
        entry.member_email || entry.queue_key,
        entry.surface,
        String(entry.turn_count),
        day(entry.last_turn_at) || day(entry.created_at)
      );
      const actions = document.createElement('td');
      if (entry.readable) {
        actions.appendChild(actionButton('Open', () => renderConversation(agent, entry, load)));
      } else {
        const held = document.createElement('span');
        held.className = 'mono';
        held.textContent = 'not shared with you';
        actions.appendChild(held);
      }
      line.appendChild(actions);
      return line;
    })
  ));
}

async function selectTab(name, detail) {
  tab = name;
  const load = ++panelLoad;
  renderTabs();
  location.hash = '#/agents/' + selected.id + (name === 'chat' ? '' : '/' + name);
  const chatting = name === 'chat';
  log.hidden = !chatting;
  composer.hidden = !chatting;
  panel.hidden = chatting;
  if (chatting) {
    input.focus();
    return;
  }
  const agent = selected;
  panel.replaceChildren();
  if (name === 'overview') { await renderOverview(agent, load, detail); return; }
  if (name === 'tasks') { await renderTasks(agent, load, detail); return; }
  if (name === 'conversations') { await renderConversations(agent, load); return; }
  if (name === 'connections') { await renderConnections(agent, load, detail); return; }
  if (name === 'skills') { await renderSkills(agent, load, detail); return; }
  if (name === 'usage') { await renderUsage(agent, load); return; }
  throw new Error('no renderer for the ' + name + ' tab');
}

function panelSection(title) {
  const section = document.createElement('section');
  const heading = document.createElement('h2');
  heading.textContent = title;
  section.appendChild(heading);
  panel.appendChild(section);
  return section;
}

function specField(key, prop, value, options) {
  const field = document.createElement('div');
  field.className = 'field';
  const label = document.createElement('label');
  label.textContent = key;
  field.appendChild(label);
  let widget;
  const choices = options || prop.enum;
  if (choices) {
    widget = document.createElement('select');
    for (const option of choices) {
      const entry = document.createElement('option');
      entry.value = entry.textContent = option;
      entry.selected = option === value;
      widget.appendChild(entry);
    }
  } else if (prop.type === 'boolean') {
    widget = document.createElement('input');
    widget.type = 'checkbox';
    widget.checked = value === true;
  } else {
    widget = document.createElement('input');
    widget.type = 'text';
    widget.value = value === undefined || value === null ? '' : String(value);
  }
  widget.dataset.key = key;
  label.htmlFor = widget.id = 'spec-' + key;
  field.appendChild(widget);
  return field;
}

async function postIntent(agent, body) {
  let res;
  try {
    res = await fetch(BASE + '/agents/' + agent.id + '/intents', {
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify(body),
    });
  } catch (err) {
    return { applied: false, message: 'Network error — try again.' };
  }
  const outcome = await res.json().catch(() => null);
  if (outcome && (outcome.message || outcome.credentials)) return outcome;
  return { applied: false, message: 'Error ' + res.status + ' — try again.' };
}

function credentialPromptRow(sealed, prompt, stored) {
  const form = document.createElement('form');
  const label = document.createElement('div');
  label.textContent = prompt.prompt;
  const field = document.createElement('input');
  field.type = 'password';
  field.autocomplete = 'off';
  field.placeholder = prompt.slot;
  const store = document.createElement('button');
  store.type = 'submit';
  store.className = 'send';
  store.textContent = 'Store';
  form.append(field, store);
  const line = document.createElement('div');
  line.append(label, form);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const value = field.value;
    if (!value.trim()) return;
    store.disabled = true;
    const body = new URLSearchParams({ sealed, slot: prompt.slot, value });
    let res;
    try {
      res = await fetch(BASE + '/credentials',
        { method: 'POST', body, credentials: 'same-origin' });
    } catch (err) {
      label.textContent = prompt.prompt + ' — network error, try again.';
      store.disabled = false;
      return;
    }
    if (!res.ok) {
      label.textContent = prompt.prompt + ' — ' + (await res.text()) + '.';
      store.disabled = false;
      return;
    }
    label.textContent = 'Stored ' + prompt.slot + '.';
    form.remove();
    stored(prompt.slot);
  });
  return line;
}

function workspaceCredentials(view, slots, notice) {
  // A slot's value is never a spec: Set and Replace ask the main agent's lane to mint the same
  // sealed private prompt a chat turn produces, and the value crosses only in that prompt's
  // fulfillment. Clear rides the credential kind's own delete verb, admin-gated there.
  if (!slots.length) {
    workspaceEmpty(view, 'No credential slots are declared.');
    return;
  }
  const result = document.createElement('div');
  result.className = 'result mono';
  if (notice) result.textContent = notice;
  const prompts = document.createElement('div');
  const mainAgent = agents.find((agent) => agent.main);
  async function act(entry, verb, button) {
    button.disabled = true;
    const answered = await postIntent(mainAgent, { verb, kind: 'credential', name: entry.name });
    button.disabled = false;
    if (answered.credentials) {
      const reason = document.createElement('div');
      reason.textContent = answered.credentials.reason;
      prompts.replaceChildren(reason);
      for (const prompt of answered.credentials.prompts) {
        prompts.appendChild(credentialPromptRow(answered.credentials.sealed, prompt, (slot) =>
          showWorkspace('credentials', undefined, { notice: 'Stored ' + slot + '.' })));
      }
      return;
    }
    if (answered.applied) {
      showWorkspace('credentials', undefined, { notice: answered.message });
      return;
    }
    result.textContent = answered.message;
  }
  view.appendChild(table(
    ['slot', 'description', 'extension', 'state', ''],
    slots.map((entry) => {
      const line = row(entry.slot, entry.description, entry.extension,
                       entry.filled ? 'filled' : 'empty');
      const actions = document.createElement('td');
      for (const [label, verb] of [
        [entry.filled ? 'Replace' : 'Set', 'request'],
        ...(entry.filled ? [['Clear', 'delete']] : []),
      ]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = label;
        button.addEventListener('click', () => act(entry, verb, button));
        actions.appendChild(button);
      }
      line.appendChild(actions);
      return line;
    })
  ));
  view.append(prompts, result);
}

function connectHandoff(target, turnId) {
  const source = new EventSource(BASE + '/turns/' + turnId + '/stream');
  source.addEventListener('connect', (event) => {
    const link = document.createElement('a');
    link.href = JSON.parse(event.data).url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Open the provider consent page';
    target.replaceChildren(link);
    source.close();
  });
  source.addEventListener('connect_error', (event) => {
    target.textContent = JSON.parse(event.data).message;
    source.close();
  });
  source.addEventListener('terminal', () => source.close());
  source.onerror = () => source.close();
}

async function renderConnections(agent, load, notice) {
  panel.replaceChildren();
  let entries;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/connections',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) {
      fail(load, 'Error ' + res.status + ' — reload to retry.', notice);
      return;
    }
    entries = (await res.json()).connections;
  } catch (err) {
    fail(load, 'Network error — try again.', notice);
    return;
  }
  if (load !== panelLoad) return;
  const form = document.createElement('form');
  form.className = 'search';
  const provider = document.createElement('input');
  provider.placeholder = 'Provider (github, notion, …)';
  const share = document.createElement('input');
  share.type = 'checkbox';
  share.id = 'connect-shared';
  const shareLabel = document.createElement('label');
  shareLabel.htmlFor = share.id;
  shareLabel.textContent = 'Share with agent';
  shareLabel.prepend(share);
  const go = document.createElement('button');
  go.type = 'submit';
  go.className = 'send';
  go.textContent = 'Connect';
  form.append(provider, shareLabel, go);
  const handoff = document.createElement('div');
  handoff.className = 'result mono';
  if (notice) handoff.textContent = notice;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const named = provider.value.trim();
    if (!named) return;
    go.disabled = true;
    const outcome = await postIntent(agent, {
      verb: 'connect',
      kind: 'connection',
      name: named,
      spec: { shared: share.checked },
    });
    go.disabled = false;
    if (!outcome.applied) {
      handoff.textContent = outcome.message;
      return;
    }
    handoff.textContent = 'Consent opens privately for you.';
    connectHandoff(handoff, outcome.turn_id);
  });
  panel.append(form, handoff);
  if (!entries.length) {
    panelEmpty('No accounts are connected to this agent.');
    return;
  }
  const table = document.createElement('table');
  const header = table.createTHead().insertRow();
  for (const column of ['provider', 'account', 'owner', 'access', 'connected', '']) {
    const cell = document.createElement('th');
    cell.textContent = column;
    header.appendChild(cell);
  }
  const body = table.createTBody();
  for (const entry of entries) {
    const row = body.insertRow();
    for (const value of [entry.provider, entry.account_id, entry.owner_email,
                         entry.shared ? 'agent-shared' : 'private',
                         entry.connected_at.slice(0, 10)]) {
      row.insertCell().textContent = value == null ? '\u2014' : value;
    }
    const actions = row.insertCell();
    if (entry.owner_email === null) continue;
    const flip = document.createElement('button');
    flip.type = 'button';
    flip.textContent = entry.shared ? 'Make private' : 'Share with agent';
    flip.addEventListener('click', async () => {
      flip.disabled = true;
      const outcome = await postIntent(agent, {
        verb: 'apply',
        kind: 'connector_grant',
        name: entry.grant,
        spec: {
          provider: entry.provider,
          account_id: entry.account_id,
          shared: !entry.shared,
        },
      });
      if (tab === 'connections') selectTab('connections', outcome.message);
    });
    const revoke = document.createElement('button');
    revoke.type = 'button';
    revoke.textContent = 'Revoke';
    revoke.addEventListener('click', async () => {
      revoke.disabled = true;
      const outcome = await postIntent(agent, {
        verb: 'delete',
        kind: 'connector_grant',
        name: entry.grant,
      });
      if (tab === 'connections') selectTab('connections', outcome.message);
    });
    actions.append(flip, revoke);
  }
  panel.appendChild(table);
}

function taskSpecType(prop) {
  if (prop.type) return { type: prop.type };
  const alternative = (prop.anyOf || []).find((entry) => entry.type && entry.type !== 'null');
  return { type: alternative ? alternative.type : 'string' };
}

function taskForm(agent, schema, task) {
  const form = document.createElement('form');
  const name = document.createElement('input');
  name.type = 'text';
  name.placeholder = 'task-name';
  if (task) {
    name.value = task.name;
    name.disabled = true;
  }
  form.appendChild(name);
  const editable = Object.entries(schema.properties)
    .filter(([key]) => key !== 'paused')
    .filter(([key]) => !task || task.prompt !== null || key === 'schedule' || key === 'expires_at');
  const current = task
    ? { schedule: task.schedule, prompt: task.prompt, description: task.description,
        expires_at: task.expires_at }
    : {};
  for (const [key, prop] of editable) {
    form.appendChild(specField(key, taskSpecType(prop), current[key], null));
  }
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'send';
  save.textContent = task ? 'Save task' : 'Create task';
  form.appendChild(save);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    const spec = {};
    for (const widget of form.querySelectorAll('input[data-key]')) {
      const value = widget.value.trim();
      if (value) spec[widget.dataset.key] = value;
      else if (task && widget.dataset.key === 'description') spec[widget.dataset.key] = '';
    }
    const outcome = await postIntent(agent, {
      verb: 'apply',
      kind: 'scheduled_task',
      name: name.value.trim(),
      spec,
    });
    save.disabled = false;
    if (tab === 'tasks' && selected === agent) selectTab('tasks', outcome.message);
  });
  return form;
}

async function renderTasks(agent, load, notice) {
  panel.replaceChildren();
  let payload;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/tasks',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) {
      fail(load, 'Error ' + res.status + ' — reload to retry.', notice);
      return;
    }
    payload = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.', notice);
    return;
  }
  if (load !== panelLoad) return;
  if (notice) {
    const outcome = document.createElement('div');
    outcome.className = 'result mono';
    outcome.textContent = notice;
    panel.appendChild(outcome);
  }
  const act = async (envelope) => {
    const outcome = await postIntent(agent, envelope);
    if (tab === 'tasks' && selected === agent) selectTab('tasks', outcome.message);
  };
  let formSection = null;
  if (!payload.tasks.length) {
    panelEmpty('No scheduled tasks for this agent.');
  } else {
    const table = document.createElement('table');
    const header = table.createTHead().insertRow();
    for (const column of ['name', 'schedule', 'task', 'creator', 'state', 'next run',
                          'last run', 'expires', '']) {
      const cell = document.createElement('th');
      cell.textContent = column;
      header.appendChild(cell);
    }
    const body = table.createTBody();
    for (const entry of payload.tasks) {
      const row = body.insertRow();
      for (const value of [entry.name, entry.schedule,
                           entry.description || entry.prompt || 'private member task',
                           entry.created_by || '—',
                           entry.paused ? 'paused' : 'scheduled',
                           day(entry.next_run_at) || '—',
                           day(entry.last_run_at) || '—',
                           day(entry.expires_at) || '—']) {
        row.insertCell().textContent = value;
      }
      const actions = row.insertCell();
      if (!payload.spec_schema) continue;
      actions.className = 'options';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit';
      edit.addEventListener('click', () => {
        formSection.replaceChildren();
        const heading = document.createElement('h2');
        heading.textContent = 'Edit ' + entry.name;
        formSection.append(heading, taskForm(agent, payload.spec_schema, entry));
      });
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.textContent = entry.paused ? 'Resume' : 'Pause';
      toggle.addEventListener('click', () => act({
        verb: 'apply',
        kind: 'scheduled_task',
        name: entry.name,
        spec: { paused: !entry.paused },
      }));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Delete';
      remove.addEventListener('click', () => act({
        verb: 'delete',
        kind: 'scheduled_task',
        name: entry.name,
      }));
      actions.append(edit, toggle, remove);
    }
    panel.appendChild(table);
  }
  if (payload.spec_schema) {
    formSection = panelSection('New task');
    formSection.appendChild(taskForm(agent, payload.spec_schema, null));
  }
}


async function renderOverview(agent, load, notice) {
  panel.replaceChildren();
  let data;
  try {
    const res = await fetch(BASE + '/agents/' + agent.id + '/overview',
      { credentials: 'same-origin' });
    if (load !== panelLoad) return;
    if (!res.ok) {
      fail(load, 'Error ' + res.status + ' — reload to retry.', notice);
      return;
    }
    data = await res.json();
  } catch (err) {
    fail(load, 'Network error — try again.', notice);
    return;
  }
  if (load !== panelLoad) return;
  const identity = panelSection('Agent');
  const facts = document.createElement('div');
  facts.className = 'mono';
  facts.textContent = [
    data.agent.main ? 'main agent' : 'agent',
    'installations: ' + (data.agent.surfaces.length ? data.agent.surfaces.join(', ') : 'none'),
    'updated ' + data.agent.updated_at.slice(0, 16).replace('T', ' '),
  ].join(' · ');
  identity.appendChild(facts);

  const settings = panelSection('Settings');
  const form = document.createElement('form');
  const properties = data.spec_schema.properties || {};
  for (const key of Object.keys(properties)) {
    form.appendChild(specField(
      key, properties[key], data.spec[key], key === 'model' ? data.models : null));
  }
  if (!data.deploy.sandbox_internet) {
    const ceiling = document.createElement('div');
    ceiling.className = 'hint';
    ceiling.textContent = 'This deploy grants no sandbox public internet — the agent setting '
      + 'narrows a capability that is currently off.';
    form.appendChild(ceiling);
  }
  const save = document.createElement('button');
  save.type = 'submit';
  save.className = 'send';
  save.textContent = 'Save';
  form.appendChild(save);
  const result = document.createElement('div');
  result.className = 'result mono';
  if (notice) result.textContent = notice;
  form.appendChild(result);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    const spec = {};
    for (const widget of form.querySelectorAll('input[data-key], select[data-key]')) {
      spec[widget.dataset.key] = widget.type === 'checkbox' ? widget.checked : widget.value;
    }
    let submitted;
    try {
      submitted = await fetch(BASE + '/agents/' + agent.id + '/intents', {
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ verb: 'apply', kind: 'agent', name: data.agent.name, spec }),
      });
    } catch (err) {
      result.textContent = 'Network error — try again.';
      save.disabled = false;
      return;
    }
    const outcome = await submitted.json().catch(() => null);
    result.textContent = outcome && outcome.message
      ? outcome.message
      : 'Error ' + submitted.status + ' — try again.';
    save.disabled = false;
    if (outcome && outcome.applied) {
      agent.model = spec.model || agent.model;
      renderSidebar();
      if (selected !== agent) return;
      renderHead(agent);
      if (tab === 'overview') selectTab('overview', outcome.message);
    }
  });
  settings.appendChild(form);

  const prompt = panelSection('Prompt');
  const digest = document.createElement('div');
  digest.className = 'hint mono';
  digest.textContent = 'digest ' + data.agent.prompt_digest + ' — prompt changes go through the '
    + 'governed proposal path in chat';
  prompt.appendChild(digest);
  const text = document.createElement('pre');
  text.textContent = data.agent.prompt;
  prompt.appendChild(text);

  if (data.audience) {
    const audience = panelSection('Web audience');
    const emails = document.createElement('div');
    emails.className = 'mono';
    emails.textContent = data.agent.main
      ? 'every member'
      : data.audience.length
        ? data.audience.join(', ')
        : 'no member grants — admins only';
    audience.appendChild(emails);
  }
}


function renderHead(agent) {
  head.replaceChildren();
  const title = document.createElement('h1');
  title.textContent = agent.name;
  const model = document.createElement('span');
  model.className = 'model mono';
  model.textContent = agent.model;
  head.append(title, model);
}

async function select(agent, name, detail) {
  closeArtifact();
  if (!head.isConnected) restoreChatPane();
  selected = agent;
  for (const item of workspaceList.children) {
    item.firstChild.setAttribute('aria-current', 'false');
  }
  for (const item of list.children) {
    item.firstChild.setAttribute('aria-current', item.dataset.id === agent.id);
  }
  renderHead(agent);
  const state = chatState(agent.id);
  renderChat(agent);
  syncComposer();
  await selectTab(name || 'chat', detail);
  if (tab === 'chat' && state.messages === null) {
    const res = await fetch(BASE + '/agents/' + agent.id + '/transcript',
      { credentials: 'same-origin' });
    const payload = res.ok ? await res.json() : { messages: [] };
    if (state.messages === null) {
      state.messages = payload.messages;
      state.handoffs = {
        question: payload.question || null,
        credentials: payload.credentials || null,
        files: payload.files || null,
      };
    }
    if (selected !== agent || tab !== 'chat') return;
    renderChat(agent);
    syncComposer();
  }
}

composer.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = input.value.trim();
  const attached = Array.from(files.files || []);
  if ((!text && !attached.length) || !selected) return;
  const agent = selected;
  const state = chatState(agent.id);
  if (state.busy || state.messages === null) return;
  input.value = '';
  state.busy = true;
  syncComposer();
  if (!state.messages.length) log.replaceChildren();
  const shown = text || attached.map((file) => file.name).join(', ');
  state.messages.push({ role: 'user', text: shown });
  bubble('me', shown);
  const reply = bubble('agent', '');
  let body = text;
  if (attached.length) {
    body = new FormData();
    body.set('message', text);
    for (const file of attached) body.append('file', file);
  }
  files.value = '';
  state.live = reply;
  run(agent, state, body, reply);
});

function finishTurn(agent, state, reply) {
  state.busy = false;
  state.live = null;
  if (selected === agent) {
    if (!reply.isConnected) renderChat(agent);
    if (tab === 'chat') input.focus();
    syncComposer();
  }
}

async function run(agent, state, body, reply) {
  let res;
  try {
    res = await fetch(BASE + '/agents/' + agent.id + '/chat',
      { method: 'POST', body, credentials: 'same-origin' });
  } catch (err) {
    state.messages.push({ role: 'error', text: 'Network error — try again.' });
    reply.remove();
    finishTurn(agent, state, reply);
    return;
  }
  if (!res.ok) {
    state.messages.push({ role: 'error', text: 'Error ' + res.status + ' — try again.' });
    reply.remove();
    finishTurn(agent, state, reply);
    return;
  }
  streamTurn(agent, state, (await res.json()).turn_id, reply, false);
}

async function answer(agent, state, question, questionIndex, body, panel, row) {
  if (state.busy || state.messages === null) return;
  state.busy = true;
  syncComposer();
  const reply = bubble('agent', '');
  state.live = reply;
  let res;
  try {
    res = await fetch(BASE + '/agents/' + agent.id + '/chat', {
      method: 'POST',
      body,
      credentials: 'same-origin',
      headers: {
        'x-ufo-answer-turn': question.turn_id,
        'x-ufo-answer-question': String(questionIndex),
      },
    });
  } catch (err) {
    state.messages.push({ role: 'error', text: 'Network error — try again.' });
    reply.remove();
    finishTurn(agent, state, reply);
    return;
  }
  if (!res.ok) {
    state.messages.push({ role: 'error', text: 'Error ' + res.status + ' — try again.' });
    reply.remove();
    finishTurn(agent, state, reply);
    return;
  }
  const payload = await res.json();
  const landed = payload.body || body;
  state.messages.push({ role: 'user', text: landed });
  if (reply.isConnected) {
    const mine = document.createElement('div');
    mine.className = 'bubble me';
    mine.textContent = landed;
    log.insertBefore(mine, reply);
  }
  if (state.handoffs.question && state.handoffs.question.turn_id === question.turn_id) {
    state.handoffs.question = markAnswered(state.handoffs.question, questionIndex);
  }
  row.remove();
  if (!panel.querySelector('.qrow')) panel.remove();
  streamTurn(agent, state, payload.turn_id, reply, true);
}

function streamTurn(agent, state, turnId, reply, answering) {
  const source = new EventSource(BASE + '/turns/' + turnId + '/stream');
  const streamedText = document.createTextNode('');
  reply.appendChild(streamedText);
  let sawFiles = false;
  let meter = null;
  let stalled = 0;
  let activity = null;
  function note(text) {
    if (!activity) {
      activity = document.createElement('div');
      activity.className = 'meta';
      reply.appendChild(activity);
    }
    activity.textContent = text;
    log.scrollTop = log.scrollHeight;
  }
  function record() {
    if (streamedText.textContent) {
      state.messages.push({ role: 'assistant', text: streamedText.textContent });
    }
  }
  function close() {
    source.close();
    finishTurn(agent, state, reply);
  }
  source.addEventListener('open', () => { stalled = 0; });
  source.onmessage = (event) => {
    streamedText.textContent += JSON.parse(event.data).text;
    log.scrollTop = log.scrollHeight;
  };
  source.addEventListener('files', (event) => {
    const payload = JSON.parse(event.data);
    sawFiles = true;
    state.handoffs.files = payload.files;
    if (selected === agent) renderFiles(reply, payload.files);
  });
  source.addEventListener('credentials', (event) => {
    const payload = JSON.parse(event.data);
    state.handoffs.credentials = payload;
    if (selected === agent) renderCredentials(agent, state, payload);
  });
  source.addEventListener('tool', (event) => {
    const frame = JSON.parse(event.data);
    note(frame.description || (frame.tool + ' ' + frame.preview));
  });
  source.addEventListener('skill', (event) => {
    note('loading skill: ' + JSON.parse(event.data).skill);
  });
  source.addEventListener('cost', (event) => {
    const frame = JSON.parse(event.data);
    if (!meter) {
      meter = document.createElement('div');
      meter.className = 'meta';
      reply.appendChild(meter);
    }
    meter.textContent = frame.tokens + ' tok · $' + (frame.cost_micro_usd / 1e6).toFixed(6);
    log.scrollTop = log.scrollHeight;
  });
  source.addEventListener('connect', (event) => {
    const link = document.createElement('a');
    link.href = JSON.parse(event.data).url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Connect account';
    reply.appendChild(link);
    log.scrollTop = log.scrollHeight;
  });
  source.addEventListener('connect_error', (event) => {
    note(JSON.parse(event.data).message);
  });
  source.addEventListener('terminal', (event) => {
    const frame = JSON.parse(event.data);
    if (frame.status === 'done') {
      if (frame.text && !streamedText.textContent) streamedText.textContent = frame.text;
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = frame.model + ' · ' + frame.tokens + ' tok · $'
        + (frame.cost_micro_usd / 1e6).toFixed(6);
      reply.appendChild(meta);
      if (frame.question) {
        const question = { turn_id: turnId, ...frame.question };
        state.handoffs.question = question;
        if (selected === agent) renderQuestion(agent, state, question);
      } else if (!answering) {
        state.handoffs.question = null;
      }
    } else {
      streamedText.textContent += (streamedText.textContent ? '\n' : '')
        + (frame.text || '(' + frame.status
           + (frame.error_class ? ': ' + frame.error_class : '') + ')');
      if (!answering) state.handoffs.question = null;
    }
    if (!sawFiles) state.handoffs.files = null;
    record();
    close();
  });
  source.addEventListener('parked', (event) => {
    streamedText.textContent += (streamedText.textContent ? '\n' : '')
      + JSON.parse(event.data).message;
    record();
    close();
  });
  source.onerror = () => {
    stalled += 1;
    if (source.readyState !== EventSource.CLOSED && stalled < MAX_STREAM_RETRIES) return;
    record();
    state.messages.push({ role: 'error', text: 'Connection lost — reload to see the reply.' });
    reply.remove();
    close();
  };
}

async function boot() {
  let res;
  try {
    res = await fetch(BASE + '/api/agents', { credentials: 'same-origin' });
  } catch (err) {
    res = null;
  }
  if (!res || res.status === 401) {
    document.getElementById('token-card').style.display = 'block';
    return;
  }
  if (!res.ok) {
    const failed = document.createElement('div');
    failed.className = 'empty';
    failed.style.gridColumn = '1 / -1';
    failed.textContent = 'Error ' + res.status + ' — reload to retry.';
    document.body.replaceChildren(failed);
    return;
  }
  const payload = await res.json();
  agents = payload.agents;
  nav.hidden = false;
  main.hidden = false;
  member = payload.member;
  wireMemberChrome();
  renderSidebar();
  renderWorkspaceSidebar();
  if (location.hash === '#/admin' && payload.member.admin) {
    showAdmin();
    return;
  }
  const opening = hashState();
  if (opening.workspace) {
    showWorkspace(opening.workspace);
    return;
  }
  select(opening.agent || agents[0], opening.tab);
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeArtifact();
});

boot();
