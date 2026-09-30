with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

# Disable chat input when opening historical incident
content = content.replace(
    "document.querySelector('.sim-panel').style.display = 'none';",
    "document.querySelector('.sim-panel').style.display = 'none';\n    document.getElementById('chat-input').disabled = true;\n    document.getElementById('chat-input').placeholder = 'Chat is disabled for historical incidents.';\n    document.getElementById('send-btn').disabled = true;",
)

# Re-enable chat input when returning to live
content = content.replace(
    "document.querySelector('.sim-panel').style.display = 'block';",
    "document.querySelector('.sim-panel').style.display = 'block';\n  document.getElementById('chat-input').disabled = false;\n  document.getElementById('chat-input').placeholder = 'Ask the SRE Agent...';\n  document.getElementById('send-btn').disabled = false;",
)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
