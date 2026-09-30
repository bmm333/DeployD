with open("/home/m3b/DeployD/frontend/index.html") as f:
    content = f.read()

content = content.replace(
    "t = new Date(t.getTime() - 5*60000);", "t = new Date(t.getTime() - 240000);"
)

with open("/home/m3b/DeployD/frontend/index.html", "w") as f:
    f.write(content)
