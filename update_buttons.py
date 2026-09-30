with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

content = content.replace(
    '<button class="sim-btn cascade" onclick="sendCascade()">Full Cascade x50</button>',
    '<button class="sim-btn cascade" onclick="sendCascade()">Full Cascade x50</button>\n          <button class="sim-btn" onclick="sendConfigDrift()">Config Drift (Real)</button>',
)

js_addition = """
async function sendConfigDrift() {
  document.querySelectorAll('.sim-btn').forEach(b=>b.disabled=true);
  try {
    const res = await fetch(`${API}/api/v1/reset`, {method: 'POST'});
    if (!res.ok) throw new Error('Reset failed');

    // Simulate API calls by calling a script on backend if it existed, but we can just post the JSON directly from frontend!
    // Since we wrote the python script, let's just make the JS post the events over time.
    let t = new Date();
    t = new Date(t.getTime() - 5*60000);

    const events = [
      {
        "source": "redis-cache-cluster",
        "event_type": "STATE_CHANGE",
        "metadata": {"config_key": "maxmemory-policy", "old_value": "allkeys-lru", "new_value": "noeviction"},
        "description": "Config changed: maxmemory-policy to noeviction",
        "timestamp": t.toISOString()
      },
      {
        "source": "redis-cache-cluster",
        "event_type": "RESOURCE_EXHAUSTION",
        "metadata": {"memory_percent": 99.5},
        "description": "Redis memory at 99.5%",
        "timestamp": new Date(t.getTime() + 60000).toISOString()
      },
      {
        "source": "user-service",
        "event_type": "DEPENDENCY_FAILURE",
        "metadata": {"_raw_event_type": "REQUEST_TIMEOUT", "dependency": "redis-cache-cluster"},
        "description": "Timeout connecting to Redis",
        "timestamp": new Date(t.getTime() + 120000).toISOString()
      },
      {
        "source": "user-service",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 500},
        "description": "Internal Server Error in user-service",
        "timestamp": new Date(t.getTime() + 180000).toISOString()
      },
      {
        "source": "api-gateway",
        "event_type": "DEPENDENCY_FAILURE",
        "metadata": {"_raw_event_type": "HTTP_ERROR", "dependency": "user-service"},
        "description": "user-service returning 500",
        "timestamp": new Date(t.getTime() + 240000).toISOString()
      },
      {
        "source": "api-gateway",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 503},
        "description": "Gateway returning 503",
        "timestamp": new Date(t.getTime() + 300000).toISOString()
      }
    ];

    for (const e of events) {
      await fetch(`${API}/api/v1/events`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(e)
      });
      await new Promise(r => setTimeout(r, 500));
    }
  } catch(e) { console.error(e); }
  document.querySelectorAll('.sim-btn').forEach(b=>b.disabled=false);
}
"""

content = content.replace(
    "async function sendCascade() {", js_addition + "\nasync function sendCascade() {"
)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
