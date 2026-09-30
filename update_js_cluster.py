with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

# Replace the single user-service 500 with three of them
user_500 = """      {
        "source": "user-service",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 500},
        "description": "Internal Server Error in user-service",
        "timestamp": new Date(t.getTime() + 180000).toISOString()
      },"""

user_500_cluster = """      {
        "source": "user-service",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 500},
        "description": "Internal Server Error in user-service",
        "timestamp": new Date(t.getTime() + 180000).toISOString()
      },
      {
        "source": "user-service",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 500},
        "description": "Internal Server Error in user-service",
        "timestamp": new Date(t.getTime() + 181000).toISOString()
      },
      {
        "source": "user-service",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 500},
        "description": "Internal Server Error in user-service",
        "timestamp": new Date(t.getTime() + 182000).toISOString()
      },"""

content = content.replace(user_500, user_500_cluster)

# Replace the single api-gateway 503 with three of them
gateway_503 = """      {
        "source": "api-gateway",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 503},
        "description": "Gateway returning 503",
        "timestamp": new Date(t.getTime() + 300000).toISOString()
      }"""

gateway_503_cluster = """      {
        "source": "api-gateway",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 503},
        "description": "Gateway returning 503",
        "timestamp": new Date(t.getTime() + 300000).toISOString()
      },
      {
        "source": "api-gateway",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 503},
        "description": "Gateway returning 503",
        "timestamp": new Date(t.getTime() + 301000).toISOString()
      },
      {
        "source": "api-gateway",
        "event_type": "STATE_CHANGE",
        "metadata": {"status_code": 503},
        "description": "Gateway returning 503",
        "timestamp": new Date(t.getTime() + 302000).toISOString()
      }"""

content = content.replace(gateway_503, gateway_503_cluster)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
