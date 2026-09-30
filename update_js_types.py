with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

# Replace RESOURCE_EXHAUSTION with MEMORY_SAMPLE
content = content.replace('"event_type": "RESOURCE_EXHAUSTION"', '"event_type": "MEMORY_SAMPLE"')
# Replace DEPENDENCY_FAILURE with REQUEST_TIMEOUT for user-service
content = content.replace(
    '"event_type": "DEPENDENCY_FAILURE", \n        "metadata": {"_raw_event_type": "REQUEST_TIMEOUT"',
    '"event_type": "REQUEST_TIMEOUT", \n        "metadata": {"dependency": "redis-cache-cluster"',
)
# Replace DEPENDENCY_FAILURE with HTTP_ERROR for api-gateway
content = content.replace(
    '"event_type": "DEPENDENCY_FAILURE", \n        "metadata": {"_raw_event_type": "HTTP_ERROR"',
    '"event_type": "HTTP_ERROR", \n        "metadata": {"dependency": "user-service"',
)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
