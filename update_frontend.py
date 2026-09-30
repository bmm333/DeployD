import re

with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

# 1. Add return-live-btn to header
content = content.replace(
    '<button class="btn btn-danger" style="margin-left:16px;" onclick="resetState()">Close Incident</button>',
    '<button class="btn btn-primary" id="return-live-btn" style="margin-left:16px; display:none;" onclick="returnToLive()">Return to Live</button>\n    <button class="btn btn-danger" id="close-incident-btn" style="margin-left:8px;" onclick="resetState()">Close Incident</button>',
)

# 2. Remove Modal HTML
content = re.sub(
    r"<!-- INCIDENT DETAIL MODAL -->.*?</div>\s*</div>\s*</div>", "", content, flags=re.DOTALL
)

# 3. Remove Modal CSS
content = re.sub(
    r"/\* MODAL \*/.*?/\* SCROLLBARS \*/", "/* SCROLLBARS */", content, flags=re.DOTALL
)

# 4. Update vis-network layout
content = content.replace(
    "layout: { improvedLayout: false }",
    "layout: { hierarchical: { enabled: true, direction: 'UD', sortMethod: 'hubsize' } }",
)

# 5. Update openIncident and remove showModal/closeModal
js_replacement = """
async function openIncident(id) {
  try {
    const res = await fetch(`${API}/api/v1/incidents/${id}`);
    if (!res.ok) return;
    const inc = await res.json();

    clearInterval(pollStateInterval);
    document.getElementById('return-live-btn').style.display = 'block';
    document.getElementById('close-incident-btn').style.display = 'none';
    document.querySelector('.sim-panel').style.display = 'none';

    updateStatus('Historical');
    updateGraph(inc.graph_snapshot.nodes || [], inc.graph_snapshot.edges || []);

    // reset chat and update it
    document.getElementById('chat-messages').innerHTML = '';
    chatHistory = [];
    updateChat(inc.chat_history || []);
  } catch(_) {}
}

function returnToLive() {
  document.getElementById('return-live-btn').style.display = 'none';
  document.getElementById('close-incident-btn').style.display = 'block';
  document.querySelector('.sim-panel').style.display = 'block';
  pollStateInterval = setInterval(poll, 2000);
  poll();
}

// ── START ──────────────────────────────────────────────────────────────────────
poll();
pollIncidents();
let pollStateInterval = setInterval(poll, 2000);
setInterval(pollIncidents, 8000);
"""

content = re.sub(
    r"async function openIncident\(id\).*?setInterval\(pollIncidents, 8000\);",
    js_replacement,
    content,
    flags=re.DOTALL,
)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
